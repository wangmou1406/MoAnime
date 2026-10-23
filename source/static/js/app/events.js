/* ============================================================
   动漫下载机 · 全局事件绑定
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
/*
 * installEvents() 目录 —— 这一个函数把全站的事件绑定集中在一处，
 * 方便回答「这个按钮是谁在处理」。按视图分成若干段：
 *
 *   L10   检索           搜索框、检索按钮、源选择、结果行
 *   L100  剧集预览       剧集面板里的预览图 / 在线播放 / 勾选
 *   L160  源管理         列表拖动、排序按钮、启停、测活、增删改
 *   L290  源编辑弹窗     表单提交、测试、JSON 应用
 *   L310  任务           分组头（暂停/继续/全部重试/删除全部）、行内操作
 *   L495  媒体库         刷新、清理、逐集预览/删除
 *   L545  设置           保存、恢复默认、背景图、浏览器取流
 *   L615  文件夹选择     目录浏览弹窗
 *   L636  日志           自动滚动、清空、级别过滤
 *
 * 说明：行号是拆分时的近似值，搜索「---------------- 段名 ----------------」即可定位。
 */

function installEvents() {
  /* ---------------- 检索 ---------------- */
  $('#btn-search').addEventListener('click', doSearch);
  $('#search-keyword').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
  $('#search-source-picker').addEventListener('click', async (e) => {
    const pill = e.target.closest('.source-pill');
    if (!pill) return;
    e.preventDefault();
    const id = Number(pill.dataset.id);
    const source = state.sources.find((s) => s.id === id);
    if (!source) return;
    source.enabled = !source.enabled;
    pill.classList.toggle('checked', source.enabled);
    const pick = pill.querySelector('.src-pick');
    if (pick) pick.checked = source.enabled;
    pill.title = `${source.note || ''}${source.enabled ? '' : '（未启用，点击启用）'}`;
    await persistSourceState();
    views.sources(state.sources);
  });
  $('#btn-select-all-sources').addEventListener('click', () => toggleAllSearchSources(true));
  $('#btn-select-none-sources').addEventListener('click', () => toggleAllSearchSources(false));
  $('#search-history-chips').addEventListener('click', async (e) => {
    if (e.target.closest('[data-clear-history]')) {
      if (!confirm('清空全部检索历史？')) return;
      const resp = await api('/api/search/history', 'DELETE');
      if (!resp.ok) { toastErr(resp.error); return; }
      toastOk('已清空检索历史');
      await loadSearchHistory();
      return;
    }
    const chip = e.target.closest('.chip');
    if (!chip || !chip.dataset.keyword) return;
    $('#search-keyword').value = chip.dataset.keyword;
    doSearch();
  });
  $('#btn-search-history').addEventListener('click', showSearchHistory);

  $('#search-results').addEventListener('click', (e) => {
    const slot = e.target.closest('.slot');
    if (!slot) return;
    const anime = {
      name: slot.dataset.name,
      url: slot.dataset.url,
      cover: slot.dataset.cover,
      source_id: Number(slot.dataset.sourceId),
      source: slot.dataset.source,
    };
    // 行内的源标签：先切到那个源（更新这一行的下载来源），再打开剧集
    const chip = e.target.closest('.src-chip');
    if (chip && !chip.classList.contains('static')) {
      e.preventDefault();
      e.stopPropagation();
      const id = Number(chip.dataset.sourceId);
      const url = chip.dataset.url || anime.url;
      state.pickedSource = state.pickedSource || {};
      state.pickedSource[anime.name] = id;
      // 重渲染列表，让这一行的源标签顺序与高亮同步
      views.searchResults(state.search || {});
      // 接着按新选的源打开剧集（相当于自动点一下这一行）
      openEpisodes({ ...anime, url, source_id: id, source: chip.textContent.trim() });
      return;
    }
    // 「直链可下载」按钮：直接把这部的全部剧集加入队列，不用先打开剧集面板
    if (e.target.closest('[data-act="quick-download"]')) {
      e.preventDefault();
      e.stopPropagation();
      quickDownloadAll(anime, e.target.closest('[data-act="quick-download"]'));
      return;
    }
    openEpisodes(anime);
  });

  $('#btn-episode-clear').addEventListener('click', closeEpisodePanel);
  $('#btn-invert-eps').addEventListener('click', () => {
    $$('#episode-list .ep-check').forEach((input) => {
      input.checked = !input.checked;
      // 剧集行是 .ep-row（曾两次因为选择器写错导致反选/点击失效，改名时务必同步这里）
      const row = input.closest('.ep-row');
      if (row) row.classList.toggle('checked', input.checked);
    });
  });
  $('#btn-download-all-eps').addEventListener('click', () => downloadEpisodes(true));
  $('#btn-download-selected-eps').addEventListener('click', () => downloadEpisodes(false));
  const subtitleAuto = $('#btn-subtitle-auto');
  if (subtitleAuto) subtitleAuto.addEventListener('click', () => openSubtitleDialog());
  const durationCheck = $('#btn-duration-check');
  if (durationCheck) durationCheck.addEventListener('click', runAdCheck);
  const episodeCheck = $('#btn-episode-check');
  if (episodeCheck) {
    episodeCheck.addEventListener('click', () => checkPanelEpisodes());
    const box = $('#episode-check');
    if (box) {
      // 结果区里的「换个条目」按钮
      box.addEventListener('click', (e) => {
        const pick = e.target.closest('[data-act="bili-season"]');
        if (pick) {
          e.preventDefault();
          checkPanelEpisodes(Number(pick.dataset.season));
        }
      });
    }
  }

  /* ---------------- 剧集预览（图片 + 在线播放） ---------------- */
  $('#episode-list').addEventListener('click', (e) => {
    const play = e.target.closest('.ep-play-btn');
    if (!play) return;
    e.preventDefault();
    e.stopPropagation();
    openPlayer(Number(play.dataset.play));
  });
  const playerModal = document.getElementById('player-modal');
  if (playerModal) {
    // 关掉弹窗就停止播放并释放 HLS 实例，避免后台一直拉流
    playerModal.addEventListener('hidden.bs.modal', closePlayer);
  }
  const playerReload = $('#btn-player-reload');
  if (playerReload) {
    playerReload.addEventListener('click', () => {
      if (playerState.index === undefined || playerState.index < 0) return;
      openPlayer(playerState.index);
    });
  }
  const copyUrl = $('#btn-player-copy-url');
  if (copyUrl) {
    copyUrl.addEventListener('click', async () => {
      if (!playerState.url) { toastWarn('还没有解析出地址'); return; }
      try {
        await navigator.clipboard.writeText(playerState.url);
        toastOk('视频直链已复制');
      } catch (err) {
        // 剪贴板不可用时退回到弹窗展示，方便手动复制
        showInfo('视频直链', `<div class="mono" style="word-break:break-all;font-size:12px">${esc(playerState.url)}</div>`);
      }
    });
  }
  const playerDownload = $('#btn-player-download');
  if (playerDownload) {
    playerDownload.addEventListener('click', async () => {
      const ep = state.episodes[playerState.index];
      const anime = state.currentAnime;
      if (!ep || !anime) return;
      playerDownload.disabled = true;
      const resp = await api('/api/download/episode', 'POST', {
        source_id: anime.source_id, episode_url: ep.url, anime_url: anime.url,
        anime_name: anime.name, episode_name: ep.name, source: anime.source,
      });
      playerDownload.disabled = false;
      if (!resp.ok) { toastErr(resp.error); return; }
      toastOk(`已加入下载队列：${ep.name}`);
      refreshTasks(true);
    });
  }
  const subtitleSearch = $('#btn-subtitle-search');
  if (subtitleSearch) subtitleSearch.addEventListener('click', runSubtitleSearch);
  const subtitleInput = $('#subtitle-keyword');
  if (subtitleInput) {
    subtitleInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSubtitleSearch(); });
  }

  /* ---------------- 源管理 ---------------- */
  $('#btn-source-add').addEventListener('click', () => openSourceEditor(null));
  $('#btn-source-wizard').addEventListener('click', openDetectWizard);
  $('#btn-source-import').addEventListener('click', openImportDialog);
  $('#btn-source-export').addEventListener('click', () => {
    window.location.href = '/api/sources/export?download=1';
  });
  $('#btn-source-check-all').addEventListener('click', checkAllSources);
  const demoteAds = $('#btn-source-demote-ads');
  if (demoteAds) {
    demoteAds.addEventListener('click', async () => {
      const resp = await api('/api/sources/demote-ads', 'POST', {});
      if (!resp.ok) { toastErr(resp.error); return; }
      state.sources = resp.data.sources || state.sources;
      views.sources(state.sources);
      views.searchSourcePicker(state.sources);
      initSortable();
      const n = resp.data.moved || 0;
      toastOk(n ? `已把 ${n} 个带广告的源移到最后` : '还没有标记任何源为「含广告」');
    });
  }
  $('#btn-source-save-order').addEventListener('click', async () => {
    const resp = await persistSourceState();
    if (resp.ok) toastOk('优先级顺序与勾选状态已保存');
    else toastErr(resp.error);
    await loadSources();
  });

  /* 一键排序：把服务端算好的顺序落库，并刷新列表 */
  const sortGroup = $('#source-sort-group');
  if (sortGroup) {
    sortGroup.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-sort]');
      if (!btn) return;
      const mode = btn.dataset.sort;
      btn.disabled = true;
      try {
        const resp = await api('/api/sources/sort', 'POST', { mode });
        if (!resp.ok) { toastErr(resp.error); return; }
        state.sourceSortMode = mode;
        state.sources = resp.data.sources || state.sources;
        renderSourcesAndPicker();
        markActiveSort(mode);
        const labels = {
          smart: '智能排序（按质量分）', health: '可用优先', hits: '命中最多',
          enabled: '启用优先', name: '按名称', ad: '广告置顶', manual: '默认顺序',
        };
        toastOk(`已按「${labels[mode] || mode}」重排 ${resp.data.count || 0} 个源`);
      } finally {
        btn.disabled = false;
      }
    });
  }

  /* 质量采样：并发测活 + 抽样检索，把耗时/命中数写进库，供智能排序使用 */
  const probeBtn = $('#btn-source-probe');
  if (probeBtn) {
    probeBtn.addEventListener('click', () => runQualityProbe(probeBtn));
  }

  /* 下载源选择：点一下就换源并重取剧集列表 */
  const epPicker = $('#episode-source-picker');
  if (epPicker) {
    epPicker.addEventListener('click', (e) => {
      const pill = e.target.closest('.ep-source-pill');
      if (!pill) return;
      const id = Number(pill.dataset.id);
      if (state.currentAnime && Number(state.currentAnime.source_id) === id) return;
      switchEpisodeSource(id);
    });
  }

  $('#source-list').addEventListener('click', async (e) => {
    // 注意：渲染出来的行 class 是 .rack-row（以前这里错写成 .source-row，
    // 导致整行的测试/测活/编辑/复制/删除按钮全都点不漫）
    const row = e.target.closest('.rack-row');
    if (!row) return;
    const id = Number(row.dataset.id);
    const button = e.target.closest('button[data-act]');
    if (!button) return;
    const act = button.dataset.act;
    if (act === 'up' || act === 'down') {
      await moveSource(id, act === 'up' ? -1 : 1);
      return;
    }
    if (act === 'ad-flag') {
      const source = state.sources.find((s) => s.id === id);
      const next = !(source && source.ad_flag);
      const resp = await api(`/api/sources/${id}/ad-flag`, 'POST', { ad_flag: next });
      if (!resp.ok) { toastErr(resp.error); return; }
      state.sources = resp.data.sources || state.sources;
      views.sources(state.sources);
      views.searchSourcePicker(state.sources);
      initSortable();
      toastOk(next ? `已标记「${source ? source.name : id}」含广告，并排到最后`
                   : `已取消「${source ? source.name : id}」的广告标记`);
      return;
    }
    if (act === 'edit') {
      openSourceEditor(id);
    } else if (act === 'test') {
      testSource(id, true);
    } else if (act === 'check') {
      button.disabled = true;
      const resp = await api(`/api/sources/${id}/check`, 'GET');
      button.disabled = false;
      const node = resp.data || {};
      if (resp.ok && node.ok) toastOk(`测活成功（HTTP ${node.status}，${node.elapsed}s）`);
      else toastErr('测活失败：' + (node.error || resp.error));
      await loadSources();
    } else if (act === 'copy') {
      const source = state.sources.find((s) => s.id === id);
      if (source) openSourceEditor(null, { ...source, name: `${source.name}-副本` });
    } else if (act === 'delete') {
      const nameNode = row.querySelector('.name');
      if (!confirm(`确定删除动漫源「${nameNode ? nameNode.textContent.trim() : id}」？`)) return;
      const resp = await api(`/api/sources/${id}`, 'DELETE');
      if (resp.ok) { toastOk('已删除'); await loadSources(); } else toastErr(resp.error);
    }
  });

  $('#source-list').addEventListener('change', async (e) => {
    const toggle = e.target.closest('.src-toggle');
    if (!toggle) return;
    const id = Number(toggle.dataset.id);
    const source = state.sources.find((s) => s.id === id);
    if (!source) return;
    source.enabled = toggle.checked;
    const row = toggle.closest('.rack-row');
    if (row) row.classList.toggle('disabled', !toggle.checked);
    const resp = await persistSourceState();
    if (!resp.ok) toastErr(resp.error);
    views.searchSourcePicker(state.sources);
  });

  /* ---------------- 源编辑弹窗 ---------------- */
  $('#src-type').addEventListener('change', updateTypeDescription);
  $('#btn-source-save').addEventListener('click', saveSource);
  $('#btn-src-test').addEventListener('click', () => previewDraft('search'));
  $('#btn-src-test-episodes').addEventListener('click', () => previewDraft('episodes'));
  $('#btn-src-test-video').addEventListener('click', () => previewDraft('video'));
  $('#btn-src-json-format').addEventListener('click', () => {
    try { $('#ad-json').value = jsonPretty(JSON.parse($('#ad-json').value || '{}')); }
    catch (err) { toastErr(err.message); }
  });
  $('#btn-src-json-apply').addEventListener('click', () => {
    try {
      const config = JSON.parse($('#ad-json').value || '{}');
      fillSourceForm({ config });
      toastOk('已用 JSON 覆盖表单');
    } catch (err) { toastErr('JSON 解析失败：' + err.message); }
  });

  /* ---------------- 任务 ---------------- */
  $('#task-filter').addEventListener('change', (e) => { state.taskFilter = e.target.value; refreshTasks(); });
  $('#btn-task-refresh').addEventListener('click', () => refreshTasks());
  $('#btn-task-pause-all').addEventListener('click', () => bulkTask('pause_all'));
  $('#btn-task-resume-all').addEventListener('click', () => bulkTask('resume_all'));
  const retryAllTasks = $('#btn-task-retry-all');
  if (retryAllTasks) {
    retryAllTasks.addEventListener('click', async () => {
      const ids = state.tasks
        .filter((t) => ['failed', 'canceled'].includes(t.status))
        .map((t) => t.id);
      if (!ids.length) { toastWarn('没有失败或已取消的任务'); return; }
      retryAllTasks.disabled = true;
      const resp = await api('/api/tasks/bulk', 'POST', { action: 'retry', ids });
      retryAllTasks.disabled = false;
      if (!resp.ok) { toastErr(resp.error || '重试失败'); return; }
      const d = resp.data || {};
      toastOk(`已重新提交 ${d.count} 个任务`
        + (d.skipped ? `，${d.skipped} 个跳过` : ''));
      await refreshTasks();
    });
  }
  $('#btn-task-clear').addEventListener('click', async () => {
    if (!confirm('清理所有已完成 / 失败 / 已取消的任务记录？')) return;
    await bulkTask('clear');
  });
  const clearPending = $('#btn-task-clear-pending');
  if (clearPending) {
    clearPending.addEventListener('click', async () => {
      const pending = state.tasks.filter((t) => t.status === 'queued' || t.status === 'paused').length;
      if (!confirm(`清空待下载队列？将取消并移除 ${pending} 个排队/暂停的任务（已下载的文件不会删除）`)) return;
      await bulkTask('clear_pending');
    });
  }
  /* 「⋯」维护菜单：点开/收起，点外面关掉 */
  const opsToggle = $('#btn-ops-toggle');
  const opsMenu = $('#ops-menu');
  if (opsToggle && opsMenu) {
    opsToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = opsMenu.classList.toggle('open');
      opsToggle.classList.toggle('active', open);
    });
    document.addEventListener('click', (e) => {
      if (!opsMenu.classList.contains('open')) return;
      if (opsMenu.contains(e.target) || e.target.closest('#btn-ops-toggle')) return;
      opsMenu.classList.remove('open');
      opsToggle.classList.remove('active');
    });
  }

  const deleteAllTasks = $('#btn-task-delete-all');  if (deleteAllTasks) {
    deleteAllTasks.addEventListener('click', async () => {
      const total = state.tasks.length;
      if (!total) { toastWarn('任务列表已经是空的'); return; }
      const pick = await askDeleteTasks({ name: '', tasks: state.tasks });
      if (!pick.ok) return;
      const resp = await api('/api/tasks/bulk', 'POST', {
        action: 'clear_all', delete_file: pick.deleteFile,
      });
      if (!resp.ok) { toastErr(resp.error || '删除失败'); return; }
      toastOk(`已删除 ${resp.data.count} 条任务记录`
        + (pick.deleteFile ? '（含视频文件）' : ''));
      await refreshTasks();
    });
  }
  const cleanDirs = $('#btn-task-clean-dirs');
  if (cleanDirs) {
    cleanDirs.addEventListener('click', async () => {
      const resp = await api('/api/tasks/bulk', 'POST', { action: 'clean_orphans' });
      if (!resp.ok) { toastErr(resp.error); return; }
      const d = resp.data || {};
      const dirs = await api('/api/tasks/bulk', 'POST', { action: 'clean_empty_dirs' });
      const parts = [];
      if (d.ts) parts.push(`${d.ts} 个旧 .ts`);
      if (d.part) parts.push(`${d.part} 个空断点文件`);
      if (dirs.ok && dirs.data.count) parts.push(`${dirs.data.count} 个空文件夹`);
      toastOk(parts.length ? `已清理 ${parts.join('、')}，释放 ${fmtSize(d.freed || 0)}` : '没有需要清理的残留文件');
      await loadLibrary();
    });
  }
  $('#btn-task-open-dir').addEventListener('click', openDownloadDir);

  $('#task-list').addEventListener('click', async (e) => {
    // 分组头：展开/收起（状态存在 state 里，刷新不会弹回）
    const toggle = e.target.closest('button[data-group-toggle]');
    if (toggle) {
      const group = e.target.closest('.task-group');
      const anime = group && group.dataset.anime;
      if (anime) {
        state.collapsedGroups = state.collapsedGroups || {};
        state.collapsedGroups[anime] = !state.collapsedGroups[anime];
        group.classList.toggle('collapsed', Boolean(state.collapsedGroups[anime]));
        toggle.innerHTML =
          `<i class="bi bi-chevron-${state.collapsedGroups[anime] ? 'right' : 'down'}"></i>`;
        toggle.title = state.collapsedGroups[anime] ? '展开' : '收起';
      }
      return;
    }
    // 分组头：暂停/继续/删除 整部番剧
    const groupButton = e.target.closest('button[data-group-act]');
    if (groupButton) {
      const group = e.target.closest('.task-group');
      const anime = group && group.dataset.anime;
      const act = groupButton.dataset.groupAct;
      const inGroup = state.tasks.filter((t) => (t.anime_name || '未命名') === anime);

      if (act === 'delete') {
        if (!inGroup.length) { toastWarn(`「${anime}」没有可删除的任务`); return; }
        const pick = await askDeleteTasks({ name: anime, tasks: inGroup });
        if (!pick.ok) return;
        groupButton.disabled = true;
        const resp = await api('/api/tasks/bulk', 'POST', {
          action: 'delete', ids: inGroup.map((t) => t.id), delete_file: pick.deleteFile,
        });
        groupButton.disabled = false;
        if (!resp.ok) { toastErr(resp.error || '删除失败'); return; }
        toastOk(`「${anime}」已删除 ${resp.data.count} 条记录`
          + (pick.deleteFile ? '（含视频文件）' : ''));
        await refreshTasks();
        return;
      }

      if (act === 'retry') {
        const retryIds = inGroup
          .filter((t) => ['failed', 'canceled'].includes(t.status))
          .map((t) => t.id);
        if (!retryIds.length) {
          toastWarn(`「${anime}」没有失败或已取消的任务可重试`);
          return;
        }
        groupButton.disabled = true;
        const resp = await api('/api/tasks/bulk', 'POST',
          { action: 'retry', ids: retryIds });
        groupButton.disabled = false;
        if (!resp.ok) { toastErr(resp.error || '重试失败'); return; }
        const d = resp.data || {};
        toastOk(`「${anime}」已重新提交 ${d.count} 个任务`
          + (d.skipped ? `，${d.skipped} 个跳过` : ''));
        await refreshTasks();
        return;
      }

      const targets = inGroup.filter((t) => (act === 'pause'
        ? ['running', 'queued'].includes(t.status) : t.status === 'paused'));
      if (!targets.length) { toastWarn(`「${anime}」没有可${act === 'pause' ? '暂停' : '继续'}的任务`); return; }
      groupButton.disabled = true;
      for (const task of targets) {
        await api(`/api/tasks/${task.id}/${act}`, 'POST');
      }
      groupButton.disabled = false;
      toastOk(`「${anime}」已${act === 'pause' ? '暂停' : '继续'} ${targets.length} 个任务`);
      await refreshTasks();
      return;
    }

    const button = e.target.closest('button[data-act]');
    const row = e.target.closest('.task-row');
    if (!button || !row) return;
    const id = row.dataset.id;
    const act = button.dataset.act;
    if (act === 'play') {
      const task = state.tasks.find((t) => t.id === id);
      if (task && task.save_path) openLocalPlayer(task.save_path, `${task.anime_name} ${task.episode_name}`);
      return;
    }
    if (act === 'delete') {
      const task = state.tasks.find((t) => t.id === id) || { id };
      const pick = await askDeleteTasks({
        name: [task.anime_name, task.episode_name].filter(Boolean).join(' '),
        tasks: [task],
      });
      if (!pick.ok) return;
      await api(`/api/tasks/${id}/delete`, 'POST', { delete_file: pick.deleteFile });
      await refreshTasks();
      return;
    }
    button.disabled = true;
    const resp = await api(`/api/tasks/${id}/${act}`, 'POST');
    button.disabled = false;
    if (!resp.ok) toastErr(resp.error || '操作失败');
    await refreshTasks();
  });

  /* ---------------- 媒体库 ---------------- */
  $('#btn-library-refresh').addEventListener('click', () => loadLibrary(true));
  const cleanLib = $('#btn-library-clean');
  if (cleanLib) {
    cleanLib.addEventListener('click', async () => {
      if (!confirm('清理「文件已被删除」的下载记录？\n（只删记录，不影响磁盘上的视频；清理后再点下载即可重新下载）')) return;
      const resp = await api('/api/library/clean-missing', 'POST', {});
      if (!resp.ok) { toastErr(resp.error); return; }
      toastOk(`已清理 ${resp.data.removed} 条失效记录`);
      await loadLibrary();
    });
  }
  $('#btn-library-open').addEventListener('click', openDownloadDir);

  /* 自动整理：扫描出计划 → 核对 → 执行 */
  const orgBtn = $('#btn-library-organize');
  if (orgBtn) {
    orgBtn.addEventListener('click', openOrganize);
    const orgRescan = $('#btn-organize-rescan');
    if (orgRescan) orgRescan.addEventListener('click', loadOrganizePlan);
    const orgScheme = $('#organize-scheme');
    if (orgScheme) orgScheme.addEventListener('change', loadOrganizePlan);
    const orgApply = $('#btn-organize-apply');
    if (orgApply) orgApply.addEventListener('click', applyOrganize);
  }
  $('#library-filter').addEventListener('input', debounce((e) => {
    const keyword = e.target.value.trim().toLowerCase();
    $$('#library-list .lib-anime').forEach((card) => {
      card.style.display = card.dataset.anime.toLowerCase().includes(keyword) ? '' : 'none';
    });
  }, 200));
  $('#library-list').addEventListener('click', async (e) => {
    // 点字幕小标签 → 在文件管理器里定位该字幕文件
    const chip = e.target.closest('[data-act="open-sub"]');
    if (chip) {
      e.preventDefault();
      e.stopPropagation();
      const resp = await api('/api/fs/reveal', 'POST', { path: chip.dataset.path });
      if (!resp.ok) toastErr(resp.error);
      else toastOk('已在文件管理器中定位字幕文件');
      return;
    }
    const button = e.target.closest('button[data-act]');
    if (!button) return;
    if (button.dataset.act === 'check-episodes') {
      e.preventDefault();
      e.stopPropagation();
      await checkEpisodeCount(button, null);
    } else if (button.dataset.act === 'bili-season') {
      e.preventDefault();
      e.stopPropagation();
      const card = button.closest('.lib-anime');
      const trigger = card ? card.querySelector('button[data-act="check-episodes"]') : null;
      if (trigger) await checkEpisodeCount(trigger, Number(button.dataset.season));
    } else if (button.dataset.act === 'play') {
      openLocalPlayer(button.dataset.path, button.dataset.name);
    } else if (button.dataset.act === 'subtitle') {
      e.preventDefault();
      // 找出这部番剧在库里的剧集名，逐个按集号匹配字幕
      const card = button.closest('.lib-anime');
      const names = card
        ? $$('.lib-ep .nm', card).map((node) => node.textContent.trim())
        : [];
      if (!names.length) { toastWarn('这部番剧没有已下载的剧集'); return; }
      await downloadSubtitlesFor(button.dataset.anime, names);
    } else if (button.dataset.act === 'delete') {
      if (!confirm('同时删除磁盘上的文件？此操作不可恢复')) return;
      const resp = await api('/api/library/delete', 'POST',
        { paths: [button.dataset.path], delete_file: true });
      if (resp.ok) { toastOk('已删除文件'); loadLibrary(); } else toastErr(resp.error);
    }
  });

  /* ---------------- 设置 ---------------- */
  $('#btn-settings-save').addEventListener('click', saveSettings);
  $('#btn-settings-reset').addEventListener('click', async () => {
    if (!confirm('恢复所有设置为默认值？')) return;
    const resp = await api('/api/settings/reset', 'POST');
    if (resp.ok) { toastOk('已恢复默认设置'); await loadSettings(); } else toastErr(resp.error);
  });
  $('#settings-form').addEventListener('click', async (e) => {
    // 浏览器取流：重新检测 / 清空嗅探缓存
    if (e.target.closest('#btn-browser-resolver-refresh')) {
      const d = await loadBrowserResolverStatus();
      if (!d) return;
      toastOk(d.usable ? '浏览器取流已就绪'
        : (d.enabled ? `不可用：${d.reason || '环境不满足'}` : '已在设置中关闭'));
      return;
    }
    if (e.target.closest('#btn-browser-resolver-clear')) {
      const resp = await api('/api/system/browser-resolver/clear-cache', 'POST');
      if (resp.ok) toastOk(`已清空 ${resp.data.cleared} 条嗅探缓存`);
      else toastErr(resp.error);
      await loadBrowserResolverStatus();
      return;
    }
    const picker = e.target.closest('[data-pick-folder]');
    if (picker) {
      state.folderPickTarget = picker.dataset.pickFolder;
      openFolderPicker(state.settings[picker.dataset.pickFolder] || '');
      return;
    }
    if (e.target.closest('#btn-clean-temp')) {
      const resp = await api('/api/settings/clean-temp', 'POST');
      if (resp.ok) toastOk(`已清理 ${resp.data.removed} 项，释放 ${fmtSize(resp.data.freed)}`);
      else toastErr(resp.error);
    } else if (e.target.closest('#btn-open-download-dir')) {
      openDownloadDir();
    } else if (e.target.closest('#btn-purge-tasks')) {
      const resp = await api('/api/settings/reset-tasks', 'POST');
      if (resp.ok) { toastOk(`已清理 ${resp.data.removed} 条未完成任务记录`); refreshTasks(); }
      else toastErr(resp.error);
    } else if (e.target.closest('#btn-factory-reset')) {
      await factoryReset();
    } else if (e.target.closest('#btn-export-sources')) {
      window.location.href = '/api/sources/export?download=1&include_builtin=1';
    } else if (e.target.closest('#btn-ffmpeg-refresh')) {
      loadFfmpegStatus();
    } else if (e.target.closest('#btn-ffmpeg-install')) {
      if (!confirm('将下载约 100oB 的 ffmpeg 到 tools 目录，继续？')) return;
      const resp = await api('/api/system/ffmpeg/install', 'POST');
      if (resp.ok) { toastOk(resp.data.message); loadFfmpegStatus(); }
      else toastErr(resp.error);
    }
  });
  $('#settings-form').addEventListener('change', (e) => {
    if (e.target.dataset.key === 'theme') applyTheme(e.target.value);
  });
  // 顶栏主题快捷切换（立即写盘，不用进设置页）
  const themeToggle = $('#btn-theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', async () => {
      const next = (state.settings.theme === 'dark') ? 'light' : 'dark';
      state.settings.theme = next;
      applyTheme(next);
      applyAppearance(state.settings);
      const resp = await api('/api/settings', 'POST', { theme: next });
      if (!resp.ok) { toastErr(resp.error); return; }
      state.settings = resp.data.settings || state.settings;
      const select = $('#set-theme');
      if (select) select.value = state.settings.theme;
      toastOk(next === 'dark' ? '已切换到暗色' : '已切换到亮色');
    });
  }
