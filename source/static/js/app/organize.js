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

/* ============================================================
   按季整理：把一部番的连续集号拆进 Season 目录

   季边界两种来源：
     1. 手填「每季集数」，如 100,22
     2. 自动爬取 —— 去 B 站查同系列所有季的集数（bili.series_seasons）
   只移动和改名，不删文件。
   ============================================================ */

let seasonRows = [];

function parseSeasonCounts(text) {
  return String(text || '')
    .split(/[,，\s/+]+/)
    .map((item) => parseInt(item, 10))
    .filter((value) => Number.isFinite(value) && value > 0);
}

function seasonOfEpisode(episode, counts) {
  let start = 1;
  for (let index = 0; index < counts.length; index += 1) {
    if (episode < start + counts[index]) return index + 1;
    start += counts[index];
  }
  return counts.length;          // 超出总和时并到最后一季
}

function previewSeasons(row, counts) {
  const buckets = {};
  (row.episodes || []).forEach((episode) => {
    const season = counts.length ? seasonOfEpisode(episode, counts) : 1;
    buckets[season] = (buckets[season] || 0) + 1;
  });
  return buckets;
}

function updateSeasonPreview(rowEl) {
  const anime = rowEl.dataset.anime;
  const row = seasonRows.find((item) => item.anime === anime);
  const preview = rowEl.querySelector('.season-preview');
  const applyBtn = $('#btn-season-apply');
  if (!row || !preview) return;

  const counts = parseSeasonCounts(rowEl.querySelector('.season-counts').value);
  const buckets = previewSeasons(row, counts);
  const keys = Object.keys(buckets).sort((a, b) => Number(a) - Number(b));

  if (!counts.length) {
    preview.className = 'season-preview';
    preview.textContent = '未填 → 全部归到第 1 季';
    return;
  }

  const parts = keys.map((key) => `S${key}: ${buckets[key]} 集`);
  const sum = counts.reduce((acc, value) => acc + value, 0);
  preview.textContent = parts.join(' · ');
  if (sum < row.max) {
    preview.className = 'season-preview warn';
    preview.textContent += `（每季集数合计 ${sum} < 最大集号 ${row.max}，后面的并到最后一季）`;
  } else {
    preview.className = 'season-preview ok';
  }
  applyBtn.disabled = false;
}

function renderSeasonRows() {
  const box = $('#season-rows');
  if (!seasonRows.length) {
    box.innerHTML = '<div class="org-empty">下载目录里没有可识别的剧集</div>';
    return;
  }
  box.innerHTML = seasonRows.map((row) => `
    <div class="season-row" data-anime="${esc(row.anime)}">
      <div class="nm">${esc(row.anime)}
        <span class="sm">${row.count} 个文件 · 第 ${row.min}–${row.max} 集</span>
      </div>
      <input class="form-control form-control-sm season-counts"
             placeholder="每季集数，如 100,22" autocomplete="off">
      <button class="btn btn-outline-light btn-sm" data-act="season-autofill"
              title="去 B 站查这个系列有几季、每季多少集">
        <i class="bi bi-cloud-download"></i> 自动爬取</button>
      <span class="season-preview">未填 → 全部归到第 1 季</span>
    </div>`).join('');
  $('#btn-season-apply').disabled = true;
}

async function openSeasonSplit() {
  showModal('modal-seasons');
  const box = $('#season-rows');
  box.innerHTML = '<div class="org-empty">扫描中…</div>';
  $('#btn-season-apply').disabled = true;

  const resp = await api('/api/library/organize/plan', 'POST', {});
  if (!resp.ok) {
    box.innerHTML = `<div class="org-empty">扫描失败：${esc(resp.error)}</div>`;
    return;
  }
  const groups = new Map();
  ((resp.data || {}).items || []).forEach((item) => {
    if (!item.anime || item.episode === null || item.episode === undefined) return;
    if (!groups.has(item.anime)) groups.set(item.anime, { anime: item.anime, episodes: [] });
    groups.get(item.anime).episodes.push(Number(item.episode));
  });
  seasonRows = Array.from(groups.values()).map((row) => {
    const episodes = row.episodes.filter((n) => Number.isFinite(n)).sort((a, b) => a - b);
    return {
      anime: row.anime,
      episodes,
      count: episodes.length,
      min: episodes[0],
      max: episodes[episodes.length - 1],
    };
  }).sort((a, b) => a.anime.localeCompare(b.anime, 'zh'));
  renderSeasonRows();
}

async function autoFillSeasons(button) {
  const rowEl = button.closest('.season-row');
  const anime = rowEl.dataset.anime;
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="bi bi-hourglass-split"></i> 查询中…';
  try {
    const resp = await api('/api/library/organize/suggest', 'POST', { anime_name: anime });
    if (!resp.ok) {
      toastErr(resp.error);
      return;
    }
    const data = resp.data || {};
    const preview = rowEl.querySelector('.season-preview');
    if (!data.splittable) {
      preview.className = 'season-preview warn';
      preview.textContent = data.reason
        || (data.seasons && data.seasons.length
          ? `B 站只有 1 季（${data.seasons[0].total} 集），没有分季可拆`
          : 'B 站查不到这个系列的分季信息');
      return;
    }
    rowEl.querySelector('.season-counts').value = data.counts.join(',');
    updateSeasonPreview(rowEl);
    const titles = data.seasons.map((s) => `${s.title}(${s.total})`).join(' / ');
    toastOk(`${anime}：B 站 ${data.counts.length} 季 共 ${data.total} 集 — ${titles}`);
  } catch (err) {
    toastErr(`自动爬取失败：${err}`);
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

async function applySeasonSplit() {
  const rules = {};
  $$('#season-rows .season-row').forEach((rowEl) => {
    const counts = parseSeasonCounts(rowEl.querySelector('.season-counts').value);
    if (counts.length) rules[rowEl.dataset.anime] = counts;
  });
  const names = Object.keys(rules);
  if (!names.length) {
    toastWarn('先给至少一部番填「每季集数」，或点「自动爬取」');
    return;
  }
  if (!confirm(`按季整理这 ${names.length} 部番？\n\n会移动文件到 Season 目录，不会删除。\n${names.join('、')}`)) {
    return;
  }
  const button = $('#btn-season-apply');
  button.disabled = true;
  button.textContent = '整理中…';
  try {
    const resp = await api('/api/library/organize/apply', 'POST', {
      season_rules: rules,
      season: Number((state.settings || {}).default_season) || 1,
    });
    if (!resp.ok) {
      toastErr(resp.error);
      return;
    }
    const stats = (resp.data || {}).stats || {};
    if (stats.failed) {
      toastWarn(`移动 ${stats.moved} 个，失败 ${stats.failed} 个（详情见日志）`);
    } else {
      toastOk(`按季整理完成：移动 ${stats.moved} 个，清理空目录 ${resp.data.pruned_dirs || 0} 个`);
    }
    hideModal('modal-seasons');
    await loadLibrary();
  } finally {
    button.disabled = false;
    button.textContent = '按季整理';
  }
}