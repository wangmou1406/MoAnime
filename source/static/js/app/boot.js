/* ============================================================
   动漫下载机 · 启动与骨架：全局 state、boot() 初始化流程、视图切换、Socket 连接
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
const state = {
  meta: null,
  sources: [],
  sourceTypes: [],
  schemes: {},
  settings: {},
  search: null,
  lastKeyword: null,
  currentAnime: null,
  lastSlot: null,
  episodes: [],
  editingSourceId: null,
  sortable: null,
  folderPickTarget: 'download_dir',
  tasks: [],
  stats: {},
  lastFinished: {},
  autoScroll: true,
  taskFilter: 'all',
  collapsedGroups: {},
  lastSearchMethod: '',
  sourceSortMode: 'manual',
  // 检索结果里用户为某部番显式选过的源：{ 番剧名: source_id }
  pickedSource: {},
};

/* ============================================================
   初始化
   ============================================================ */
/* ============================================================
   免责声明（首次使用必须同意）
   ------------------------------------------------------------
   未同意时不加载任何功能：检索、下载、源管理全都不可用。
   同意状态存在设置里（disclaimer_accepted / disclaimer_version），
   条款版本提高后会自动要求重新确认。
   ============================================================ */

async function ensureDisclaimer() {
  let resp;
  try {
    resp = await api('/api/disclaimer');
  } catch (err) {
    return true;                       // 接口异常不该把程序锁死
  }
  if (!resp || !resp.ok) return true;   // 同上
  if (resp.data && resp.data.accepted) return true;

  const check = $('#disclaimer-agree');
  const accept = $('#btn-disclaimer-accept');
  const exit = $('#btn-disclaimer-exit');
  if (!check || !accept || !exit) return true;   // 模板缺失时放行

  check.checked = false;
  accept.disabled = true;
  check.onchange = () => { accept.disabled = !check.checked; };

  return new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };

    accept.onclick = async () => {
      accept.disabled = true;
      const r = await api('/api/disclaimer', 'POST', { accept: true });
      if (!r.ok) {
        toastErr('记录同意状态失败：' + (r.error || '未知错误'));
        accept.disabled = false;
        return;
      }
      hideModal('modal-disclaimer');
      finish(true);
    };

    exit.onclick = () => {
      // 浏览器只允许脚本关闭「由脚本打开」的窗口，多数情况关不掉，
      // 所以关不掉就换成一块挡住整页的提示
      try { window.open('', '_self'); window.close(); } catch (err) { /* 忽略 */ }
      setTimeout(() => {
        showExitedScreen();
        finish(false);
      }, 300);
    };

    showModal('modal-disclaimer');
  });
}

/** 用户选择「不同意」后挡屏。 */

function showExitedScreen() {
  hideModal('modal-disclaimer');
  document.querySelectorAll('.modal-backdrop').forEach((n) => n.remove());
  document.body.classList.remove('modal-open');
  if (document.getElementById('disclaimer-exited')) return;
  const wrap = document.createElement('div');
  wrap.id = 'disclaimer-exited';
  wrap.className = 'disclaimer-exited';
  wrap.innerHTML = `
    <div class="disclaimer-exited-card">
      <i class="bi bi-slash-circle"></i>
      <h4>已停止</h4>
      <p>你未同意使用条款，程序不会运行。</p>
      <p class="text-dim" style="font-size:12.5px">
        如果这是误操作，点下面的按钮重新阅读条款。</p>
      <button class="btn btn-outline-light btn-sm" onclick="location.reload()">
        <i class="bi bi-arrow-clockwise"></i> 重新载入</button>
    </div>`;
  document.body.appendChild(wrap);
}

