/* ============================================================
   动漫下载机 · 检索与剧集：聚合检索（含流式）、检索历史、剧集面板、批量下载
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
async function loadSearchHistory() {
  const resp = await api('/api/search/history');
  if (!resp.ok) return;
  const list = resp.data || [];
  // 历史只作为下方快捷标签展示，不预填到搜索框
  const chips = list.map((item) =>
    `<span class="chip" data-keyword="${esc(item.keyword)}">${esc(item.keyword)}</span>`);
  if (chips.length) {
    chips.push('<span class="chip chip-clear" data-clear-history '
      + 'title="清空检索历史"><i class="bi bi-trash3"></i> 清空历史</span>');
  }
  $('#search-history-chips').innerHTML = chips.join('');
}

/** 数值变化时让徽章弹一下（只在真的变了才动，避免每 6 秒刷新都在抖） */

async function quickDownloadAll(anime, button) {
  if (!anime || !anime.url) { toastWarn('这条结果缺少地址，先点开看看'); return; }
  if (button) button.disabled = true;
  const job = jobs.start(`整部下载 · ${anime.name}`, { total: 2, message: '正在解析剧集列表…' });
  try {
    const resp = await api('/api/episodes', 'POST', {
      source_id: anime.source_id, anime_url: anime.url, anime_name: anime.name,
    });
    if (!resp.ok) throw new Error(resp.error);
    const episodes = (resp.data.episodes || []);
    if (!episodes.length) {
      jobs.fail(job, '这个源没有解析出剧集');
      return;
    }
    jobs.set(job, 1, `解析到 ${episodes.length} 集，正在提交…`);
    const items = episodes.map((ep) => ({
      anime_name: anime.name,
      episode_name: ep.name,
      episode_url: ep.url,
      anime_url: anime.url,
      source_id: anime.source_id,
      source: anime.source,
    }));
    const created = await api('/api/download/batch', 'POST', { items });
    if (!created.ok) throw new Error(created.error);
    const count = (created.data || {}).created_count;
    jobs.finish(job, `已提交 ${count ?? items.length} 集到下载队列`);
    await refreshTasks(true);
    switchView('tasks');
  } catch (err) {
    jobs.fail(job, String(err.message || err));
  } finally {
    if (button) button.disabled = false;
  }
}

/* ---------------------------------------------------------- 清除用户配置 */

async function doSearch() {
  const keyword = $('#search-keyword').value.trim();
  if (!keyword) { toastWarn('请输入关键字'); return; }
  const enabled = state.sources.filter((s) => s.enabled);
  if (!enabled.length) { toastErr('没有勾选任何动漫源'); return; }

  // 开始新检索前，先关掉上一部的剧集面板：
  // 否则它会一直挂着，显示的是上一部动漫的剧集（很容易误当成新结果）
  closeEpisodePanel();

  const button = $('#btn-search');
  button.disabled = true;
  button.innerHTML = '<span class="spinner-border spinner-border-sm spinner-inline"></span> 检索中';
  $('#search-results').innerHTML = '<div class="empty"><i class="bi bi-hourglass-split"></i>正在按优先级检索…</div>';
  $('#search-summary').textContent = '检索中…';
  $('#search-elapsed').textContent = '';
  const searchJob = jobs.start(`聚合检索 · ${keyword}`, { total: enabled.length });
  jobs.log(searchJob, `开始检索「${keyword}」，并发请求 ${enabled.length} 个源`);

  // 走**流式**接口：每个源一返回就往作业面板写一行日志，
  // 而不是干等十几秒只看到一句「正在并发请求 N 个源…」（那时看起来像卡住了）。
  const searchMode = ($('#search-mode') && $('#search-mode').value) || 'aggregate';
  const payload = await streamSearch(keyword, searchMode, enabled, searchJob);
  state.lastSearchMethod = 'POST(stream)';

  button.disabled = false;
  button.innerHTML = '<i class="bi bi-search"></i> 检索';

  if (!payload) {
    $('#search-results').innerHTML =
      '<div class="empty"><i class="bi bi-x-octagon"></i>检索失败，详见右下角作业日志</div>';
    $('#search-summary').textContent = '检索失败';
    return;
  }
  state.search = payload;
  // 换了关键词/换了源，之前为某部番单独选过的源不再适用
  state.pickedSource = {};
  // 汇总行由 views.searchResults 统一设置（只有它知道合并成了多少部）；
  // 这里不要再赋值，否则会把「合并为 N 部」覆盖掉。
  views.searchResults(payload);
  $('#search-elapsed').textContent = `耗时 ${payload.elapsed || 0}s`;
  loadSearchHistory();
}

