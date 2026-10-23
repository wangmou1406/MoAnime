/* ============================================================
   番剧自动整理

   流程：打开面板 → 扫描出计划（只看不动）→ 用户核对 → 执行。
   后端每次都重算计划，前端回传的只是「方案 + 默认季」两个参数。
   ============================================================ */

let orgPlan = null;

function orgFillSchemes() {
  const sel = $('#organize-scheme');
  if (!sel || sel.options.length) return;
  const schemes = state.schemes || {};
  Object.keys(schemes).forEach((key) => {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = schemes[key];
    sel.appendChild(opt);
  });
}

async function openOrganize() {
  // 设置可能还没加载过（用户直接点媒体库进来），先补一次
  if (!state.settings) {
    const resp = await api('/api/settings');
    if (resp.ok) {
      state.settings = resp.data.settings || {};
      state.schemes = resp.data.schemes || state.schemes;
    }
  }
  orgFillSchemes();
  const settings = state.settings || {};
  $('#organize-scheme').value = settings.naming_scheme || 'simple';
  $('#organize-season').value = settings.default_season || 1;
  showModal('modal-organize');
  await loadOrganizePlan();
}

async function loadOrganizePlan() {
  const box = $('#organize-list');
  const applyBtn = $('#btn-organize-apply');
  box.innerHTML = '<div class="org-empty">扫描中…</div>';
  $('#organize-summary').textContent = '扫描中…';
  applyBtn.disabled = true;
  applyBtn.textContent = '执行整理';
  orgPlan = null;

  const resp = await api('/api/library/organize/plan', 'POST', {
    scheme: $('#organize-scheme').value,
    season: Number($('#organize-season').value) || 1,
  });
  if (!resp.ok) {
    box.innerHTML = `<div class="org-empty">扫描失败：${esc(resp.error)}</div>`;
    $('#organize-summary').textContent = '扫描失败';
    return;
  }
  orgPlan = resp.data;
  renderOrganize(orgPlan);
}

function renderOrganize(plan) {
  const box = $('#organize-list');
  const stats = plan.stats || {};
  const items = plan.items || [];

  $('#organize-summary').textContent =
    `${stats.total} 个视频 · 待整理 ${stats.move} · 跳过 ${stats.skip} · ${fmtSize(stats.bytes)}`;

  const applyBtn = $('#btn-organize-apply');
  applyBtn.disabled = !stats.move;
  applyBtn.textContent = stats.move ? `执行整理（${stats.move} 个）` : '无需整理';

  if (!items.length) {
    box.innerHTML = '<div class="org-empty">下载目录里没有视频文件</div>';
    return;
  }

  // 按番名分组，方便逐个番核对路径
  const groups = new Map();
  items.forEach((item) => {
    const key = item.anime || '未识别（保持原样）';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });

  const html = [];
  groups.forEach((list, name) => {
    const moveCount = list.filter((i) => i.action === 'move').length;
    html.push(`<div class="org-group">${esc(name)} · ${list.length} 集 · 待整理 ${moveCount}</div>`);
    list.forEach((item) => html.push(orgRow(item)));
  });
  box.innerHTML = html.join('');
}

function orgRow(item) {
  const isMove = item.action === 'move';
  const tags = [];
  if (isMove) {
    const conflict = (item.reason || '').includes('加序号');
    tags.push(conflict
      ? '<span class="tag conflict">重名</span>'
      : '<span class="tag move">移动</span>');
    const subs = (item.subtitles || []).length;
    if (subs) tags.push(`<span class="tag sub">字幕 ${subs}</span>`);
  } else {
    tags.push(`<span class="tag">${esc(item.reason || '跳过')}</span>`);
  }

  const to = isMove ? esc(item.dst_rel) : '—';
  return `<div class="org-row ${isMove ? '' : 'skip'}">
    <span class="from" title="${esc(item.src)}">${esc(item.src_rel)}</span>
    <span class="arrow"><i class="bi bi-arrow-right"></i></span>
    <span class="to">${to}</span>
    <span class="text-dim mono" style="flex:0 0 auto;font-size:11px">${fmtSize(item.size)}</span>
    ${tags.join('')}
  </div>`;
}

async function applyOrganize() {
  if (!orgPlan || !orgPlan.stats.move) return;
  const stats = orgPlan.stats;
  const ok = confirm(
    `确认整理 ${stats.move} 个文件（${fmtSize(stats.bytes)}）？\n\n` +
    '只会移动和改名，不会删除任何文件。\n' +
    '同名冲突会自动加 (2)，可在整理后核对。'
  );
  if (!ok) return;

  const btn = $('#btn-organize-apply');
  btn.disabled = true;
  btn.textContent = '整理中…';

  const resp = await api('/api/library/organize/apply', 'POST', {
    scheme: $('#organize-scheme').value,
    season: Number($('#organize-season').value) || 1,
  });

  if (!resp.ok) {
    toastErr(resp.error);
    btn.disabled = false;
    btn.textContent = '执行整理';
    return;
  }

  const result = resp.data || {};
  const s = result.stats || {};
  if (s.failed) {
    toastWarn(`整理完成：移动 ${s.moved} 个，失败 ${s.failed} 个（详情见日志）`);
    (result.failed || []).slice(0, 3).forEach((f) => console.warn('整理失败', f));
  } else if (s.moved) {
    toastOk(`整理完成：移动 ${s.moved} 个，清理空目录 ${result.pruned_dirs || 0} 个`);
  } else {
    toastOk('没有需要移动的文件');
  }

  await loadOrganizePlan();
  await loadLibrary();
}
