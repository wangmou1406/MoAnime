/* ============================================================
   动漫下载机 · 设置与维护：配置读写、统计、日志、浏览器取流状态、恢复出厂、目录选择
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
async function loadSettings() {
  const resp = await api('/api/settings');
  if (!resp.ok) { toastErr('加载设置失败：' + resp.error); return; }
  state.settings = resp.data.settings || {};
  state.schemes = resp.data.schemes || state.schemes;
  views.settings(state.settings, state.schemes);
  // 必须在表单渲染之后绑定外观控件：
  // 以前放在启动时的 installEvents() 里，那一刻 #settings-form 还是空的，
  // 导致背景图拖拽/点选、滑块预演等全部绑不上（按钮看着在、点了没反应）
  bindAppearanceControls();
  applyTheme(state.settings.theme || 'light');
  applyAppearance(state.settings);
  loadLanInfo();
}

/* ---------------- 远程访问 ---------------- */

async function loadLanInfo() {
  const info = $('#lan-info');
  const status = $('#lan-status');
  if (!info || !status) return;

  const resp = await api('/api/system/network');
  if (!resp.ok) {
    info.textContent = '读取失败：' + resp.error;
    status.textContent = '读取失败';
    return;
  }
  const data = resp.data;
  state.lan = data;

  status.textContent = data.lan_enabled ? '已开放局域网' : '仅本机';
  status.className = 'badge ' + (data.lan_enabled ? 'badge-ok' : 'badge-dim');

  const rows = [];
  rows.push(`<div>本机：<code>${esc(data.local_url)}</code></div>`);
  if (!data.lan_enabled) {
    rows.push('<div class="mt-2">当前只监听 <code>127.0.0.1</code>，'
      + '别的电脑连不上。点下面的「允许局域网访问」后<b>重启服务</b>生效。</div>');
  } else if (data.addresses && data.addresses.length) {
    rows.push('<div class="mt-2">局域网地址（在另一台电脑的浏览器里打开）：</div>');
    data.addresses.forEach((item, index) => {
      const shown = data.token_enabled ? `${item.url}?token=…` : item.url;
      const badge = item.primary
        ? '<span class="badge badge-ok" style="flex:0 0 auto">推荐</span>'
        : '<span class="badge badge-dim" style="flex:0 0 auto">其它网卡</span>';
      rows.push(`<div class="d-flex align-items-center gap-2 mt-1">
        ${badge}
        <code id="lan-url-${index}">${esc(shown)}</code>
        <button class="btn btn-outline-light btn-sm btn-icon" data-copy="${esc(item.url)}"
                title="复制地址"><i class="bi bi-clipboard"></i></button>
      </div>`);
    });
    if (data.token_enabled) {
      rows.push('<div class="mt-2">已开启访问令牌：另一台电脑第一次打开时会要求输入，'
        + '令牌就是下面「访问令牌」里填的那串。</div>');
    } else {
      rows.push('<div class="mt-2 warn-line">⚠ 没有设置访问令牌 —— 同一网络里的任何设备'
        + '都能打开并操作（包括删文件）。建议在上面「访问令牌」里填一串随机字符。</div>');
    }
    const first = data.addresses[0];
    if (first) {
      rows.push(`<div class="mt-2">同一网络内，用手机也能打开：<code>${esc(first.url)}</code></div>`);
    }
  } else {
    rows.push('<div class="mt-2">已开放，但没枚举到局域网地址 —— 请确认已连上网络，'
      + '或在「监听地址」里直接填本机 IP。</div>');
  }
  if (data.platform === 'nt' && data.lan_enabled) {
    rows.push('<div class="mt-2">Windows 首次开放时可能需要在防火墙弹窗里点「允许」，'
      + '否则其它设备仍然连不上。</div>');
  }
  info.innerHTML = rows.join('');
}

function setListenHost(host) {
  const input = $('#set-web_host');
  if (!input) { toastErr('找不到监听地址字段'); return; }
  input.value = host;
  toastOk(`已把监听地址设为 ${host}，保存设置并重启服务后生效`);
}

function setCount(id, value) {
  const node = $(id);
  if (!node) return;
  const next = String(value);
  if (node.textContent === next) return;
  node.textContent = next;
  if (!node.classList.contains('badge')) return;
  node.classList.remove('bump');
  // 强制回流，保证同一个元素连续变化时动画能重放
  void node.offsetWidth;
  node.classList.add('bump');
}

