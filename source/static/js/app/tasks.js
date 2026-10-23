/* ============================================================
   动漫下载机 · 下载任务与广告体检
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
async function runAdCheck() {
  const anime = state.currentAnime;
  if (!anime || !anime.name) { toastWarn('请先在检索结果里点选一部动漫'); return; }
  const episode = (state.episodes[0] || {}).name || '';
  const button = $('#btn-duration-check');
  if (button) { button.disabled = true; button.classList.add('is-busy'); }

  const enabled = state.sources.filter((s) => s.enabled);
  const job = jobs.start(`广告体检 · ${anime.name}`, {
    total: enabled.length,
    message: '开始：时长通道 + 画面文字通道…',
  });
  // 使用建议：指定具体集数，各源才会去取同一集，时长对比才有意义
  jobs.log(job, episode
    ? `对比基准：${episode}（各源都会取这一集）`
    : '未指定集数：时长通道只能比「各源的第 1 集」，'
      + '若源之间集数不一致会自动跳过；建议先在剧集面板点某一集再体检', episode ? 'ok' : 'warn');

  const rows = [];
  let meta = { seconds: 20, text_ok: true, text_why: '', text_engine: '' };
  let final = null;
  try {
    const resp = await fetch('/api/tools/ad-check-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ anime_name: anime.name, episode_name: episode,
                             seconds: 20, step: 2 }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let msg;
        try { msg = JSON.parse(line); } catch (err) { continue; }
        if (msg.type === 'start') {
          meta.seconds = msg.seconds || 20;
          meta.text_ok = msg.text_ok !== false;
          meta.text_why = msg.text_why || '';
          meta.text_engine = msg.text_engine || '';
          if (!meta.text_ok) {
            jobs.log(job, `画面文字通道不可用（${meta.text_why}），只跑时长对比`,
                     'warn');
          } else {
            jobs.log(job, `OCR 引擎：${meta.text_engine}`, 'ok');
          }
        } else if (msg.type === 'row') {
          const row = msg.row || {};
          rows.push(row);
          // 注意：NDJSON 是逐个源推送的，推这一行时跨源时长汇总还没跑完，
          // 所以此刻 row.is_ad 一定还是 false —— 文字命中要看 row.hits
          const textHit = !!(row.hits && row.hits.length);
          const bad = textHit || row.is_ad || row.is_incomplete;
          const detail = textHit
            ? `画面文字命中：${row.hits.join('、')}`
            : ((row.reasons || []).join('；') || row.note
               || (row.text_note ? `文字：${row.text_note}` : '正常'));
          jobs.log(job, `${row.source} · ${detail}`,
                   bad ? 'err' : (row.ok ? 'ok' : 'warn'));
          jobs.step(job, msg.index, `${msg.index}/${msg.total} 已完成`);
        } else if (msg.type === 'done') {
          final = msg;
        } else if (msg.type === 'error') {
          throw new Error(msg.error || '体检出错');
        }
      }
    }

    if (!final) throw new Error('没有收到体检结果');
    state.sources = final.sources || state.sources;
    views.sources(state.sources);
    views.searchSourcePicker(state.sources);
    initSortable();

    const flagged = final.flagged || [];
    jobs.finish(job, flagged.length
      ? `发现 ${flagged.length} 个问题源，已自动排到末尾`
      : `全部正常（${(final.clean || []).length} 个源）`);
    showAdCheckReport(anime.name, episode, final, meta);
  } catch (err) {
    jobs.fail(job, String(err.message || err));
    toastErr(`广告体检失败：${err.message || err}`);
  } finally {
    if (button) { button.disabled = false; button.classList.remove('is-busy'); }
  }
}

function showAdCheckReport(animeName, episode, data, meta) {
  const rows = data.rows || [];
  const fmt = (s) => (s === undefined || s === null || s === '')
    ? '-' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
  const isBad = (r) => !!((r.hits && r.hits.length) || r.is_ad || r.is_incomplete);
  const bad = rows.filter(isBad);
  const good = rows.filter((r) => r.ok && !isBad(r));
  const skipped = rows.filter((r) => !r.ok);

  const kindOf = (r) => {
    if (r.hits && r.hits.length) return { label: '文字广告', cls: 'badge-soft' };
    if (r.is_incomplete) return { label: '内容残缺', cls: 'badge-dim' };
    if (r.is_ad) return { label: '插播广告', cls: 'badge-soft' };
    return { label: '正常', cls: 'badge-dim' };
  };

  const badBlock = (r) => {
    const k = kindOf(r);
    const samples = (r.samples || []).map((s) =>
      `<div class="mono text-dim" style="font-size:11.5px">${s.t}s ·
         ${esc((s.text || '').slice(0, 72))}</div>`).join('');
    return `<tr>
      <td><span class="badge ${k.cls}">${k.label}</span></td>
      <td>${esc(r.source)}</td>
      <td class="mono">${fmt(r.seconds)}</td>
      <td class="mono">${r.diff === undefined || r.diff === null ? '-'
                        : (r.diff > 0 ? '+' : '') + r.diff + 's'}</td>
      <td style="font-size:12px">${esc((r.reasons || []).join('；') || r.note || '')}
        ${samples}</td></tr>`;
  };

  showInfo('广告体检（时长 + 画面文字）', `
    <div class="hint-box mb-3" style="font-size:12.5px;line-height:1.7">
      «${esc(animeName)}» 的 <code>${esc(episode || '第01集')}</code> · 中位时长
      <code>${fmt(data.median)}</code><br>
      <b>时长通道</b>：
      ${data.measured_count
        ? `只对比了<b>都给出「${esc(data.used_episode || '第01集')}」的 ${data.measured_count} 个源</b>
           —— 各源给不同集时比时长没有意义，所以那些源不参与
           ${data.skipped_same_count ? `（${data.skipped_same_count} 个源没有这一集，已跳过）` : ''}。
           明显更长 = 插播了广告片；明显更短 = 内容残缺。`
        : '<span class="text-dim">没有源同时给出同一集，未做时长判定。</span>'}<br>
      <b>文字通道</b>（${meta.text_ok === false ? '未启用' : `各源前 ${meta.seconds || 20} 秒`}）：
      抽帧 OCR 看画面文字，治的是<b>「本片由 xxx 赞助发布 / 主营：棋牌 捕鱼」这类叠上去的字幕广告</b>
      —— 它<b>不改变时长</b>，时长通道测不出来。<br>
      ${meta.text_ok === false
        ? `<span class="text-dim">画面文字通道不可用（${esc(meta.text_why || '')}），本次只做了时长对比。</span>`
        : `<span class="text-dim">OCR 引擎：${esc(meta.text_engine || '')}；临时分片与帧已自动删除。</span>`}
    </div>
    ${data.spread_ok === false && data.spread_note ? `
      <div class="hint-box mb-3" style="font-size:12px">时长通道未下结论：
        ${esc(data.spread_note)}</div>` : ''}
    ${bad.length ? `
      <div class="mb-1"><b>⚠ 问题源（${bad.length} 个，已自动排到列表末尾）</b></div>
      <table class="table table-sm" style="font-size:12.5px">
        <thead><tr><th>类型</th><th>源</th><th>时长</th><th>偏差</th><th>依据</th></tr></thead>
        <tbody>${bad.map(badBlock).join('')}</tbody></table>`
    : `<div class="text-dim">未发现异常。</div>`}
    ${good.length ? `<div class="mt-3 mb-1"><b>干净（${good.length} 个）</b></div>
      <div style="font-size:12.5px">${good.map((r) => esc(r.source)).join('、')}</div>` : ''}
    ${skipped.length ? `<div class="mt-3 mb-1"><b>跳过（${skipped.length} 个）</b></div>
      <div style="font-size:12px" class="text-dim">${skipped.map((r) =>
        `${esc(r.source)}（${esc(r.note || r.text_note || '')}）`).join('、')}</div>` : ''}
  `);
}

async function applyDurationVerdict(final) {
  const rows = (final && final.rows) || [];
  if (!rows.length) return 0;
  const flagByName = new Map();
  const verdictByName = new Map();
  rows.forEach((r) => {
    if (!r.source) return;
    flagByName.set(r.source, Boolean(r.suspicious));
    if (r.ok) verdictByName.set(r.source, { verdict: r.verdict, diff: Number(r.diff) || 0 });
  });

  const items = state.sources.map((s) => {
    const verdict = verdictByName.get(s.name);
    const flagged = flagByName.has(s.name) ? flagByName.get(s.name) : Boolean(s.ad_flag);
    const note = verdict
      ? `${verdict.verdict}（${verdict.diff > 0 ? '+' : ''}${verdict.diff}s）`
      : (s.note || '');
    return {
      id: s.id,
      name: s.name,
      enabled: s.enabled,
      hidden: s.hidden,
      ad_flag: flagged,
      // 区分原因：插广告（更长）vs 内容残缺（更短）
      ad_kind: flagged
        ? (verdict ? (verdict.diff >= 15 ? 'ad' : 'incomplete') : (s.ad_kind || 'ad'))
        : '',
      note,
    };
  });
  // 干净源在前、异常源在后，各自保持原相对顺序
  items.sort((a, b) => Number(a.ad_flag) - Number(b.ad_flag));

  const resp = await api('/api/sources/reorder', 'POST', { items });
  if (!resp.ok) { toastErr('自动重排失败：' + resp.error); return 0; }
  state.sources = resp.data.sources || state.sources;
  views.sources(state.sources);
  views.searchSourcePicker(state.sources);
  initSortable();
  return items.filter((i) => i.ad_flag).length;
}

/* ============================================================
   文字广告体检：只下开头 20 秒 → OCR 看画面文字（检测完删临时文件）
   ============================================================ */

