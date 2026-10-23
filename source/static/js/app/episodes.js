/* ============================================================
   集数校验

   问 B 站要官方集数，和本地已下载的集号对比，指出缺哪几集。
   只做「查询 + 展示」，不下载、不改文件。
   ============================================================ */

const MISSING_SHOW_LIMIT = 30;

async function checkEpisodeCount(button, seasonId) {
  const anime = button.dataset.anime;
  const card = button.closest('.lib-anime');
  const box = card ? card.querySelector('.lib-check') : null;
  if (!box) return;

  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="bi bi-hourglass-split"></i> 查询中…';
  box.hidden = false;
  box.innerHTML = '<div class="line dim"><i class="bi bi-hourglass-split"></i> 正在查询 B 站…</div>';

  try {
    const payload = { anime_name: anime };
    if (seasonId) payload.season_id = seasonId;
    const resp = await api('/api/library/bili/check', 'POST', payload);
    if (!resp.ok) {
      box.innerHTML = `<div class="line warn"><i class="bi bi-exclamation-triangle"></i> ${esc(resp.error)}</div>`;
      return;
    }
    box.innerHTML = renderEpisodeCheck(resp.data);
  } catch (err) {
    box.innerHTML = `<div class="line warn"><i class="bi bi-exclamation-triangle"></i> 查询失败：${esc(err)}</div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

function renderEpisodeCheck(data, label) {
  const who = label || '本地';
  if (!data || !data.found) {
    const reason = (data && data.reason) || 'B 站没有搜到这部番';
    return `<div class="line dim"><i class="bi bi-question-circle"></i> ${esc(reason)}</div>`;
  }

  const match = data.match || {};
  const total = match.total || 0;
  const seasonTitle = match.season_title && match.season_title !== match.title
    ? `（${esc(match.season_title)}）` : '';
  const head = `<div class="line">`
    + `<span class="sm">B站：<a href="${esc(match.url || '')}" target="_blank" rel="noopener">`
    + `${esc(match.title || '')}</a>${seasonTitle} · 官方 ${total} 集</span>`
    + `</div>`;

  const lines = [];
  if (data.complete) {
    lines.push(`<div class="line ok"><i class="bi bi-check-circle"></i> `
      + `${who} ${data.local_in_range} 集，与官方一致</div>`);
  } else if (!data.local_in_range) {
    lines.push(`<div class="line dim"><i class="bi bi-info-circle"></i> `
      + `${who}没有可识别的集号，无法比对</div>`);
  } else {
    lines.push(`<div class="line warn"><i class="bi bi-exclamation-triangle"></i> `
      + `${who} ${data.local_in_range} / 官方 ${total} 集，缺 ${data.missing.length} 集</div>`);
    const shown = data.missing.slice(0, MISSING_SHOW_LIMIT)
      .map((n) => `第${n}集`).join('、');
    const more = data.missing.length > MISSING_SHOW_LIMIT
      ? ` …（共 ${data.missing.length} 集）` : '';
    lines.push(`<div class="nums">缺：${shown}${more}</div>`);
  }

  if (data.beyond && data.beyond.length) {
    const shown = data.beyond.slice(0, 8).join('、');
    const more = data.beyond.length > 8 ? '…' : '';
    lines.push(`<div class="line dim">${who}另有 ${data.beyond.length} 集超出官方集数`
      + `（${shown}${more}），可能是后续季或特别篇</div>`);
  }

  const others = (data.candidates || []).filter((c) => c.season_id !== match.season_id);
  const picks = others.length
    ? `<div class="season-picks">${others.slice(0, 5).map((c) => `
        <button class="season-pick" data-act="bili-season" data-season="${c.season_id}"
                title="换成这个条目重新比对">${esc(c.title)}${c.index_show ? ' · ' + esc(c.index_show) : ''}</button>`
      ).join('')}</div>`
    : '';

  return head + lines.join('') + picks;
}

/* ---------------- 剧集面板里的校验（还没下载的番）---------------- */

async function checkPanelEpisodes(seasonId) {
  const anime = state.currentAnime;
  const box = $('#episode-check');
  const button = $('#btn-episode-check');
  if (!anime || !box || !button) {
    toastWarn('先点开一部番的剧集面板');
    return;
  }
  const names = (state.episodes || []).map((ep) => ep && ep.name).filter(Boolean);
  if (!names.length) {
    toastWarn('这个源没有给出剧集');
    return;
  }

  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<i class="bi bi-hourglass-split"></i> 查询中…';
  box.hidden = false;
  box.innerHTML = '<div class="line dim"><i class="bi bi-hourglass-split"></i> 正在查询 B 站…</div>';

  try {
    const payload = { anime_name: anime.name, episode_names: names };
    if (seasonId) payload.season_id = seasonId;
    const resp = await api('/api/library/bili/check', 'POST', payload);
    if (!resp.ok) {
      box.innerHTML = `<div class="line warn"><i class="bi bi-exclamation-triangle"></i> ${esc(resp.error)}</div>`;
      return;
    }
    box.innerHTML = renderEpisodeCheck(resp.data, '该源');
  } catch (err) {
    box.innerHTML = `<div class="line warn"><i class="bi bi-exclamation-triangle"></i> 查询失败：${esc(err)}</div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}
