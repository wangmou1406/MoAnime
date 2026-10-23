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
  // 支持两种写法：
  //   13,14,21              → 目录名 Season 01 / 02 / 03
  //   下沙篇:13,王权篇:14    → 目录名直接用篇名
  return String(text || '')
    .split(/[,，\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      const matched = item.match(/^(.+?)\s*[:：]\s*(\d+)$/);
      if (matched) return { name: matched[1].trim(), count: parseInt(matched[2], 10) };
      return { name: '', count: parseInt(item, 10) };
    })
    .filter((rule) => Number.isFinite(rule.count) && rule.count > 0);
}

function seasonOfEpisode(episode, rules) {
  let start = 1;
  for (let index = 0; index < rules.length; index += 1) {
    if (episode < start + rules[index].count) return index + 1;
    start += rules[index].count;
  }
  return rules.length;          // 超出总和时并到最后一季
}

function previewSeasons(row, rules) {
  const buckets = {};
  (row.episodes || []).forEach((episode) => {
    const season = rules.length ? seasonOfEpisode(episode, rules) : 1;
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

  const rules = parseSeasonCounts(rowEl.querySelector('.season-counts').value);
  const buckets = previewSeasons(row, rules);
  const keys = Object.keys(buckets).sort((a, b) => Number(a) - Number(b));

  if (!rules.length) {
    preview.className = 'season-preview';
    preview.textContent = '未填 → 全部归到第 1 季';
    return;
  }

  const parts = keys.map((key) => {
    const rule = rules[Number(key) - 1] || {};
    return `${rule.name || 'S' + key}: ${buckets[key]} 集`;
  });
  const sum = rules.reduce((acc, rule) => acc + rule.count, 0);
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
             placeholder="13,14,21 或 下沙篇:13,王权篇:14" autocomplete="off">
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
  renderArcTargets();
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
    // 用「篇名:集数」的写法填进去，目录名就是篇名而不是 Season 01
    const series = (data.series_title || '').trim();
    const parts = (data.seasons || []).map((season, index) => {
      let name = (season.title || '').trim();
      if (series && name.startsWith(series)) name = name.slice(series.length).trim();
      if (!name) name = `S${index + 1}`;
      return `${name}:${season.total}`;
    });
    rowEl.querySelector('.season-counts').value = parts.join(',');
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
/* 从粘贴的分篇表解析规则并填进指定番剧 */
function renderArcTargets() {
  const sel = $('#arc-target');
  if (!sel) return;
  sel.innerHTML = seasonRows
    .map((row) => `<option value="${esc(row.anime)}">${esc(row.anime)}（${row.count} 集）</option>`)
    .join('');
}

async function parseArcText() {
  const text = ($('#arc-text') || {}).value || '';
  const target = ($('#arc-target') || {}).value || '';
  const box = $('#arc-result');
  if (!text.trim()) { toastWarn('先把分篇文字贴进输入框'); return; }
  if (!target) { toastWarn('先打开一次按季整理，让它扫描出番剧'); return; }

  const button = $('#btn-arc-parse');
  const original = button ? button.innerHTML : '';
  if (button) { button.disabled = true; button.innerHTML = '<i class="bi bi-hourglass-split"></i> 解析中…'; }
  try {
    const resp = await api('/api/library/organize/parse-arcs', 'POST', { text });
    if (!resp.ok) { toastErr(resp.error); return; }
    const data = resp.data || {};
    const rules = data.rules || [];
    if (!rules.length) {
      if (box) box.textContent = '没认出任何「篇名 + 集数」，检查一下粘贴的内容';
      return;
    }
    const rowEl = $$('#season-rows .season-row').find((el) => el.dataset.anime === target);
    if (!rowEl) { toastErr('找不到这一部番'); return; }
    rowEl.querySelector('.season-counts').value =
      rules.map((rule) => `${rule.name}:${rule.count}`).join(',');
    updateSeasonPreview(rowEl);
    if (box) {
      box.textContent = `认出 ${rules.length} 段 · 合计 ${data.total} 集`
        + (data.skipped ? ` · 跳过 ${data.skipped} 条特典` : '');
    }
    toastOk(`已填入 ${rules.length} 段`);
  } catch (err) {
    toastErr(`解析失败：${err}`);
  } finally {
    if (button) { button.disabled = false; button.innerHTML = original; }
  }
}
/* ============================================================
   去重：同一部番的同一集有多份时只留一份

   判定是「番名 + 集号」，所以 第29集.mp4 / 第29集 (2).mp4 / 第29集.mkv
   都会被认成一组。保留体积最大的；体积相同留已归档（层级更深）的那份。
   默认只移动到 _重复/，删除要单独点。
   ============================================================ */

let dedupPlan = null;

async function openDedup() {
  showModal('modal-dedup');
  await loadDedupPlan();
}

async function loadDedupPlan() {
  const box = $('#dedup-list');
  const summary = $('#dedup-summary');
  if (!box) return;
  box.innerHTML = '<div class="org-empty">扫描中…</div>';
  summary.textContent = '扫描中…';
  $('#btn-dedup-move').disabled = true;
  $('#btn-dedup-delete').disabled = true;
  dedupPlan = null;

  const resp = await api('/api/library/dedup/plan', 'POST', {});
  if (!resp.ok) {
    box.innerHTML = `<div class="org-empty">扫描失败：${esc(resp.error)}</div>`;
    summary.textContent = '扫描失败';
    return;
  }
  dedupPlan = resp.data || {};
  renderDedup(dedupPlan);
}

function renderDedup(plan) {
  const box = $('#dedup-list');
  const stats = plan.stats || {};
  const groups = plan.groups || [];
  summary.textContent = stats.groups
    ? `${stats.groups} 组重复 · 多余 ${stats.duplicates} 个 · 可释放 ${fmtSize(stats.bytes)}`
    : `共 ${stats.files} 个视频，没有重复`;

  if (!groups.length) {
    box.innerHTML = '<div class="org-empty">没有发现重复剧集</div>';
    return;
  }

  box.innerHTML = groups.map((group) => `
    <div class="org-group">${esc(group.anime)} · 第${group.episode}集</div>
    <div class="org-row">
      <span class="to" title="${esc(group.keep.rel)}">保留 ${esc(group.keep.rel)}</span>
      <span class="text-dim mono" style="flex:0 0 auto;font-size:11px">${fmtSize(group.keep.size)}</span>
      <span class="tag sub">保留</span>
    </div>
    ${group.extras.map((extra) => `
      <div class="org-row skip">
        <span class="from" title="${esc(extra.rel)}">多余 ${esc(extra.rel)}</span>
        <span class="text-dim mono" style="flex:0 0 auto;font-size:11px">${fmtSize(extra.size)}</span>
        <span class="tag">多余</span>
      </div>`).join('')}
  `).join('');

  $('#btn-dedup-move').disabled = false;
  $('#btn-dedup-delete').disabled = false;
}

async function applyDedup(action) {
  if (!dedupPlan || !dedupPlan.stats.groups) return;
  const stats = dedupPlan.stats;
  if (action === 'delete') {
    if (!confirm(`直接删除 ${stats.duplicates} 个文件（${fmtSize(stats.bytes)}）？\n\n`
      + '删除不可恢复。不确定就先选「移到 _重复」。')) return;
  } else if (!confirm(`把 ${stats.duplicates} 个多余文件移到 _重复/？\n\n不会删除，随时能拿回来。`)) {
    return;
  }

  const button = $(action === 'delete' ? '#btn-dedup-delete' : '#btn-dedup-move');
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="bi bi-hourglass-split"></i> 处理中…';
  try {
    const resp = await api('/api/library/dedup/apply', 'POST', { action });
    if (!resp.ok) { toastErr(resp.error); return; }
    const data = resp.data || {};
    const done = (data.stats || {}).handled || 0;
    const failed = (data.stats || {}).failed || 0;
    if (failed) {
      toastWarn(`去重完成：处理 ${done} 个，失败 ${failed} 个（详情见日志）`);
    } else {
      toastOk(action === 'delete'
        ? `已删除 ${done} 个重复文件`
        : `已把 ${done} 个重复文件移到 _重复/`);
    }
    await loadDedupPlan();
    await loadLibrary();
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}
/* ============================================================
   删除整部番

   两种模式：只删记录（文件留着）/ 连文件一起删。
   「连文件一起删」需要二次确认，且后端会拒绝删除下载目录本身。
   ============================================================ */

let pendingAnimeDelete = '';

function openAnimeDelete(animeName) {
  if (!animeName) return;
  pendingAnimeDelete = animeName;
  $('#anime-delete-name').textContent = animeName;
  const item = (state.libraryItems || []).find((it) => it.anime_name === animeName);
  $('#anime-delete-info').textContent = item
    ? `本地 ${item.episode_count} 个文件 · ${fmtSize(item.size)}`
    : '';
  showModal('modal-anime-delete');
}

async function doAnimeDelete(deleteFile) {
  const animeName = pendingAnimeDelete;
  if (!animeName) return;
  if (deleteFile) {
    const typed = prompt(
      `连文件一起删会永久删除「${animeName}」的整个文件夹。\n\n`
      + '确认请输入番剧名：');
    if (typed === null) return;
    if (typed.trim() !== animeName) {
      toastWarn('输入的番剧名不一致，已取消');
      return;
    }
  }

  const button = $(deleteFile ? '#btn-anime-delete-files' : '#btn-anime-delete-records');
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="bi bi-hourglass-split"></i> 处理中…';
  try {
    const resp = await api('/api/library/delete-anime', 'POST', {
      anime_name: animeName,
      delete_file: !!deleteFile,
    });
    if (!resp.ok) { toastErr(resp.error); return; }
    const data = resp.data || {};
    if (deleteFile) {
      toastOk(`已删除「${animeName}」：${data.files} 个文件（${fmtSize(data.bytes)}）`);
    } else {
      toastOk(`已清掉「${animeName}」的记录（${data.history} 条历史、${data.tasks} 条任务），文件保留`);
    }
    hideModal('modal-anime-delete');
    pendingAnimeDelete = '';
    await loadLibrary();
  } catch (err) {
    toastErr(`删除失败：${err}`);
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}