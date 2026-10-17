// ============================================================================
//  动漫下载机 · 桌面外壳（System WebView 方案）
//
//  不使用打包的浏览器内核，而是调用 Windows 自带的 **WebView2** 运行时
//  （Win10/11 默认安装，本机实测 155.0.4283.60）。
//  这正是 Tauri / Wails / Neutralino 一类框架的思路：省掉整个 Chromium。
//
//  职责只有一个：把 Python 后端拉起来，再用 WebView2 显示它的界面。
//    1. 分配一个空闲端口
//    2. 启动 backend\动漫下载机.exe --headless --port <port>
//    3. 轮询 http://127.0.0.1:<port>/api/meta 直到就绪
//    4. 开窗口、加载页面
//    5. 窗口关闭 → 结束后端进程树（连子进程一起）
//
//  为什么 WebView2 的 UserDataFolder 必须显式指定：
//    默认位置是 exe 同级目录，而程序常装在 Program Files 之类只读位置，
//    首次创建缓存就会失败。所以固定放到 %LOCALAPPDATA%\AnimeDownloader\webview2。
// ============================================================================

using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AnimeDownloader.Web;

internal static class Program
{
    private const string AppName = "AnimeDownloader";
    private const string WindowTitle = "动漫下载机 Anime Downloader";

    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();

        // ------------------------------------------------------------ 单实例保护
        // 双击已经开着的程序时，如果放任它起第二个实例：
        //   · 两个进程连同一个 SQLite，任务会被重复执行；
        //   · 抢同一个日志文件，直接报
        //     "The process cannot access the file ...backend.log"（实际踩过）。
        // 所以这里用命名互斥体拦住。用 Local\ 而不是 Global\：
        // 同一台机器上不同用户各自可以开一个，互不干扰。
        var mutexName = @"Local\AnimeDownloader.SingleInstance";
        Mutex? instanceMutex = null;
        bool isFirstInstance = true;
        try
        {
            instanceMutex = new Mutex(initiallyOwned: true, mutexName, out isFirstInstance);
        }
        catch
        {
            // 互斥体都建不出来就别拦了，正常启动
            isFirstInstance = true;
        }

        if (!isFirstInstance)
        {
            MessageBox.Show(
                "动漫下载机已经在运行了。\n\n"
                + "请到任务栏单击「动漫下载机」窗口；如果要重新启动，\n"
                + "先关掉现有窗口（或结束「AnimeDownloader.exe」进程）再试。",
                WindowTitle, MessageBoxButtons.OK, MessageBoxIcon.Information);
            instanceMutex?.Dispose();
            return 0;
        }