async function refreshStats() {
  const resp = await api('/api/stats');
  if (!resp.ok) return;
  const data = resp.data;
  state.stats = data.tasks || {};
  const active = (state.stats.running || 0) + (state.stats.queued || 0);
  const paused = state.stats.paused || 0;

  $('#foot-stats').textContent = `源 ${data.sources_enabled}/${data.sources_total} · 任务 ${active}`;
  setCount('#nav-task-count', active + paused);
  setFfmpegState(data.ffmpeg);

  const tick = (id, value) => { const node = $(id); if (node) node.textContent = value; };
  tick('#tick-sources', `${data.sources_enabled}/${data.sources_total}`);
  tick('#tick-tasks', active + paused);
  const speedNode = $('#tick-speed');
  if (speedNode) {
    // 有任务在跑但还没测出速度时给骨架脉冲，不要长时间顶着 "—" 像卡死
    if (!state.stats.speed && active) speedNode.innerHTML = '<span class="skeleton"></span>';
    else speedNode.textContent = fmtSpeed(state.stats.speed);
  }
  const meta = $('#tick-meta');
  if (meta && state.meta) {
    meta.textContent = `v${state.meta.version} · python ${state.meta.python} · ${state.meta.platform}`;
  }
  const live = $('#tick-live');
  if (live) {
    live.textContent = active ? '● DOWNLOADING' : '● STANDBY';
    live.style.color = active ? 'var(--primary)' : '';
    // 呼吸动画只在真的在下载时开，空闲时保持静止
    live.classList.toggle('is-busy', active > 0);
  }
  const dot = $('#foot-dot');
  if (dot) dot.classList.toggle('warn', !!state.stats.failed);
}

/* ============================================================
   事件绑定
   ============================================================ */

async function factoryReset() {
  const warn = [
    '将清除以下用户配置：',
    '  · 全部设置项（下载目录、并发、外观等回到默认）',
    '  · 全部任务记录（任务列表清空）',
    '  · 全部下载历史与检索历史',
    '  · 自定义背景图、预览图缓存',
    '  · 源的「含广告」标记（源本身保留）',
    '',
    '不会删除已经下载好的视频文件。',
    '',
    '确定继续吗？',
  ].join('\n');
  if (!confirm(warn)) return;
  // 二次确认：把 RESET 这个口令交给后端，防止误触
  const typed = prompt('这是不可撤销的操作。\n请输入 RESET 以确认执行：', '');
  if (typed === null) return;
  if (typed.trim().toUpperCase() !== 'RESET') { toastWarn('输入不匹配，已取消'); return; }

  const job = jobs.start('清除用户配置', { total: 1, message: '正在重置…' });
  const resp = await api('/api/settings/factory-reset', 'POST', { confirm: 'RESET' });
  if (!resp.ok) { jobs.fail(job, resp.error); return; }

  const removed = resp.data.removed || {};
  const parts = [];
  if (removed.tasks) parts.push(`任务 ${removed.tasks}`);
  if (removed.history) parts.push(`历史 ${removed.history}`);
  if (removed.search_history) parts.push(`检索历史 ${removed.search_history}`);
  if (removed.backgrounds) parts.push(`背景图 ${removed.backgrounds}`);
  if (removed.thumbnails) parts.push(`预览图 ${removed.thumbnails}`);
  jobs.finish(job, parts.length ? '已清除 ' + parts.join(' / ') : '配置已重置');

  // 前端也要跟着清干净：外观缓存与内存状态
  try {
    localStorage.removeItem('anime-downloader-appearance');
    localStorage.removeItem('moanime-appearance');
  } catch (e) { /* 忽略 */ }
  state.settings = resp.data.settings || {};
  closeEpisodePanel();

  const root = document.documentElement;
  root.classList.remove('has-bg');
  ['--app-bg-image', '--app-bg-blur', '--app-bg-dim', '--app-title-color',
    // 自定义配色也要一起清掉，否则恢复出厂后颜色和文字色还是旧的
    '--primary', '--primary-dark', '--primary-soft',
    '--bg', '--panel', '--panel-2',
    '--fg', '--fg-dim', '--fg-mute', '--line', '--line-strong', '--hover']
    .forEach((name) => root.style.removeProperty(name));
  root.style.setProperty('--panel-alpha', '100%');
  root.style.setProperty('--panel-hover-alpha', '92%');
  root.dataset.anim = 'full';
  root.dataset.customPalette = '0';

  views.settings(state.settings, state.schemes);
  bindAppearanceControls();
  applyTheme(state.settings.theme || 'light');
  applyAppearance(state.settings);
  await refreshStats();
  await loadSearchHistory();
  toastOk('用户配置已清除，界面已恢复默认');
}