async function refreshTasks(silent) {
  const url = `/api/tasks?status=${encodeURIComponent(state.taskFilter)}`;
  const resp = await api(url);
  if (!resp.ok) {
    console.warn('[AnimeDownloader] 加载任务失败', url, resp.error);
    if (!silent) toastErr('加载任务失败：' + resp.error);
    return;
  }
  state.tasks = resp.data.tasks || [];
  views.taskStats(resp.data.stats || {});
  updateSpeedPanel(resp.data.stats || {}, state.tasks);
  renderTaskList();
  const stats = resp.data.stats || {};
  $('#nav-task-count').textContent = (stats.running || 0) + (stats.queued || 0) + (stats.paused || 0);
  console.debug('[AnimeDownloader] 任务刷新', url, '返回', state.tasks.length, '条 | 过滤条件', state.taskFilter,
    '| 实际渲染', document.querySelectorAll('#task-list .task-row').length, '条');
}

function renderTaskList() {
  let tasks = state.tasks;
  const filter = state.taskFilter || 'all';
  if (filter !== 'all') {
    const allowed = filter.split(',');
    tasks = tasks.filter((t) => allowed.includes(t.status));
  }
  const box = $('#task-list');
  // 先试原地更新：只有进度/速度在变的轮询不该重建 DOM。
  // 不要再用「给容器加 no-anim 抑制动画」那种做法：那会把入场动画整个取消，
  // 元素就停在 from{opacity:0} 上变成不可见（DOM 里有行、屏幕上却空的）。
  // 正确做法是根本不重建；真要重建时让它正常播一次入场动画。
  try {
    if (box && views.updateTaskRows(tasks)) return;
  } catch (err) {
    console.warn('[AnimeDownloader] 原地更新任务行失败，回退整体重建：', err);
  }

  try {
    views.tasks(tasks);
  } catch (err) {
    console.error('[AnimeDownloader] 渲染任务列表失败', err);
    const failBox = $('#task-list');
    if (failBox) {
      failBox.innerHTML = `<div class="empty"><i class="bi bi-exclamation-triangle"></i>
        任务列表渲染失败：${esc(err.message)}（共 ${tasks.length} 条，可用 F12 查看控制台）</div>`;
    }
  }
}