async function boot() {
  installNav();
  installEvents();

  const meta = await api('/api/meta');
  if (!meta.ok) {
    toastErr('无法连接后端服务：' + (meta.error || '未知错误'));
    return;
  }
  state.meta = meta.data;
  state.sourceTypes = meta.data.source_types || [];
  state.schemes = meta.data.naming_schemes || {};
  setFfmpegState(meta.data.ffmpeg);
  setFrontendVersion(meta.data.version);

  // 首次使用：必须同意免责声明，否则不加载任何功能
  const agreed = await ensureDisclaimer();
  if (!agreed) return;

  fillSourceTypeSelect();
  await loadSettings();
  await loadSources();
  await loadSearchHistory();
  await refreshTasks();
  await refreshLogs();
  await refreshStats();
  views.ticker(currentView());
  connectSocket();
  setInterval(refreshStats, 6000);
  setInterval(() => { if (currentView() === 'tasks') refreshTasks(true); }, 2500);

  // 只有显式带 ?q=关键字 / ?keyword=关键字 时才自动检索（便于分享/收藏）。
  // 不带参数时**不预填**任何关键字：输入框保持空白，检索历史用下面的快捷标签点选
  const params = new URLSearchParams(location.search);
  const preset = params.get('q') || params.get('keyword');
  const mode = params.get('mode');
  if (mode && $('#search-mode')) $('#search-mode').value = mode;
  if (preset) {
    $('#search-keyword').value = preset;
    doSearch();
  }
}

const FRONTEND_BUILD = '20261013-2';

function setFrontendVersion(backendVersion) {
  const node = $('#tick-front');
  if (!node) return;
  node.textContent = `前端 ${FRONTEND_BUILD}`;
  node.title = '前端脚本构建号；若与后端版本不符，刷新页面（Ctrl+F5）即可更新';
}

function setFfmpegState(available) {
  const node = $('#foot-ffmpeg');
  if (!node) return;
  node.innerHTML = available
    ? '<i class="bi bi-check2"></i> ffmpeg 已就绪（输出 mp4）'
    : '<i class="bi bi-exclamation-triangle"></i> 无 ffmpeg（输出 .ts）';
  node.dataset.state = available ? 'ok' : 'warn';
}

function currentView() {
  const active = $('.view.active');
  return active ? active.id.replace('view-', '') : 'search';
}

function switchView(name) {
  $$('.tab').forEach((item) => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  views.ticker(name);
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  if (name === 'library') loadLibrary();
  if (name === 'tasks') refreshTasks();
  if (name === 'sources') loadSources();
  if (name === 'logs') refreshLogs();
}

function installNav() {
  $$('.tab').forEach((item) => item.addEventListener('click', () => switchView(item.dataset.view)));
  window.addEventListener('hashchange', () => {
    const name = (location.hash || '').replace('#', '');
    if (name && $(`#view-${name}`)) switchView(name);
  });
  const initial = (location.hash || '').replace('#', '');
  if (initial && $(`#view-${initial}`)) switchView(initial);
}

/* ============================================================
   Socket.IO 实时通道
   ============================================================ */

function connectSocket() {
  if (typeof io === 'undefined') {
    $('#conn-state').textContent = '轮询模式';
    return;
  }
  const socket = io({ transports: ['websocket', 'polling'] });
  socket.on('connect', () => { $('#conn-state').textContent = '已连接'; });
  socket.on('disconnect', () => { $('#conn-state').textContent = '连接已断开'; });
  socket.on('log', (entry) => {
    if (currentView() === 'logs' || entry.level === 'ERROR') {
      views.appendLog(entry, state.autoScroll !== false);
    }
  });
  socket.on('task_update', (snapshot) => {
    const index = state.tasks.findIndex((t) => t.id === snapshot.id);
    if (index >= 0) {
      state.tasks[index] = { ...state.tasks[index], ...snapshot };
      renderTaskList();
    } else {
      refreshTasks(true);
    }
    if (snapshot.status === 'completed' && state.lastFinished[snapshot.id] !== 'completed') {
      state.lastFinished[snapshot.id] = 'completed';
      const task = state.tasks.find((t) => t.id === snapshot.id) || {};
      toastOk(`下载完成：${task.anime_name || ''} ${task.episode_name || ''}`);
    }
    if (snapshot.status === 'failed' && state.lastFinished[snapshot.id] !== 'failed') {
      state.lastFinished[snapshot.id] = 'failed';
      toastErr(`下载失败：${snapshot.error || '未知错误'}`);
    }
    refreshStats();
  });
  socket.on('task_removed', () => refreshTasks(true));
  socket.on('tasks_cleared', () => refreshTasks(true));
  socket.on('ffmpeg_status', () => { if ($('#ffmpeg-status')) loadFfmpegStatus(true); });
  state.socket = socket;
}

/* ============================================================
   数据加载
   ============================================================ */
/** 把源列表与检索页的源选择器一起刷新（顺序一变两处都要跟）。 */
