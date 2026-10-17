/* ============================================================
   动漫下载机 · 字幕：搜索、选择、下载
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
let subtitleContext = { anime: '', episode: '', target: '' };

function openSubtitleDialog(animeName, episodeName) {
  const anime = animeName || (state.currentAnime && state.currentAnime.name) || '';
  const episode = episodeName || (state.currentEpisodes && state.currentEpisodes[0]
    ? (state.episodes[0] || {}).name : '') || '';
  subtitleContext = { anime, episode, target: '' };
  const info = $('#subtitle-target-info');
  if (info) {
    info.innerHTML = episode
      ? `将保存为：<code>${esc(anime)} / ${esc(episode)}.srt</code>（与视频同目录，播放器自动加载）`
      : `未指定剧集时会保存到下载目录下的 <code>字幕</code> 文件夹`;
  }
  const input = $('#subtitle-keyword');
  if (input) input.value = anime;
  const box = $('#subtitle-results');
  if (box) box.innerHTML = '<div class="text-dim" style="font-size:13px">搜索后选择要用的字幕包</div>';
  showModal('modal-subtitle');
  if (anime) runSubtitleSearch();
}

async function runSubtitleSearch() {
  const box = $('#subtitle-results');
  const keyword = ($('#subtitle-keyword').value || '').trim();
  const source = ($('#subtitle-source') && $('#subtitle-source').value) || 'assrt';
  if (!keyword) { toastWarn('请输入字幕关键字'); return; }
  box.innerHTML = '<div class="text-dim" style="font-size:13px">正在搜索…</div>';
  const resp = await api('/api/subtitle/search', 'POST', { keyword, source, limit: 20 });
  if (!resp.ok) { box.innerHTML = `<div class="text-danger">${esc(resp.error)}</div>`; return; }
  const results = resp.data.results || [];
  subtitleContext.results = results;
  if (!results.length) {
    const hint = source === 'opensub'
      ? '（opensubtitles 用英文名命中率更高，例如 one piece）'
      : '（可切换到 opensubtitles 源，用英文名再试）';
    box.innerHTML = `<div class="text-dim" style="font-size:13px">没有搜到字幕 ${esc(hint)}</div>`;
    return;
  }
  box.innerHTML = results.map((item, index) => `
    <div class="sub-entry" data-index="${index}"
         style="border:1px solid var(--line);border-radius:6px;padding:9px 12px;margin-bottom:6px;cursor:pointer">
      <div class="d-flex align-items-center gap-2">
        <span class="badge badge-dim">${index + 1}</span>
        <b style="font-size:13.5px">${esc(item.title)}</b>
        ${item.lang_label ? `<span class="badge badge-soft">${esc(item.lang_label)}</span>` : ''}
        <span class="spacer" style="flex:1"></span>
        <span class="text-dim mono" style="font-size:11px">#${esc(item.id)}</span>
        <i class="bi bi-chevron-right text-dim"></i>
      </div>
    </div>`).join('');
  box.querySelectorAll('.sub-entry').forEach((node) => {
    node.addEventListener('click', () => showSubtitleFiles(results[Number(node.dataset.index)]));
  });
}

async function showSubtitleFiles(entry) {
  const box = $('#subtitle-results');
  if (!entry) return;
  const title = entry.title || '';
  box.innerHTML = `<div class="text-dim" style="font-size:13px">正在读取「${esc(title)}」的文件…</div>`;
  const resp = await api('/api/subtitle/files', 'POST', { entry });
  if (!resp.ok) { box.innerHTML = `<div class="text-danger">${esc(resp.error)}</div>`; return; }
  const files = resp.data.files || [];
  if (!files.length) {
    box.innerHTML = '<div class="text-dim" style="font-size:13px">这个条目里没有可直接下载的字幕文件</div>';
    return;
  }
  box.innerHTML = `
    <button class="btn btn-outline-light btn-sm mb-2" id="btn-subtitle-back">
      <i class="bi bi-arrow-left"></i> 返回搜索结果</button>
    <div class="text-dim mb-2" style="font-size:12.5px">${esc(title)} · 共 ${files.length} 个文件</div>
    ${files.map((f) => `
      <div class="d-flex align-items-center gap-2 mb-1 sub-file"
           style="border:1px solid var(--line);border-radius:6px;padding:8px 12px">
        <i class="bi bi-file-earmark-text text-dim"></i>
        <span class="mono" style="font-size:12.5px;word-break:break-all">${esc(f.name)}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn btn-primary btn-sm" data-url="${esc(f.url)}">下载</button>
      </div>`).join('')}`;
  $('#btn-subtitle-back').addEventListener('click', runSubtitleSearch);
  box.querySelectorAll('.sub-file button[data-url]').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      const payload = { url: button.dataset.url, lang: entry.lang || '' };
      if (subtitleContext.anime && subtitleContext.episode) {
        payload.anime_name = subtitleContext.anime;
        payload.episode_name = subtitleContext.episode;
      }
      const saved = await api('/api/subtitle/download', 'POST', payload);
      button.disabled = false;
      if (!saved.ok) { toastErr(saved.error); return; }
      toastOk(`字幕已保存：${saved.data.path.split(/[\\/]/).pop()}`);
    });
  });
}

/* 为多个剧集批量匹配字幕（按集号） */

async function downloadSubtitlesFor(animeName, episodes) {
  const list = (episodes || []).filter(Boolean);
  if (!list.length) { toastWarn('没有可匹配的剧集'); return; }
  const job = jobs.start(`批量字幕 · ${animeName}`, {
    total: list.length, message: '正在搜索字幕源（assrt → opensubtitles）…',
  });
  const resp = await api('/api/subtitle/auto', 'POST', {
    keyword: animeName, anime_name: animeName,
    source: 'all',
    episodes: list.map((name) => ({ name })),
  });
  if (!resp.ok) { jobs.fail(job, resp.error); return; }
  const d = resp.data;
  const parts = [`成功 ${d.saved.length} 集`];
  if (d.skipped.length) parts.push(`未匹配 ${d.skipped.length} 集`);
  if (d.failed.length) parts.push(`失败 ${d.failed.length} 集`);
  jobs.finish(job, `${parts.join('，')}（检查了 ${d.entries} 个字幕条目）`);
  if (d.saved.length) {
    showInfo('字幕下载结果', `
      <div class="mb-2 text-dim" style="font-size:12.5px">
        共检查 ${d.entries} 个字幕条目</div>
      ${d.saved.map((s) => `<div class="d-flex gap-2 align-items-center mb-1">
        <span class="led on"></span><b style="min-width:90px">${esc(s.episode)}</b>
        <span class="mono text-dim" style="font-size:11px">${esc(String(s.from).slice(0, 60))}</span>
      </div>`).join('')}
      ${d.skipped.length ? `<div class="mt-3 text-dim" style="font-size:12px">
        未匹配到字幕的剧集：${esc(d.skipped.join('、'))}</div>` : ''}`);
  }
}

/* ============================================================
   在线预览播放器
   ============================================================ */