/** 流式检索：读 NDJSON，每来一个源就写一行作业日志；返回最终 payload（失败返回 null）。 */

async function streamSearch(keyword, mode, enabled, job) {
  const t0 = Date.now();
  try {
    const resp = await fetch('/api/search/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keyword,
        mode: mode === 'single' ? 'single' : 'aggregate',
        source_ids: enabled.map((s) => s.id),
      }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = null;
    let failMsg = null;
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let obj;
        try { obj = JSON.parse(line); } catch (e) { continue; }
        if (obj.type === 'start') {
          jobs.set(job, 0, null);
        } else if (obj.type === 'source') {
          jobs.set(job, obj.done, null);
          const name = obj.source || '未知源';
          if (!obj.ok) {
            jobs.log(job, `${name}：检索失败 — ${obj.error || '未知错误'}`, 'err');
          } else if (obj.empty || !obj.count) {
            jobs.log(job, `${name}：没有匹配结果（${obj.elapsed}s）`, 'warn');
          } else {
            jobs.log(job, `${name}：命中 ${obj.count} 条（${obj.elapsed}s）`, 'ok');
          }
        } else if (obj.type === 'error') {
          failMsg = obj.error || '检索失败';
        } else if (obj.type === 'done') {
          done = obj.payload || {};
        }
      }
    }
    if (!done) throw new Error(failMsg || '没有收到完整结果');
    done.elapsed = ((Date.now() - t0) / 1000).toFixed(2);

    const groupList = done.groups || [];
    const okSources = groupList.filter((g) => g.ok && g.count).length;
    const emptySources = groupList.filter((g) => g.ok && !g.count).length;
    const failedSources = (done.errors || []).length;
    jobs.log(job,
      `检索结束：${okSources} 个源有结果、${emptySources} 个源无匹配、`
      + `${failedSources} 个源失败，合计 ${done.total} 条`,
      failedSources && !okSources ? 'err' : 'ok');
    jobs.finish(job, `命中 ${done.total} 条 · ${done.source_count} 个源 · ${done.elapsed}s`);
    return done;
  } catch (err) {
    jobs.log(job, `检索中断：${err.message || err}`, 'err');
    jobs.fail(job, String(err.message || err));
    toastErr('检索失败：' + (err.message || err));
    return null;
  }
}

async function toggleAllSearchSources(enabled) {
  state.sources.forEach((s) => { s.enabled = enabled; });
  const resp = await persistSourceState();
  if (!resp.ok) toastErr(resp.error);
  await loadSources();
}

function showSearchHistory() {
  api('/api/search/history').then((resp) => {
    if (!resp.ok) return;
    const list = resp.data || [];
    if (!list.length) { toastWarn('暂无检索历史'); return; }
    showInfo('检索历史', `<div class="chip-bar">${list.map((item) =>
      `<span class="chip" data-keyword="${esc(item.keyword)}">${esc(item.keyword)}</span>`).join('')}</div>
      <div class="text-dim mt-3" style="font-size:12px">共 ${list.length} 条记录，点击即可重新检索</div>`);
    $$('#modal-info-body .chip').forEach((chip) => chip.addEventListener('click', () => {
      $('#search-keyword').value = chip.dataset.keyword;
      hideModal('modal-info');
      doSearch();
    }));
  });
}