/* ---------------------------------------------------------- 外观设置交互 */

async function loadLibrary(showJob) {
  const job = showJob ? jobs.start('扫描媒体库', { total: 1, message: '正在读取下载目录…' }) : null;
  const resp = await api('/api/library');
  if (!resp.ok) {
    if (job) jobs.fail(job, resp.error);
    else toastErr('加载媒体库失败：' + resp.error);
    return;
  }
  views.library(resp.data);
  if (job) {
    const count = (resp.data.items || []).length;
    jobs.finish(job, `${count} 部 · ${fmtSize(resp.data.total_size)}`);
  }
}

/** 浏览器取流能力状态（设置页显示）。 */

async function loadBrowserResolverStatus() {
  const box = $('#browser-resolver-status');
  const detail = $('#browser-resolver-detail');
  if (!box) return null;
  const resp = await api('/api/system/browser-resolver');
  if (!resp.ok) {
    box.className = 'badge badge-dim';
    box.textContent = '检测失败';
    return null;
  }
  const d = resp.data || {};
  box.className = 'badge ' + (d.usable ? 'badge-soft' : 'badge-dim');
  box.textContent = d.usable
    ? (d.builtin_browser ? '已就绪 · 内置内核' : '已就绪')
    : (!d.enabled ? '已在设置中关闭' : '不可用');
  if (detail) {
    if (d.usable) {
      const parts = [
        d.builtin_browser ? '内置 Chromium' : d.browser,
        d.builtin_node ? '内置 Node' : d.node,
      ];
      detail.textContent = parts.join(' · ')
        + ' · 缓存 ' + (d.cache || 0) + ' 条';
      detail.title = '浏览器：' + d.browser + '\nNode：' + d.node;
    } else {
      detail.textContent = d.enabled
        ? (d.reason || '环境不满足')
        : '打开上方开关即可启用';
    }
  }
  return d;
}

async function saveSettings() {
  const payload = views.collectSettings();
  if (payload.concurrent_tasks !== undefined && payload.concurrent_tasks < 1) payload.concurrent_tasks = 1;
  // 配色方案：套用预设时把 4 个颜色一起写进去（存的是具体颜色，
  // 这样以后调整预设也不会改变用户已保存的外观）
  const preset = (typeof THEME_PALETTES !== 'undefined' ? THEME_PALETTES : [])
    .find((p) => p.key === payload.palette);
  if (preset) {
    payload.accent_color = preset.accent_color || '';
    payload.bg_color = preset.bg_color || '';
    payload.panel_color = preset.panel_color || '';
    payload.panel_2_color = preset.panel_2_color || '';
  }
  const resp = await api('/api/settings', 'POST', payload);
  if (!resp.ok) { toastErr(resp.error); return; }
  state.settings = resp.data.settings;
  applyTheme(state.settings.theme);
  applyAppearance(state.settings);
  toastOk('设置已保存');
  await refreshStats();
}

async function refreshLogs() {
  const level = $('#log-level').value;
  const resp = await api(`/api/logs?limit=400&level=${encodeURIComponent(level)}`);
  if (!resp.ok) return;
  views.renderLogs(resp.data.logs || []);
}

async function openDownloadDir() {
  const resp = await api('/api/fs/open', 'POST', { path: state.settings.download_dir });
  if (!resp.ok) toastErr(resp.error);
}

/* ---------------------------------------------------------- 文件夹浏览 */

async function openFolderPicker(path) {
  showModal('modal-folder');
  await browseFolder(path || state.settings.download_dir || '');
}

async function browseFolder(path) {
  const resp = await api(`/api/fs/list?path=${encodeURIComponent(path || '')}`);
  if (!resp.ok) { toastErr(resp.error); return; }
  views.folderList(resp.data);
}

/* ---------------------------------------------------------- 本地文件预览
   （在线预览见上面的 openPlayer；这两个函数曾经同名，后者会把前者覆盖掉） */