/* ---------------------------------------------------------- 文件夹选择 */
  $('#btn-folder-go').addEventListener('click', () => browseFolder($('#folder-path').value));
  $('#btn-folder-up').addEventListener('click', () => {
    const current = $('#folder-path').value.replace(/[\\/]+$/, '');
    const parent = current.split(/[\\/]/).slice(0, -1).join('\\');
    browseFolder(parent);
  });
  $('#folder-list').addEventListener('click', (e) => {
    const row = e.target.closest('[data-path]');
    if (row) browseFolder(row.dataset.path);
  });
  $('#btn-folder-choose').addEventListener('click', () => {
    const path = $('#folder-path').value.trim();
    if (!path) { toastWarn('请选择目录'); return; }
    const input = $(`#set-${state.folderPickTarget}`);
    if (input) input.value = path;
    hideModal('modal-folder');
    toastOk('已选择目录，记得点击“保存设置”');
  });

  /* ---------------- 日志 ---------------- */
  $('#btn-log-clear').addEventListener('click', async () => {
    await api('/api/logs', 'DELETE');
    $('#log-box').innerHTML = '';
  });
  $('#log-level').addEventListener('change', refreshLogs);
  $('#btn-log-autoscroll').classList.add('active');
  $('#btn-log-autoscroll').addEventListener('click', (e) => {
    e.currentTarget.classList.toggle('active');
    state.autoScroll = e.currentTarget.classList.contains('active');
    if (state.autoScroll) $('#log-box').scrollTop = $('#log-box').scrollHeight;
  });
  // 注意：外观控件（背景图上传、滑块等）不在这里绑定——
  // 此时 #settings-form 还没渲染出来，绑了也是空的。
  // 它们在 loadSettings() 里渲染完成后绑定。
}

/** 一键把某部动漫的全部剧集加入下载队列（搜索结果行的「直链可下载」按钮）。 */