/* ============================================================
   剧集与下载
   ============================================================ */

function closeEpisodePanel() {
  const card = $('#episode-card');
  if (card) card.style.display = 'none';
  const list = $('#episode-list');
  if (list) list.innerHTML = '';
  const count = $('#episode-count');
  if (count) count.textContent = '';
  const name = $('#episode-anime-name');
  if (name) name.textContent = '剧集';
  const source = $('#episode-source');
  if (source) source.textContent = '';
  state.episodes = [];
  state.currentEpisodes = [];
  state.currentAnime = null;
}

/** 渲染剧集卡片里的「下载源」选择器。
 *  只选一个源：切换即按新源重新取剧集列表，下载也只从它解析。
 *  刻意不做自动回退 —— 换源会换到不同清晰度/字幕，静默换源比报错更难排查。 */

function renderEpisodeSourcePicker(currentSourceId) {
  const box = $('#episode-source-picker');
  if (!box) return;
  const list = (state.sources || []).filter((s) => s.enabled);
  if (!list.length) {
    box.innerHTML = '<span class="text-dim" style="font-size:12px">没有启用的源</span>';
    return;
  }
  box.innerHTML = list.map((s) => {
    const led = s.last_error ? '<span class="led err"></span>'
      : (s.ok_count ? '<span class="led on"></span>' : '<span class="led off"></span>');
    const active = Number(s.id) === Number(currentSourceId);
    return `<span class="ep-source-pill ${active ? 'active' : ''}" data-id="${s.id}"
                  title="${esc(s.note || '')}质量分 ${Number(s.quality || 0).toFixed(0)}">${led}${esc(s.name)}</span>`;
  }).join('');
  const hint = $('#episode-source-hint');
  if (hint) {
    const cur = list.find((s) => Number(s.id) === Number(currentSourceId));
    hint.textContent = cur ? `剧集来自「${cur.name}」；失败不会自动换源` : '';
  }
}

/** 切换下载源：按新源重新拉一次剧集列表（同一部动漫在不同源上集数可能不同）。 */

async function switchEpisodeSource(sourceId) {
  const picker = $('#episode-source-picker');
  const log = [];
  const trace = (msg) => { log.push(msg); console.debug('[AnimeDownloader] 换源:', msg); };
  if (picker) picker.style.pointerEvents = 'none';
  try {
    const cur = state.currentAnime || {};
    const src = (state.sources || []).find((s) => Number(s.id) === Number(sourceId));
    trace(`目标 id=${sourceId} 解析到源=${src ? src.name : '(未找到)'} 番剧=${cur.name}`);
    if (!src) { toastErr('找不到这个源，请刷新页面重试'); return; }
    // 换源后要重新检索这部番剧在该源上的条目地址，不能沿用旧源的 url
    const keyword = cur.name || '';
    toastOk(`正在从「${src.name}」查找「${keyword}」…`);
    const found = await api('/api/search', 'POST', {
      keyword, mode: 'single', source_ids: [Number(sourceId)],
    });
    trace(`检索 ok=${found.ok} 错误=${found.error || '无'}`);
    if (!found.ok) { toastErr(found.error); return; }
    const groups = (found.data && found.data.groups) || [];
    const hit = groups.find((g) => (g.results || []).length) || null;
    trace(`分组 ${groups.length} 个，有结果的 ${hit ? 1 : 0} 个`);
    if (!hit || !hit.results.length) {
      toastErr(`「${src.name}」没有找到「${keyword}」，换个源试试`);
      return;
    }
    // 选最接近原标题的一条（名字完全相同优先，否则第一条）
    const exact = hit.results.find((r) => r.name === keyword) || hit.results[0];
    trace(`命中「${exact.name}」`);
    await openEpisodes({
      name: exact.name || keyword,
      url: exact.url,
      source_id: Number(sourceId),
      source: src.name,
      cover: exact.cover || '',
      playable: exact.playable,
    });
    trace('已刷新剧集列表');
  } catch (err) {
    console.error('[AnimeDownloader] 换源异常：', err);
    toastErr('换源失败：' + (err.message || err));
    trace('异常: ' + (err.message || err));
  } finally {
    if (picker) picker.style.pointerEvents = '';
    window.__lastSwitch = log;
  }
}