async function bulkTask(action) {
  const resp = await api('/api/tasks/bulk', 'POST', { action });
  if (!resp.ok) { toastErr(resp.error); return; }
  const count = (resp.data || {}).count;
  const labels = {
    pause_all: '已暂停全部进行中任务',
    resume_all: '已继续全部暂停任务',
    clear_pending: `已清空 ${count ?? 0} 个排队任务`,
    clear_completed: `已清理 ${count ?? 0} 条完成记录`,
    clear_failed: `已清理 ${count ?? 0} 条失败记录`,
    clear_all: `已删除全部 ${count ?? 0} 条任务记录（视频文件未删除）`,
    clear: `已清理 ${count ?? 0} 条记录`,
  };
  toastOk(labels[action] || `已执行：${action}`);
  await refreshTasks();
}

/* ============================================================
   媒体库 / 设置 / 日志
   ============================================================ */

/* ============================================================
   下载速度曲线 + 预估剩余时间

   速度每秒采一次，保留最近 60 个点，用条形图画出来。
   剩余时间 = 所有未完成任务的剩余字节 / 当前总速度。
   ============================================================ */

const SPEED_HISTORY_MAX = 60;
let speedHistory = [];
let speedTimer = null;
let lastSpeedBps = 0;

function recordSpeedSample(bps) {
  lastSpeedBps = Math.max(0, Number(bps) || 0);
  speedHistory.push(lastSpeedBps);
  if (speedHistory.length > SPEED_HISTORY_MAX) speedHistory.shift();
  drawSpeedChart();
}

