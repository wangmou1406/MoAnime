/* ============================================================
   通用工具：DOM、请求、提示、格式化
   ============================================================ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (value) => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function fmtSize(bytes) {
  const n = Number(bytes || 0);
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

const fmtSpeed = (bps) => (bps ? `${fmtSize(bps)}/s` : '--');

/** 秒数 → 「1 小时 2 分」/「3 分 12 秒」/「8 秒」。算不出来返回 '--'。 */
function fmtEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '--';
  const total = Math.round(value);
  if (total < 60) return `${total} 秒`;
  if (total < 3600) return `${Math.floor(total / 60)} 分 ${total % 60} 秒`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours} 小时 ${minutes} 分`;
}

/** 紧凑版：14:02 / 1:02:15。任务行空间窄，用这个避免换行。 */
function fmtEtaShort(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '--';
  const total = Math.round(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

function fmtTime(ts) {
  if (!ts) return '--';
  if (typeof ts === 'number') return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
  return String(ts);
}

function relativeTime(ts) {
  if (!ts) return '';
  const date = new Date(String(ts).replace(' ', 'T'));
  const diff = (Date.now() - date.getTime()) / 1000;
  if (Number.isNaN(diff)) return String(ts);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/* ---------------------------------------------------------- 请求 */
// 记录最近 60 次请求，便于出错时在控制台用 __animeDownloaderRequests() 回看真实发出的内容
const API_TRACE = [];
const API_TRACE_LIMIT = 60;

function __animeDownloaderRequests() {
  console.table(API_TRACE);
  return API_TRACE;
}
window.__animeDownloaderRequests = __animeDownloaderRequests;

async function api(path, options, body) {
  // 兼容两种调用方式：
  //   api(path, 'POST', {...})          —— 项目里 15 处都是这种
  //   api(path, { method, body })       —— 选项对象写法
  // 之前这里只写了对象解构，传字符串时 method 会退化成 GET、body 被丢掉，
  // 于是参数全部丢失（表现为“请输入检索关键字”“缺少源或动漫地址”等）。
  const opts = (typeof options === 'string')
    ? { method: options, body: body === undefined ? null : body }
    : (options || {});
  const method = opts.method || 'GET';
  let payload = opts.body === undefined ? null : opts.body;
  if (payload === undefined) payload = null;
  const raw = Boolean(opts.raw);
  const timeout = opts.timeout || 180000;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const entry = { time: new Date().toLocaleTimeString(), method, path, body: null, status: '-', note: '' };
  API_TRACE.push(entry);
  if (API_TRACE.length > API_TRACE_LIMIT) API_TRACE.shift();
  try {
    const fetchOptions = { method, headers: {}, signal: controller.signal };
    if (payload !== null) {
      fetchOptions.headers['Content-Type'] = 'application/json; charset=utf-8';
      fetchOptions.body = JSON.stringify(payload);
      entry.body = payload;
    }
    const resp = await fetch(path, fetchOptions);
    entry.status = resp.status;
    if (raw) return resp;
    const text = await resp.text();
    let json = null;
    if (text) {
      try { json = JSON.parse(text); } catch (e) { json = null; }
    }
    if (json && typeof json === 'object') {
      if (!resp.ok && json.ok !== false) json.ok = false;
      if (json.ok === false) entry.note = String(json.error || '').slice(0, 90);
      return json;
    }
    // 后端没返回 JSON：把能看懂的线索直接给出来，别只丢一个状态码
    const route = resp.url ? new URL(resp.url, location.origin).pathname : path;
    const hint = json ? '' :
      (text.trim().startsWith('<') ? '（返回的是网页而不是接口数据，可能端口上跑的不是本程序）' : '');
    entry.note = '非 JSON 响应';
    return {
      ok: false,
      error: `接口 ${route} 返回 HTTP ${resp.status} ${resp.statusText || ''}${hint}`.trim(),
      raw: text.slice(0, 300),
    };
  } catch (err) {
    if (err.name === 'AbortError') return { ok: false, error: '请求超时或被取消' };
    return { ok: false, error: String(err.message || err) };
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------------------------------------------------- 提示 */
function toast(message, type = 'info', timeout = 3600) {
  const icons = { ok: 'check-circle-fill', err: 'exclamation-octagon-fill', warn: 'exclamation-triangle-fill', info: 'info-circle-fill' };
  const node = document.createElement('div');
  node.className = `toast-item ${type}`;
  node.innerHTML = `<i class="bi bi-${icons[type] || icons.info}"></i><div>${esc(message)}</div>`;
  $('#toast-stack').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .25s';
    setTimeout(() => node.remove(), 260);
  }, timeout);
}

const toastOk = (m) => toast(m, 'ok');
const toastErr = (m) => toast(m, 'err', 5200);
const toastWarn = (m) => toast(m, 'warn', 4600);

/* ============================================================
   右下角「作业进度」面板
   ------------------------------------------------------------
   除了下载任务（那有独立的任务页），其它耗时操作都在这里显示进度：
   检索、测活、测源、广告体检、字幕搜索/下载、媒体库扫描、清理 等。
   用法：
     const job = jobs.start('广告体检', { total: 11 });
     jobs.set(job, 3, '正在读取 量子资源');
     jobs.finish(job, '完成，发现 2 个异常源');
     jobs.fail(job, '全部源都读取失败');
   ============================================================ */
const jobs = (() => {
  const items = new Map();
  let seq = 0;

  function panel() {
    let node = document.getElementById('job-panel');
    if (node) return node;
    node = document.createElement('div');
    node.id = 'job-panel';
    node.className = 'job-panel';
    node.innerHTML = `<div class="job-panel-head">
        <i class="bi bi-activity"></i> 后台作业
        <span class="badge badge-dim">空闲</span>
        <span class="spacer" style="flex:1"></span>
        <button class="job-clear" title="清空已结束的作业">清空</button>
      </div><div class="job-list"></div>`;
    node.querySelector('.job-clear').addEventListener('click', () => {
      items.forEach((job, id) => {
        if (job.state !== 'running') { remove(id); }
      });
      render();
    });
    document.body.appendChild(node);
    return node;
  }

  function remove(id) {
    const job = items.get(id);
    if (job && job.node && job.node.parentNode) job.node.parentNode.removeChild(job.node);
    items.delete(id);
  }

  function render() {
    const node = document.getElementById('job-panel');
    const stack = document.getElementById('toast-stack');
    if (stack) {
      // 作业面板占据右下角时，把提示条往上抬，避免互相遮挡
      const panel = node && !node.classList.contains('hidden') ? node : null;
      stack.style.bottom = panel ? `${panel.offsetHeight + 26}px` : '18px';
    }
    if (!node) return;
    const running = [...items.values()].filter((j) => j.state === 'running').length;
    node.classList.toggle('empty', items.size === 0);
    const badge = node.querySelector('.job-panel-head .badge');
    if (badge) {
      badge.textContent = running ? `${running} 进行中` : '已结束';
      badge.className = 'badge ' + (running ? 'badge-soft' : 'badge-dim');
    }
    if (items.size === 0) {
      node.classList.add('hidden');   // 全部清理完就收起面板
    } else {
      node.classList.remove('hidden');
    }
  }

  function makeRow(job) {
    const row = document.createElement('div');
    row.className = 'job-item';
    row.innerHTML = `
      <div class="job-line">
        <span class="job-name"></span>
        <span class="job-count mono"></span>
      </div>
      <div class="job-bar"><i></i></div>
      <div class="job-log"></div>
      <div class="job-msg"></div>`;
    return row;
  }

  function paint(job) {
    if (!job.node) return;
    const pct = job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : (job.state === 'running' ? 15 : 100);
    const nameEl = job.node.querySelector('.job-name');
    nameEl.innerHTML =
      `<i class="bi ${job.state === 'running' ? 'bi-arrow-repeat spin' : (job.state === 'ok' ? 'bi-check-circle-fill' : 'bi-exclamation-octagon-fill')}"></i>
       ${esc(job.title)}`;
    job.node.querySelector('.job-count').textContent =
      job.total ? `${job.done}/${job.total}` : '';
    job.node.querySelector('.job-bar').firstElementChild.style.width = `${pct}%`;

    // 有日志就显示滚动日志（面板里只露最近几行），点了能看全部；
    // 没有日志时退回一行 message（老调用方不受影响）
    const logBox = job.node.querySelector('.job-log');
    const msgBox = job.node.querySelector('.job-msg');
    if (job.logs && job.logs.length) {
      logBox.style.display = '';
      msgBox.style.display = 'none';
      logBox.innerHTML = job.logs.map((line) => (
        `<div class="job-log-line${line.level ? ' ' + line.level : ''}">`
        + `<span class="t mono">${esc(line.time)}</span>`
        + `<span class="x">${esc(line.text)}</span></div>`
      )).join('');
      logBox.scrollTop = logBox.scrollHeight;          // 自动滚到最新
      nameEl.classList.add('has-log');
      nameEl.title = '点击查看完整日志';
    } else {
      logBox.style.display = 'none';
      msgBox.style.display = '';
      msgBox.textContent = job.message || '';
      nameEl.classList.remove('has-log');
    }
    job.node.classList.toggle('done', job.state === 'ok');
    job.node.classList.toggle('failed', job.state === 'failed');
  }

  const LOG_LIMIT = 400;    // 内存里最多保留多少行

  /** 追加一行日志（带时间戳）；level 可选 ok / warn / err，用于着色。 */
  function log(job, text, level) {
    if (!job || !items.has(job.id)) return;
    const d = new Date();
    const time = `${String(d.getHours()).padStart(2, '0')}:`
      + `${String(d.getMinutes()).padStart(2, '0')}:`
      + `${String(d.getSeconds()).padStart(2, '0')}`;
    job.logs = job.logs || [];
    job.logs.push({ time, text: String(text || ''), level: level || '' });
    if (job.logs.length > LOG_LIMIT) job.logs.splice(0, job.logs.length - LOG_LIMIT);
    paint(job);
  }

  /** 弹出完整日志（面板里只显示最近几行，长任务需要能翻全部）。 */
  function showLog(job) {
    if (!job) return;
    const lines = job.logs || [];
    const body = lines.length
      ? lines.map((l) => `<div class="log-line${l.level ? ' ' + l.level : ''}">`
          + `<span class="t mono">${esc(l.time)}</span>`
          + `<span class="x">${esc(l.text)}</span></div>`).join('')
      : '<div class="text-dim">（这个作业没有日志）</div>';
    showInfo(`${job.title} · 日志（${lines.length} 行）`,
      `<div class="job-log-full">${body}</div>`);
  }

  function start(title, opts = {}) {
    const id = 'job' + (++seq);
    const job = { id, title, total: opts.total || 0, done: 0,
                  message: opts.message || '', state: 'running', node: null, logs: [] };
    panel();
    job.node = makeRow(job);
    // 点作业名看完整日志
    job.node.querySelector('.job-name').addEventListener('click', () => showLog(job));
    document.querySelector('#job-panel .job-list').appendChild(job.node);
    items.set(id, job);
    paint(job);
    render();
    return job;
  }

  function set(job, done, message) {
    if (!job || !items.has(job.id)) return;
    if (typeof done === 'number') job.done = done;
    if (message !== undefined) job.message = message;
    paint(job);
  }

  function step(job, message) {
    if (!job || !items.has(job.id)) return;
    job.done += 1;
    if (message !== undefined) job.message = message;
    paint(job);
  }

  function settle(job, state, message, type) {
    if (!job || !items.has(job.id)) return;
    job.state = state;
    if (job.total) job.done = job.total;
    if (message !== undefined) job.message = message;
    paint(job);
    render();
    // 结束的作业保留一会儿再淡出，避免"没看见就没了"
    setTimeout(() => {
      const current = items.get(job.id);
      if (current && current.state !== 'running') {
        if (current.node) current.node.classList.add('fade');
        setTimeout(() => { remove(job.id); render(); }, 700);
      }
    }, type === 'failed' ? 12000 : 9000);
  }

  const finish = (job, message) => {
    settle(job, 'ok', message, 'ok');
    if (message) toastOk(`${job.title}：${message}`);
  };
  const fail = (job, message) => {
    settle(job, 'failed', message, 'failed');
    if (message) toastErr(`${job.title}：${message}`);
  };

  return { start, set, step, finish, fail, items, log, showLog };
})();
window.jobs = jobs;

/* ---------------------------------------------------------- 模态框 */
const modals = {};
function getModal(id) {
  if (!modals[id]) modals[id] = new bootstrap.Modal(document.getElementById(id));
  return modals[id];
}
const showModal = (id) => getModal(id).show();
const hideModal = (id) => getModal(id).hide();

function showInfo(title, html) {
  $('#modal-info-title').textContent = title;
  $('#modal-info-body').innerHTML = html;
  showModal('modal-info');
}

/* ---------------------------------------------------------- 删除任务确认
   删除下载记录时附带「同时删除已下载的视频文件」选项。
   默认不勾（记录可以安全重下，文件删了就没了）。
   返回 Promise<{ok: boolean, deleteFile: boolean}>；取消时 ok=false。
   用法：
     const r = await askDeleteTasks({ name: '某番剧', tasks: [...] });
     if (r.ok) await api('/api/tasks/bulk', 'POST',
                         { action: 'delete', ids, delete_file: r.deleteFile });
   ---------------------------------------------------------- */
function askDeleteTasks(opts = {}) {
  const tasks = opts.tasks || [];
  const name = opts.name || '';
  const summary = $('#delete-task-summary');
  const check = $('#delete-task-with-file');
  const hint = $('#delete-task-file-hint');
  const warn = $('#delete-task-warn');
  const confirmBtn = $('#btn-delete-task-confirm');
  const confirmText = $('#delete-task-confirm-text');
  if (!summary || !check || !confirmBtn) return Promise.resolve({ ok: false, deleteFile: false });

  const withFile = tasks.filter((t) => t && t.save_path);
  const running = tasks.filter((t) => ['running', 'queued'].includes(t.status)).length;
  const totalBytes = withFile.reduce((sum, t) => sum + (Number(t.total_bytes) || 0), 0);

  summary.innerHTML = (name
    ? `<b>${esc(name)}</b> · ${tasks.length} 条下载记录`
    : `${tasks.length} 条下载记录`)
    + (running ? `<br><span class="text-dim">其中 ${running} 个正在下载/排队，会先被取消。</span>` : '');

  hint.textContent = withFile.length
    ? `（${withFile.length} 个有文件路径${totalBytes ? '，约 ' + fmtSize(totalBytes) : ''}）`
    : '（这些记录还没有下载出文件）';

  check.checked = false;
  check.disabled = !withFile.length;
  warn.hidden = true;
  confirmText.textContent = '删除记录';

  const sync = () => {
    warn.hidden = !check.checked;
    confirmText.textContent = check.checked ? '删除记录和文件' : '删除记录';
    confirmBtn.classList.toggle('btn-danger', true);
  };
  check.onchange = sync;

  return new Promise((resolve) => {
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      confirmBtn.onclick = null;
      resolve({ ok, deleteFile: ok ? check.checked : false });
    };

    confirmBtn.onclick = () => {
      // 先落结果再关弹窗：关弹窗会触发 hide.bs.modal，
      // 那个监听器会把没落定的情况当成「取消」，顺序反了会误判。
      done(true);
      hideModal('modal-delete-task');
    };
    const node = document.getElementById('modal-delete-task');
    if (node) {
      // 用 hide.bs.modal（关闭动作一开始就触发）而不是 hidden.bs.modal
      // （过渡结束后才触发）—— 后者在后台标签页里可能永远等不到。
      node.addEventListener('hide.bs.modal', () => done(false), { once: true });
    }
    showModal('modal-delete-task');
  });
}

/* ---------------------------------------------------------- 其它 */
function debounce(fn, wait = 260) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function statusLabel(status) {
  return {
    queued: '排队中', running: '下载中', paused: '已暂停',
    completed: '已完成', failed: '失败', canceled: '已取消',
    skipped: '已跳过',
  }[status] || status;
}

function statusBadge(status) {
  return `<span class="chip-status st-${esc(status)}">${esc(statusLabel(status))}</span>`;
}

function coverHtml(item) {
  const fallback = '<div class="cover-fallback"><i class="bi bi-image"></i></div>';
  if (!item || !item.cover) return fallback;
  return `<img class="cover" src="${esc(item.cover)}" alt="" loading="lazy"
    onerror="this.parentNode.innerHTML='&lt;div class=&quot;cover-fallback&quot;&gt;&lt;i class=&quot;bi bi-image&quot;&gt;&lt;/i&gt;&lt;/div&gt;'">`;
}

function jsonPretty(obj) {
  try { return JSON.stringify(obj, null, 2); } catch (e) { return String(obj); }
}

function copyText(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => toastOk('已复制到剪贴板'),
      () => toastErr('复制失败'));
  } else {
    toastWarn('当前浏览器不支持自动复制');
  }
}

function parseJsonInput(value, fallback = {}) {
  const text = (value || '').trim();
  if (!text) return fallback;
  try { return JSON.parse(text); } catch (e) { throw new Error('JSON 格式不正确: ' + e.message); }
}