        try
        {
            return RunApp();
        }
        finally
        {
            try { instanceMutex?.ReleaseMutex(); } catch { /* 非持有者释放会抛，忽略 */ }
            instanceMutex?.Dispose();
        }
    }

    private static int RunApp()
    {
        var backend = ResolveBackend();
        if (backend is null)
        {
            Fatal("找不到后端程序。\n\n期望位置：\n  <本程序目录>\\backend\\动漫下载机.exe\n  <本程序目录>\\动漫下载机.exe\n\n请重新安装。");
            return 1;
        }

        var port = FindFreePort();
        // 日志按端口命名：两个实例同时跑时不会抢同一个文件（曾经因此
        // "The process cannot access the file ...backend.log because it is
        // being used by another process" 直接启动失败）。
        var stdoutLog = Path.Combine(LogDir, $"backend-{port}.log");
        Process? proc;
        try
        {
            Directory.CreateDirectory(LogDir);
            HostLog($"后端: {backend}");
            HostLog($"端口: {port}");
            HostLog($"日志: {stdoutLog}");
            // 每次启动前清空本轮日志，避免历史内容干扰排查（真正的 app.log 有轮转）
            try { File.Delete(stdoutLog); } catch { /* 正被占用就算了 */ }
            proc = StartBackend(backend, port, stdoutLog);
            HostLog($"后端已启动 PID={proc.Id}");
        }
        catch (Exception ex)
        {
            HostLog($"启动后端失败: {ex}");
            Fatal($"启动后端失败：\n{ex.Message}");
            return 1;
        }

        try
        {
            if (!WaitReady(port, TimeSpan.FromSeconds(90), proc))
            {
                var reason = proc.HasExited
                    ? $"后端进程已退出（退出码 {SafeExitCode(proc)}）"
                    : "后端 90 秒内没有就绪";
                HostLog(reason);
                Fatal($"{reason}。\n\n"
                      + $"后端日志：{stdoutLog}\n"
                      + $"数据日志：{Path.Combine(DataDir, "logs", "app.log")}\n"
                      + $"崩溃日志：{Path.Combine(DataDir, "logs", "crash.log")}");
                return 1;
            }
            HostLog("后端已就绪，打开窗口");

            Application.Run(new MainForm($"http://127.0.0.1:{port}/"));
            return 0;
        }
        finally
        {
            HostLog("窗口已关闭，结束后端");
            KillTree(proc);
        }
    }

    private static string SafeExitCode(Process proc)
    {
        try { return proc.ExitCode.ToString(); } catch { return "未知"; }
    }

    /// <summary>宿主自己的步骤日志，出问题时至少能看到走到哪一步。</summary>
    private static void HostLog(string message)
    {
        try
        {
            Directory.CreateDirectory(LogDir);
            File.AppendAllText(
                Path.Combine(LogDir, "host.log"),
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}{Environment.NewLine}",
                Encoding.UTF8);
        }
        catch
        {
            // 日志写不了也不能影响主流程
        }
    }

    // ------------------------------------------------------------------ 路径

    private static string? _dataDir;

    /// <summary>
    /// 数据目录，**按可写性逐级回退**：
    ///   AD_DATA_DIR → %LOCALAPPDATA% → %APPDATA% → %TEMP% → exe 同级 data\
    ///
    /// 为什么不能只用 %LOCALAPPDATA%：
    ///   受限账户、企业策略、漫游配置、或者被安全软件"受控文件夹访问"锁住时，
    ///   那里根本建不出目录。早先这里直接 Directory.CreateDirectory 就抛，
    ///   宿主立刻 Fatal 退出 —— 用户只看到"打不开"，日志也写不出来。
    ///   后端（Python）一直有多级回退，宿主这边没跟上，这里补齐。
    /// </summary>
    private static string DataDir
    {
        get
        {
            if (_dataDir is not null) return _dataDir;

            var overridden = Environment.GetEnvironmentVariable("AD_DATA_DIR");
            if (!string.IsNullOrWhiteSpace(overridden) && TryEnsureWritable(overridden))
            {
                _dataDir = overridden;
                return _dataDir;
            }

            var candidates = new List<string>();
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            var temp = Path.GetTempPath();
            if (!string.IsNullOrWhiteSpace(local))
                candidates.Add(Path.Combine(local, AppName, "data"));
            if (!string.IsNullOrWhiteSpace(roaming))
                candidates.Add(Path.Combine(roaming, AppName, "data"));
            if (!string.IsNullOrWhiteSpace(temp))
                candidates.Add(Path.Combine(temp, AppName, "data"));
            candidates.Add(Path.Combine(AppContext.BaseDirectory, "data"));

            foreach (var candidate in candidates)
            {
                if (TryEnsureWritable(candidate))
                {
                    _dataDir = candidate;
                    return _dataDir;
                }
            }
            // 全都不可写（几乎不可能）—— 仍然返回第一个，让后面的错误信息更有指向性
            _dataDir = candidates[0];
            return _dataDir;
        }
    }

    /// <summary>目录能否创建**并真正写入**（只"能创建"不算数）。</summary>
    private static bool TryEnsureWritable(string dir)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(dir, "logs"));
            var probe = Path.Combine(dir, ".write-probe");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static string LogDir => Path.Combine(DataDir, "logs");

    /// <summary>
    /// WebView2 的缓存/配置目录，**跟随已解析出的 DataDir**（放在它旁边）。
    ///
    /// 不能再用 %LOCALAPPDATA%：DataDir 可能已经回退到 %TEMP% 或程序目录，
    /// 这时 WebView2 若还去 %LOCALAPPDATA% 建 profile 就会失败 ——
    /// 表现为窗口闪一下就关（走了 OnLoad 里的失败分支）。
    /// 这个坑实测踩过一次。
    /// </summary>
    internal static string WebViewDataDir
    {
        get
        {
            var parent = Path.GetDirectoryName(DataDir.TrimEnd('\\', '/'));
            if (!string.IsNullOrWhiteSpace(parent))
                return Path.Combine(parent, "webview2");
            return Path.Combine(DataDir, "webview2");
        }
    }

    /// <summary>后端 exe：优先 backend\ 子目录，其次与本程序同级。</summary>
    private static string? ResolveBackend()
    {
        var baseDir = AppContext.BaseDirectory;
        var candidates = new[]
        {
            Path.Combine(baseDir, "backend", "动漫下载机.exe"),
            Path.Combine(baseDir, "动漫下载机.exe"),
        };
        return candidates.FirstOrDefault(File.Exists);
    }

    // ------------------------------------------------------------------ 进程

    private static Process StartBackend(string exe, int port, string logFile)
    {
        var psi = new ProcessStartInfo(exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Path.GetDirectoryName(exe)!,
            // 让后端只起服务、不开窗口
            Arguments = $"--headless --port {port}",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        // 数据目录由宿主定好后**显式传给后端**：避免两边各猜一套回退顺序，
        // 结果一个落在 %LOCALAPPDATA%、一个落在 %TEMP%，用户找不到自己的设置。
        psi.Environment["AD_DATA_DIR"] = DataDir;
        psi.Environment["AD_LOG_DIR"] = Path.Combine(DataDir, "logs");
        psi.Environment["AD_DESKTOP_HOST"] = "1";

        var proc = Process.Start(psi) ?? throw new InvalidOperationException("进程未启动");

        // 把后端输出落到日志文件。
        // 不重定向的话，后端（console=False 打包）的输出就彻底丢了 ——
        // 出问题时只能看到一个"打不开"的窗口，完全没法排查。
        //
        // 共享模式必须显式给 FileShare.ReadWrite：
        // StreamWriter(path, append) 默认只允许别的进程「读」，一旦另一个实例
        // 或上一轮残留还开着这个文件，构造就会抛
        // "being used by another process"。这里放宽共享，写失败也不致命。
        StreamWriter? writer = null;
        try
        {
            var fs = new FileStream(logFile, FileMode.Append, FileAccess.Write,
                                    FileShare.ReadWrite | FileShare.Delete);
            writer = new StreamWriter(fs, Encoding.UTF8) { AutoFlush = true };
        }
        catch
        {
            // 日志打不开不能拦住程序启动
        }

        void Pump(string? line)
        {
            if (line is null || writer is null) return;
            try { writer.WriteLine(line); } catch { /* 关窗阶段的写入失败忽略 */ }
        }
        proc.OutputDataReceived += (_, e) => Pump(e.Data);
        proc.ErrorDataReceived += (_, e) => Pump(e.Data);
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        return proc;
    }

    private static int FindFreePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private static bool WaitReady(int port, TimeSpan timeout, Process proc)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        var deadline = DateTime.UtcNow + timeout;
        var url = $"http://127.0.0.1:{port}/api/meta";
        while (DateTime.UtcNow < deadline)
        {
            if (proc.HasExited) return false;      // 后端自己挂了，别傻等
            try
            {
                var text = client.GetStringAsync(url).GetAwaiter().GetResult();
                if (text.Contains("\"data\"")) return true;
            }
            catch
            {
                // 还没起来
            }
            Thread.Sleep(400);
        }
        return false;
    }

    private static void KillTree(Process? proc)
    {
        if (proc is null) return;
        try
        {
            if (!proc.HasExited)
            {
                // 后端会派生子进程（ffmpeg 等），整棵树一起收
                proc.Kill(entireProcessTree: true);
                proc.WaitForExit(5000);
            }
        }
        catch
        {
            // 退出阶段的异常不用打扰用户
        }
        finally
        {
            proc.Dispose();
        }
    }

    private static void Fatal(string message)
    {
        MessageBox.Show(message, WindowTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

/// <summary>承载 WebView2 的主窗口。</summary>
internal sealed class MainForm : Form
{
    private readonly string _url;
    private readonly WebView2 _webView = new();

    public MainForm(string url)
    {
        _url = url;
        Text = "动漫下载机 Anime Downloader";
        Width = 1320;
        Height = 880;
        MinimumSize = new Size(1024, 700);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = LoadAppIcon();

        _webView.Dock = DockStyle.Fill;
        Controls.Add(_webView);
        Load += OnLoad;
    }

    /// <summary>
    /// 兜底：万一窗口落在所有屏幕之外，把它拉回主屏。
    ///
    /// WinForms 的 CenterScreen 会按"当前活动屏"居中（实测多显示器下它选了
    /// 坐标 X=-1920 的副屏 —— 对用户没问题，窗口就在他看的那个屏上）。
    /// 但如果显示器数量/布局变过（拔掉副屏、改分辨率、远程桌面），窗口可能
    /// 整个跑到虚拟桌面之外，用户只会以为"程序没打开"。
    /// 这里只在这种情况下纠正，正常情况绝不动窗口位置。
    /// </summary>
    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        try
        {
            if (Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(Bounds)))
                return;

            var area = Screen.PrimaryScreen?.WorkingArea
                       ?? Screen.AllScreens[0].WorkingArea;
            Location = new Point(
                area.Left + Math.Max(0, (area.Width - Width) / 2),
                area.Top + Math.Max(0, (area.Height - Height) / 2));
        }
        catch
        {
            // 位置纠正失败不影响使用
        }
    }

    /// <summary>
    /// 窗口图标直接取 exe 自己嵌入的图标（csproj 的 ApplicationIcon）。
    /// 这样任务栏、Alt+Tab、窗口左上角、快捷方式全部一致，
    /// 不用再单独维护一份资源。
    /// </summary>
    private static Icon LoadAppIcon()
    {
        try
        {
            var exe = Environment.ProcessPath ?? Application.ExecutablePath;
            var extracted = Icon.ExtractAssociatedIcon(exe);
            if (extracted is not null) return extracted;
        }
        catch
        {
            // 取不到就用系统默认图标，不影响功能
        }
        return SystemIcons.Application;
    }

    private async void OnLoad(object? sender, EventArgs e)
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: Program.WebViewDataDir);

            await _webView.EnsureCoreWebView2Async(env);

            var settings = _webView.CoreWebView2.Settings;
            settings.AreDefaultContextMenusEnabled = true;
            settings.AreDevToolsEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.IsZoomControlEnabled = true;

            // 站点经常用 target=_blank 打开外链，交给系统浏览器，别在应用窗口里开
            _webView.CoreWebView2.NewWindowRequested += (_, ev) =>
            {
                ev.Handled = true;
                try
                {
                    Process.Start(new ProcessStartInfo(ev.Uri) { UseShellExecute = true });
                }
                catch
                {
                    // 打不开就算了
                }
            };

            _webView.CoreWebView2.Navigate(_url);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "WebView2 初始化失败。\n\n"
                + "Windows 10/11 一般自带 WebView2 运行时；若被精简过，请安装：\n"
                + "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                + ex.Message,
                "动漫下载机 Anime Downloader",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }
}
