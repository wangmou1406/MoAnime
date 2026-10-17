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

        var backend = ResolveBackend();
        if (backend is null)
        {
            Fatal("找不到后端程序。\n\n期望位置：\n  <本程序目录>\\backend\\动漫下载机.exe\n  <本程序目录>\\动漫下载机.exe\n\n请重新安装。");
            return 1;
        }

        var port = FindFreePort();
        var stdoutLog = Path.Combine(LogDir, "backend.log");
        Process? proc;
        try
        {
            Directory.CreateDirectory(LogDir);
            HostLog($"后端: {backend}");
            HostLog($"端口: {port}");
            HostLog($"日志: {stdoutLog}");
            // 每次启动前清空后端日志，避免历史内容干扰排查（真正的 app.log 有轮转）
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

    /// <summary>
    /// 数据目录：默认 %LOCALAPPDATA%\AnimeDownloader\data。
    /// 允许用 AD_DATA_DIR 覆盖 —— 便携部署、或者把数据放到别的盘时用得上，
    /// 也方便自动化测试把目录指到工作区内。
    /// </summary>
    private static string DataDir
    {
        get
        {
            var overridden = Environment.GetEnvironmentVariable("AD_DATA_DIR");
            if (!string.IsNullOrWhiteSpace(overridden)) return overridden;
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                AppName, "data");
        }
    }

    private static string LogDir => Path.Combine(DataDir, "logs");

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
        // 数据目录交给后端自己决定（它有多级回退），这里不强行覆盖
        psi.Environment["AD_DESKTOP_HOST"] = "1";

        var proc = Process.Start(psi) ?? throw new InvalidOperationException("进程未启动");

        // 把后端输出落到日志文件。
        // 不重定向的话，后端（console=False 打包）的输出就彻底丢了 ——
        // 出问题时只能看到一个"打不开"的窗口，完全没法排查。
        var writer = new StreamWriter(logFile, append: true, Encoding.UTF8) { AutoFlush = true };
        void Pump(string? line)
        {
            if (line is null) return;
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
                userDataFolder: ProgramPaths.WebViewDataDir);

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

internal static class ProgramPaths
{
    /// <summary>WebView2 的缓存/配置目录。跟随数据目录，卸载时可一并清理。</summary>
    public static string WebViewDataDir
    {
        get
        {
            var dataDir = Environment.GetEnvironmentVariable("AD_DATA_DIR");
            if (!string.IsNullOrWhiteSpace(dataDir))
            {
                var parent = Path.GetDirectoryName(dataDir.TrimEnd('\\', '/'));
                if (!string.IsNullOrWhiteSpace(parent))
                    return Path.Combine(parent, "webview2");
            }
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AnimeDownloader", "webview2");
        }
    }
}