function ensureSpeedTimer() {
  if (speedTimer) return;
  speedTimer = setInterval(() => recordSpeedSample(lastSpeedBps), 1000);
}

function drawSpeedChart() {
  const canvas = $('#speed-chart');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = Math.max(120, canvas.clientWidth || canvas.parentElement.clientWidth || 600);
  const cssH = 64;
  if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
  }
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const style = getComputedStyle(document.body);
  const lineColor = (style.getPropertyValue('--line') || '#e3e8f0').trim();
  const barColor = (style.getPropertyValue('--primary') || '#2563eb').trim();

  // 基线
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, cssH - 0.5);
  ctx.lineTo(cssW, cssH - 0.5);
  ctx.stroke();

  const maxValue = Math.max(1, ...speedHistory);
  const slot = cssW / SPEED_HISTORY_MAX;
  const barWidth = Math.max(1.5, slot - 1.5);
  ctx.fillStyle = barColor;
  speedHistory.forEach((value, index) => {
    if (value <= 0) return;
    const height = Math.max(1.5, (value / maxValue) * (cssH - 8));
    // 越新越亮，一眼能看出趋势
    ctx.globalAlpha = 0.35 + 0.65 * (index / Math.max(1, speedHistory.length - 1));
    ctx.fillRect(index * slot, cssH - height - 1, barWidth, height);
  });
  ctx.globalAlpha = 1;

  const scale = $('#speed-scale');
  if (scale) scale.textContent = `峰值 ${fmtSpeed(maxValue)}`;
}

function updateSpeedPanel(stats, tasks) {
  const speed = Number((stats || {}).speed) || 0;
  lastSpeedBps = speed;
  ensureSpeedTimer();

  const now = $('#speed-now');
  if (now) now.textContent = speed > 0 ? `当前 ${fmtSpeed(speed)}` : '当前空闲';

  let remainingBytes = 0;
  let active = 0;
  (tasks || []).forEach((task) => {
    if (!['running', 'paused', 'queued'].includes(task.status)) return;
    const total = Number(task.total_bytes) || 0;
    const done = Number(task.done_bytes) || 0;
    if (total > done) {
      remainingBytes += total - done;
      active += 1;
    }
  });

  const etaNode = $('#speed-eta');
  if (!etaNode) return;
  if (!active || remainingBytes <= 0) {
    etaNode.textContent = '没有进行中的任务';
    return;
  }
  if (speed <= 0) {
    etaNode.textContent = `剩 ${fmtSize(remainingBytes)}（速度为 0，可能在排队或暂停）`;
    return;
  }
  etaNode.textContent = `剩 ${fmtSize(remainingBytes)} · 预计 ${fmtEta(remainingBytes / speed)}`;
}