async function openEpisodes(anime) {
  state.currentAnime = anime;
  state.lastSlot = anime;
  $('#episode-card').style.display = '';
  $('#episode-anime-name').textContent = anime.name;
  $('#episode-source').textContent = anime.source || '';
  $('#episode-list').innerHTML = '<div class="text-dim">正在获取剧集…</div>';
  $('#episode-count').textContent = '';
  renderEpisodeSourcePicker(anime.source_id);
  $('#episode-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  const resp = await api('/api/episodes', 'POST', {
    source_id: anime.source_id, anime_url: anime.url, anime_name: anime.name,
  });
  if (!resp.ok) {
    console.warn('[AnimeDownloader] 取剧集失败', { anime, error: resp.error });
    $('#episode-list').innerHTML = `<div class="text-danger">获取剧集失败：${esc(resp.error)}</div>`;
    toastErr(resp.error);
    return;
  }
  state.episodes = resp.data.episodes || [];
  state.currentEpisodes = state.episodes;
  views.episodes({ ...resp.data, episodes: state.episodes });
}

async function downloadEpisodes(all) {
  const anime = state.currentAnime;
  state.currentEpisodes = state.episodes;

  // 自愈：状态丢失时不报“缺少源或动漫地址”把人卡住，而是直接重取剧集列表
  if (!anime || !anime.url || anime.source_id === null || anime.source_id === undefined
      || Number.isNaN(Number(anime.source_id))) {
    const remembered = state.lastSlot;
    if (remembered) {
      console.warn('[AnimeDownloader] currentAnime 状态缺失，用最近点击的条目重建：', remembered);
      await openEpisodes(remembered);
      const retry = state.currentAnime;
      if (retry && retry.url && retry.source_id !== undefined) {
        return downloadEpisodes(all);
      }
    }
    toastErr('无法确定要下载哪一部（请重新点击结果列表里的条目）');
    return;
  }

  // 不做任何"已下载"过滤：勾选了什么就下载什么（同一集重复下载会覆盖原文件）
  let picked = state.episodes.map((ep, index) => ({ ep, index }));
  if (!all) {
    picked = picked.filter(({ index }) => {
      const input = $(`.ep-check[data-index="${index}"]`);
      return input && input.checked;
    });
  }
  if (!picked.length) { toastWarn('没有勾选任何剧集'); return; }

  const items = picked.map(({ ep }) => ({
    anime_name: anime.name,
    episode_name: ep.name,
    episode_url: ep.url,
    anime_url: anime.url,
    source_id: anime.source_id,
    source: anime.source,
  }));
  // 失败是否自动换源：默认关闭——用户在剧集卡片选了哪个源就只从哪个源下
  const fallbackBox = $('#ep-auto-fallback');
  const autoFallback = Boolean(fallbackBox && fallbackBox.checked);
  const resp = await api('/api/download/batch', 'POST', {
    items, auto_fallback: autoFallback,
  });
  if (!resp.ok) { toastErr(resp.error); return; }
  const data = resp.data;
  toastOk(`已加入 ${data.created_count} 个下载任务`
    + (data.skipped_count ? `，跳过 ${data.skipped_count} 集` : '')
    + (autoFallback ? '（失败会自动换源）' : `（只用「${anime.source || '当前源'}」）`));
  if (data.failed && data.failed.length) toastWarn(`${data.failed.length} 个任务创建失败`);
  refreshTasks();
}

/* ============================================================
   源管理
   ============================================================ */
