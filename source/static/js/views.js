/* ============================================================
   视图渲染
   结构：结果列表（slot）/ 源列表（rack）/ 任务队列（task）
   ============================================================ */
const views = {};

/* ---------------------------------------------------------- 顶部状态条 */
views.ticker = function (name) {
  const label = { search: '番剧搜索', sources: '动漫源', tasks: '下载任务',
    library: '番剧库', settings: '设置', logs: '系统日志' }[name] || name;
  const node = $('#tick-view');
  if (node) node.textContent = label;
};

/* ---------------------------------------------------------- 检索源选择 */
views.searchSourcePicker = function (sources) {
  const box = $('#search-source-picker');
  const list = (sources || []).filter((s) => !s.hidden);
  if (!list.length) {
    box.innerHTML = '<span class="text-dim">还没有动漫源，请先到「动漫源」添加</span>';
    return;
  }
  box.innerHTML = list.map((s, index) => {
    const led = s.last_error ? '<span class="led err"></span>'
      : (s.ok_count ? '<span class="led on"></span>' : '<span class="led off"></span>');
    // 勾选框是蓝色的（accent-color 控制），chip 保持白底描边：
    // 蓝色只出现在勾选框一处，比整块蓝色底更好读，状态灯也不会被蓝底糊掉。
    return `
    <label class="source-pill ${s.enabled ? 'checked' : ''}" data-id="${s.id}"
           title="${esc(s.note || '')}${s.enabled ? '' : '（未启用，点击启用）'}">
      <input type="checkbox" class="form-check-input src-pick" ${s.enabled ? 'checked' : ''}
             data-id="${s.id}" tabindex="-1" aria-label="${esc(s.name)} 是否参与检索">
      <span class="order-badge">${String(index + 1).padStart(2, '0')}</span>
      <span class="pill-name">${esc(s.name)}</span>
      ${led}
    </label>`;
  }).join('');
};

/* ---------------------------------------------------------- 播出单 */
/** 归一化标题，用于跨源合并同一部番剧。
 *  不同源对同一部的写法常有差异：全角/半角括号、多余空格、
 *  分隔符 ·・‧ 与中划线等。这里统一成可比对的形式；
 *  **只用于分组**，显示时仍用原始标题。 */
function normalizeTitle(name) {
  return String(name || '')
    .replace(/\uFF08/g, '(')
    .replace(/\uFF09/g, ')')
    .replace(/[\u3010\u3011]/g, '')
    .replace(/[\u00b7\u30fb\u2027\u2022\u30fc\u2013\u2014\-]/g, '')
    .replace(/[\uFF1A:]/g, '')
    .replace(/\s+/g, '')
    .toLowerCase();
}

/** 把每个源的分组结果摊平成「按番剧」的桶。
 *  以前是按资源站分块：同一部番在 9 个源里就出现 9 次，
 *  用户得在多个块之间来回找「到底哪个源有这部」。 */
function collectAnimeBuckets(payload, picked) {
  const kw = normalizeTitle(payload.keyword || '');
  const buckets = new Map();
  (payload.groups || []).forEach((group, gi) => {
    (group.results || []).forEach((item) => {
      const key = normalizeTitle(item.name) || String(item.name || '');
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = { key, variants: new Map(), sourceCount: 0, exact: false };
        buckets.set(key, bucket);
      }
      // 变体也按**归一化名**分桶，不能按原始名字：
      // 「狐妖小红娘 镜花缘篇」与「狐妖小红娘镜花缘篇」只差一个空格，
      // 归一化后相同；若按原名分桶就会出现两行看着一模一样的条目。
      const vkey = normalizeTitle(item.name) || String(item.name || item.url);
      let variant = bucket.variants.get(vkey);
      if (!variant) {
        variant = { name: item.name, entries: [] };
        bucket.variants.set(vkey, variant);
      } else if (variant.name
                 && String(item.name || '').length < String(variant.name).length) {
        // 显示名用最短的那个（通常是没多余空格的规范写法）
        variant.name = item.name;
      }
      variant.entries.push({
        name: item.name, url: item.url, cover: item.cover || '',
        playable: item.playable, episode_count: item.episode_count,
        type_name: item.type_name, update_info: item.update_info,
        source_id: item.source_id, source: item.source || group.source,
        priority: gi,
      });
      bucket.sourceCount += 1;
      if (normalizeTitle(item.name) === kw) bucket.exact = true;
    });
  });

  const list = [...buckets.values()];
  list.forEach((b) => {
    b.variants.forEach((v) => {
      const want = picked ? picked[v.name] : undefined;
      v.entries.sort((x, y) => {
        // 用户显式选过的源排第一，其余按全局优先级
        if (want !== undefined) {
          const wx = Number(x.source_id) === Number(want) ? 0 : 1;
          const wy = Number(y.source_id) === Number(want) ? 0 : 1;
          if (wx !== wy) return wx - wy;
        }
        return x.priority - y.priority;
      });
    });
  });
  // 排序：片名完全匹配在前 → 有更多源可选的在前（更可能下得动）→ 按名称
  list.sort((a, b) => (Number(b.exact) - Number(a.exact))
    || (b.sourceCount - a.sourceCount)
    || String(a.key).localeCompare(String(b.key), 'zh'));
  return list;
}

views.searchResults = function (payload) {
  const box = $('#search-results');
  const groups = payload.groups || [];
  const keyword = payload.keyword || '';
  const summary = $('#search-summary');
  const hasResult = groups.some((g) => (g.results || []).length);
  const errors = (payload.errors || []).length;

  if (!groups.length) {
    box.innerHTML = '<div class="empty"><i class="bi bi-inboxes"></i>没有可展示的结果</div>';
    return;
  }
  if (!hasResult) {
    if (summary) summary.textContent = `「${keyword}」没有命中`;
    // 区分两种"没结果"：源真的连不上（要修源） vs 源正常但没有这个片子（换个关键词）
    const allFailed = errors > 0 && errors >= groups.length;
    const why = allFailed
      ? '所有源都没能返回数据（多为网络不通或源站不可达），请看下面的报错'
      : (errors > 0
        ? `有 ${errors} 个源没有返回数据（见下面报错），其余源没匹配到这个片名`
        : '源都正常，只是没有这个片名；换个关键词或到「动漫源」用“测试”检查检索规则');
    box.innerHTML = `<div class="empty"><i class="bi bi-emoji-frown"></i>
      没有检索到「${esc(keyword)}」<div class="text-dim mt-2" style="font-size:11.5px">
      ${why}</div></div>
      ${views.errorList(payload.errors)}`;
    return;
  }

  const list = collectAnimeBuckets(payload, state.pickedSource || {});
  if (summary) {
    // 有结果的失败源只在汇总行里报个数，完整报错留给「完全没结果」时显示 ——
    // 否则一大段红色报错会一直挂在结果列表最下面，很吵且没人看
    const failed = errors ? ` · ${errors} 个源未返回` : '';
    summary.textContent = `「${keyword}」命中 ${payload.total} 条 · `
      + `合并为 ${list.length} 部 · 来自 ${payload.source_count} 个源${failed}`;
  }

  let html = '';
  list.forEach((bucket, index) => {
    const title = bucket.variants.values().next().value.name;
    html += `
      <div class="group-block anime-group">
        <div class="chan">
          <span class="rank">${String(index + 1).padStart(2, '0')}</span>
          <span class="name">${esc(title)}</span>
          <span class="stat">${bucket.sourceCount} 个源命中</span>
          <span class="spacer"></span>
          ${bucket.sourceCount > 1
            ? '<span class="badge badge-soft">已合并同名片源</span>' : ''}
        </div>
        <div class="slot-list">
          ${[...bucket.variants.values()].map((v, vi) => views.slot(v, vi)).join('')}
        </div>
      </div>`;
  });
  // 这里**不**再附加失败源清单：要看得去「系统日志」，或在完全没结果时自动显示
  box.innerHTML = html;
};

views.errorList = function (errors) {
  if (!errors || !errors.length) return '';
  return `<div class="hint-box" style="margin-top:14px;border-left-color:var(--amber);background:transparent">
    <b>以下源检索失败</b>
    <div class="mono mt-1" style="font-size:11px">
      ${errors.map((e) => `${esc(e.source)} — ${esc(String(e.error).slice(0, 90))}`).join('<br>')}
    </div></div>`;
};

views.slot = function (variant, index) {
  const entries = (variant && variant.entries) || [];
  const cur = entries[0];
  if (!cur) return '';
  const meta = [];
  if (cur.episode_count) meta.push(`<span class="ep">${cur.episode_count} 集</span>`);
  if (cur.type_name) meta.push(`<span>${esc(cur.type_name)}</span>`);
  if (cur.update_info) meta.push(`<span>${esc(cur.update_info)}</span>`);

  // 「能不能直接下载」是选源时最该先看到的：放在源标签之前
  const tags = [];
  if (cur.playable === true) {
    tags.push(`<button class="slot-flag slot-flag-btn" data-act="quick-download"
                       type="button" title="直接把这部的全部剧集加入下载队列">
                <i class="bi bi-download"></i> 直链可下载</button>`);
  } else if (cur.playable === false) {
    tags.push('<span class="slot-flag warn" title="只能打开分享页，下载时程序会自动进去解析">仅分享页</span>');
  }

  // 这一部在哪些源有；点一下即可切到那个源下载
  const chips = entries.length > 1
    ? `<span class="slot-sources">${entries.map((en, i) => `
        <button type="button" class="src-chip${i === 0 ? ' active' : ''}"
                data-src-index="${i}"
                data-source-id="${esc(en.source_id ?? '')}"
                data-url="${esc(en.url)}"
                title="用「${esc(en.source || '未知源')}」下载这一部"
        >${esc(en.source || '未知源')}</button>`).join('')}</span>`
    : (cur.source ? `<span class="slot-sources"><span class="src-chip active static"
        >${esc(cur.source)}</span></span>` : '');

  return `
    <div class="slot" data-name="${esc(cur.name)}" data-url="${esc(cur.url)}"
         data-cover="${esc(cur.cover || '')}" data-source-id="${esc(cur.source_id ?? '')}"
         data-source="${esc(cur.source || '')}">
      <span class="slot-no">${String(index + 1).padStart(2, '0')}</span>
      <span>${cur.cover
        ? `<img class="slot-thumb" src="${esc(cur.cover)}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'">`
        : '<span class="slot-thumb ph"><i class="bi bi-film"></i></span>'}</span>
      <span class="slot-main">
        <span class="slot-name" title="${esc(cur.name)}">${esc(cur.name)}</span>
        <span class="slot-meta">${meta.join('') || '<span>—</span>'}</span>
        ${chips}
      </span>
      <span class="slot-side">
        ${tags.join('')}
        <i class="bi bi-chevron-right text-dim"></i>
      </span>
    </div>`;
};

/* ---------------------------------------------------------- 剧集 */
views.episodes = function (data) {
  const box = $('#episode-list');
  const episodes = data.episodes || [];
  // 所有剧集一视同仁：默认全部勾选，不因"是否已下载"做任何区分（避免误跳过）
  box.innerHTML = episodes.map((ep, index) => `
    <label class="ep-row checked" data-ep-index="${index}" data-ep-url="${esc(ep.url || '')}"
           title="${esc(ep.name)}">
      <input type="checkbox" class="ep-check form-check-input mt-0" data-index="${index}"
             checked>
      <span class="ep-main">
        <span class="ep-title">${esc(ep.name)}</span>
        <span class="ep-sub">第 ${index + 1} 项 · ${esc(ep.url || '').slice(0, 46) || '—'}</span>
      </span>
      <button class="btn btn-outline-light btn-sm ep-play-btn" data-play="${index}"
              title="在线预览这一集（不下载）"><i class="bi bi-play-fill"></i></button>
    </label>`).join('');
  $('#episode-count').textContent = `${episodes.length} 集`;
  box.onchange = (e) => {
    if (e.target.classList.contains('ep-check')) {
      e.target.closest('.ep-row').classList.toggle('checked', e.target.checked);
    }
  };
  // 预览图缓存（同一集不重复抽帧）
};

/* ---------------------------------------------------------- 频道机架 */
views.sources = function (sources) {
  const box = $('#source-list');
  if (!sources.length) {
    box.innerHTML = `<div class="empty"><i class="bi bi-hdd-stack"></i>
      还没有动漫源，点右上角「添加源」或「自动探测」</div>`;
    return;
  }
  box.innerHTML = sources.map((s, index) => {
    const led = s.last_error ? '<span class="led err"></span>'
      : (s.ok_count ? '<span class="led on"></span>' : '<span class="led off"></span>');
    const stats = [];
    stats.push(`<span>${esc(s.type_label || s.type)}</span>`);
    if (s.ok_count) stats.push(`<span class="ok">成功 ${s.ok_count}</span>`);
    if (s.fail_count) stats.push(`<span class="bad">失败 ${s.fail_count}</span>`);
    if (s.hit_count) stats.push(`<span>命中 ${s.hit_count}</span>`);
    if (s.last_ms) stats.push(`<span>${(s.last_ms / 1000).toFixed(2)}s</span>`);
    if (s.last_error) stats.push(`<span class="bad">${esc(String(s.last_error).slice(0, 54))}</span>`);
    // 质量分徽标：默认隐藏，切到「智能排序」时才由 body.show-quality 显示
    const q = Number(s.quality || 0);
    const qClass = q >= 70 ? 'hi' : (q >= 40 ? 'mid' : 'lo');
    const quality = `<span class="quality-badge ${qClass}" title="质量分 ${q}/100：可用性 40 + 内容量 25 + 响应速度 20 + 干净度 15">${q.toFixed(0)}</span>`;
    return `
    <div class="rack-row ${s.enabled ? '' : 'disabled'} ${index === 0 && !s.ad_flag ? 'lead' : ''}"
         data-id="${s.id}" data-ad="${s.ad_flag ? '1' : '0'}">
      <i class="bi bi-grip-vertical handle" title="按住拖动调整优先级"></i>
      <span class="order">${String(index + 1).padStart(2, '0')}</span>
      <span class="d-flex justify-content-center">
        <input class="form-check-input src-toggle" type="checkbox" data-id="${s.id}"
               ${s.enabled ? 'checked' : ''} title="勾选后参与检索" style="cursor:pointer">
      </span>
      <span class="move-ops">
        <button data-act="up" data-id="${s.id}" title="上移一位"
                ${index === 0 ? 'disabled' : ''}><i class="bi bi-caret-up-fill"></i></button>
        <button data-act="down" data-id="${s.id}" title="下移一位"
                ${index === sources.length - 1 ? 'disabled' : ''}><i class="bi bi-caret-down-fill"></i></button>
      </span>
      <span class="info">
        <span class="name">${led}${esc(s.name)}
          ${quality}
          ${s.ad_flag
            ? `<span class="badge badge-ad" title="${s.ad_kind === 'incomplete'
                ? '实测该源时长明显短于其它源（内容可能残缺），已排到最后'
                : '实测该源时长明显长于其它源（片头/片尾可能被换成广告），已排到最后'}">
                ${s.ad_kind === 'incomplete' ? '疑似残缺' : '含广告'}</span>`
            : ''}
          ${s.builtin ? '<span class="badge badge-dim">内置</span>' : ''}
          ${s.note ? `<span class="badge badge-dim">${esc(String(s.note).slice(0, 24))}</span>` : ''}
        </span>
        <span class="sub">${esc(s.base_url || '未填写地址')} · ${stats.join(' · ')}</span>
      </span>
      <span class="actions">
        <button class="btn btn-outline-light btn-sm" data-act="test" data-id="${s.id}">测试</button>
        <button class="btn btn-outline-light btn-sm" data-act="check" data-id="${s.id}">测活</button>
        <button class="btn ${s.ad_flag ? 'btn-primary' : 'btn-outline-light'} btn-sm"
                data-act="ad-flag" data-id="${s.id}"
                title="${s.ad_flag ? '取消广告标记' : '标记该源视频含广告（会排到最后）'}">
          <i class="bi bi-flag${s.ad_flag ? '-fill' : ''}"></i></button>
        <button class="btn btn-outline-light btn-sm" data-act="edit" data-id="${s.id}"><i class="bi bi-pencil"></i></button>
        <button class="btn btn-outline-light btn-sm" data-act="copy" data-id="${s.id}"><i class="bi bi-files"></i></button>
        <button class="btn btn-outline-light btn-sm" data-act="delete" data-id="${s.id}"><i class="bi bi-trash3"></i></button>
      </span>
    </div>`;
  }).join('');
};

/* ---------------------------------------------------------- 任务 */
views.taskStats = function (stats) {
  const speedText = fmtSpeed(stats.speed);
  const noSpeed = !stats.speed || speedText === '—';
  const cells = [
    ['进行中', (stats.running || 0) + (stats.queued || 0)],
    ['已暂停', stats.paused || 0],
    ['已完成', stats.completed || 0],
    ['失败', (stats.failed || 0) + (stats.canceled || 0)],
    ['总大小', fmtSize(stats.total_bytes)],
  ];
  const html = cells.map(([k, v]) =>
    `<div class="stat-cell"><div class="v">${esc(v)}</div><div class="k">${k}</div></div>`).join('');
  // 速度位没有数据时用骨架脉冲，别让整页顶着一个 "—" 像卡死
  $('#task-stats').innerHTML = html + `<div class="stat-cell"><div class="v">${
    noSpeed ? '<span class="skeleton" title="暂无数据"></span>' : esc(speedText)
  }</div><div class="k">实时速度</div></div>`;
};

/** 任务列表的排序规则。
 *  渲染与原地更新**必须共用**这一套，否则原地更新的顺序比对永远不命中。 */
function orderTasks(tasks) {
  // 按番剧分组；同一部动漫内部：未完成的排前面，已完成/失败等已结束的排最后
  const groups = new Map();
  tasks.forEach((t) => {
    const key = t.anime_name || '未命名';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  });
  const finishedRank = (t) => (['completed', 'failed', 'canceled'].includes(t.status) ? 1 : 0);
  groups.forEach((list) => {
    list.sort((a, b) => finishedRank(a) - finishedRank(b));
  });
  // 番剧之间：还有活儿在干的排前面
  const ordered = [...groups.entries()].sort((a, b) => {
    const activeA = a[1].filter((t) => !finishedRank(t)).length;
    const activeB = b[1].filter((t) => !finishedRank(t)).length;
    return activeB - activeA;
  });
  return { groups, ordered, finishedRank };
}

views.tasks = function (tasks) {
  const box = $('#task-list');
  if (!tasks.length) {
    box.innerHTML = '<div class="empty"><i class="bi bi-inbox"></i>暂无下载任务</div>';
    return;
  }
  const { ordered, finishedRank } = orderTasks(tasks);

  box.innerHTML = ordered.map(([anime, list]) => {
    const running = list.filter((t) => t.status === 'running').length;
    const queued = list.filter((t) => t.status === 'queued').length;
    const paused = list.filter((t) => t.status === 'paused').length;
    const done = list.filter((t) => t.status === 'completed').length;
    const bad = list.filter((t) => ['failed', 'canceled'].includes(t.status)).length;
    const bytes = list.reduce((sum, t) => sum + (t.total_bytes || 0), 0);
    const overall = list.length
      ? list.reduce((sum, t) => sum + Math.max(0, Math.min(100, Number(t.progress || 0))), 0) / list.length
      : 0;
    const parts = [];
    if (running) parts.push(`下载中 ${running}`);
    if (queued) parts.push(`排队 ${queued}`);
    if (paused) parts.push(`暂停 ${paused}`);
    if (done) parts.push(`完成 ${done}`);
    if (bad) parts.push(`<span class="err">失败 ${bad}</span>`);
    const collapsed = Boolean(state.collapsedGroups && state.collapsedGroups[anime]);
    const allFinished = list.every(finishedRank);

    return `
      <div class="task-group ${collapsed ? 'collapsed' : ''}" data-anime="${esc(anime)}">
        <div class="task-group-head">
          <button class="g-toggle" data-group-toggle title="${collapsed ? '展开' : '收起'}">
            <i class="bi bi-chevron-${collapsed ? 'right' : 'down'}"></i>
          </button>
          <span class="g-name">${esc(anime)}</span>
          <span class="badge badge-dim">${list.length} 集</span>
          ${allFinished ? '<span class="badge badge-dim">已结束</span>' : ''}
          <span class="g-stat">${parts.join(' · ') || '—'}</span>
          <span class="g-stat">${fmtSize(bytes)}</span>
          <span class="spacer"></span>
          <span class="g-stat">总进度 ${overall.toFixed(0)}%</span>
          <span class="g-ops">
            <button class="btn btn-outline-light btn-sm" data-group-act="pause"
                    title="暂停「${esc(anime)}」的全部未完成任务"><i class="bi bi-pause-fill"></i> 暂停全部</button>
            <button class="btn btn-outline-light btn-sm" data-group-act="resume"
                    title="继续「${esc(anime)}」的全部已暂停任务"><i class="bi bi-play-fill"></i> 继续全部</button>
            <button class="btn btn-outline-light btn-sm" data-group-act="retry"
                    title="重新提交「${esc(anime)}」的全部失败 / 已取消任务（会重新解析视频地址）"><i class="bi bi-arrow-clockwise"></i> 全部重试</button>
            <button class="btn btn-outline-light btn-sm g-del" data-group-act="delete"
                    title="删除「${esc(anime)}」的全部下载记录（不会删除已下载的视频文件）"><i class="bi bi-trash"></i> 删除全部</button>
          </span>
        </div>
        <div class="task-group-body">
          ${list.map((t) => views.taskRow(t)).join('')}
        </div>
      </div>`;
  }).join('');
};

/** 一行的「结构指纹」：只有这些字段变了才需要重建 DOM。
 *  注意**不要**把 message / progress / speed 放进来 —— 它们每轮都在变，
 *  放进来就等于每 2.5 秒重建一次列表，列表会持续抽搐。
 *  这些易变字段由 views.updateTaskRows 原地更新。 */
function taskKey(t) {
  return [
    t.episode_name || '',
    t.source || '',
    t.status || '',
    t.error ? 1 : 0,
    t.save_path ? 1 : 0,
  ].join('\u0001');
}

views.taskRow = function (t) {
  const running = t.status === 'running';
  const progress = Math.max(0, Math.min(100, Number(t.progress || 0)));
  const barClass = t.status === 'completed' ? 'done'
    : (['failed', 'canceled'].includes(t.status) ? 'fail' : '');
  // 剩余时间：仅对进行中、知道自己总共多少字节、且速度大于 0 的任务显示
  const etaSeconds = running ? taskEtaSecondsSmoothed(t) : 0;
  const eta = etaSeconds > 0 ? fmtEtaShort(etaSeconds) : '';
  const detail = running && t.segments_total
    ? `${t.segments_done}/${t.segments_total} 分片 · ${fmtSpeed(t.speed)}`
    : `${fmtSize(t.done_bytes)}${t.total_bytes ? ' / ' + fmtSize(t.total_bytes) : ''}`;
  // 剩余时间放右边、和百分比一起：左列放不下会折行，右边短得多
  const rightText = `${eta ? '剩 ' + eta + ' · ' : ''}${progress.toFixed(0)}%`;
  // 中列已经有一个状态徽标了，这里只在 message 带来新信息时才显示，
  // 避免同一行出现两次"下载中"、两次分片计数（评审指出过这个噪音）
  const msg = String(t.message || '').trim();
  const redundant = !msg
    || msg === statusLabel(t.status)
    || /^分片\s*\d+\s*\/\s*\d+$/.test(msg);
  return `
      <div class="task-row${running ? ' is-running' : ''}"
           data-id="${esc(t.id)}" data-status="${esc(t.status)}"
           data-task-key="${esc(taskKey(t))}">
        <div>
          <div class="anime">${esc(t.episode_name)}</div>
          <div class="ep">${t.source ? esc(t.source) : '—'}</div>
        </div>
        <div>
          <span class="tag ${esc(t.status)}">${statusLabel(t.status)}</span>
          ${redundant ? '' : `<div class="ep" data-role="msg" title="${esc(msg)}">${esc(msg.slice(0, 34))}</div>`}
        </div>
        <div>
          <div class="status-line"><span>${detail}</span><span>${rightText}</span></div>
          <div class="bar ${barClass}"><i style="width:${progress}%"></i></div>
          ${t.error ? `<div class="ep" style="color:var(--amber)" title="${esc(t.error)}">
            ${esc(String(t.error).slice(0, 52))}</div>` : ''}
          ${(t.status === 'completed' && /缺\s*\d+\s*个分片|不完整/.test(t.message || ''))
            ? `<div class="ep" style="color:var(--amber)" title="源站 CDN 缺片，换一个源重下可得完整版">
                 ⚠ 分片不完整，建议换源重下</div>` : ''}
        </div>
        <div class="ops">
          ${(running || t.status === 'queued')
            ? '<button class="btn btn-outline-light btn-sm" data-act="pause"><i class="bi bi-pause-fill"></i></button>' : ''}
          ${t.status === 'paused'
            ? '<button class="btn btn-outline-light btn-sm" data-act="resume"><i class="bi bi-play-fill"></i></button>' : ''}
          ${['failed', 'canceled'].includes(t.status)
            ? '<button class="btn btn-outline-light btn-sm" data-act="retry"><i class="bi bi-arrow-clockwise"></i></button>' : ''}
          ${t.status === 'completed' && t.save_path
            ? '<button class="btn btn-outline-light btn-sm" data-act="play"><i class="bi bi-play-fill"></i></button>' : ''}
          <button class="btn btn-outline-light btn-sm" data-act="delete"><i class="bi bi-trash3"></i></button>
        </div>
      </div>`;
};

/**
 * 尝试原地更新已渲染的任务行（返回 true = 完全命中，调用方不必重建 DOM）。
 *
 * 任务页每 2.5 秒刷新一次；如果每次都重建 DOM，进度条宽度、速度文字、
 * 状态文案会被反复重写，列表看起来在"抽搐"。这里只把变化的值写进去，
 * 结构没变的行连动都不动。
 */
views.updateTaskRows = function (tasks) {
  const box = $('#task-list');
  if (!box) { views.__updateDebug = 'no-box'; return false; }
  // 关键：按渲染时相同的顺序展开，否则行顺序比对必然失败
  const flat = [];
  orderTasks(tasks).ordered.forEach(([, list]) => list.forEach((t) => flat.push(t)));

  const existing = Array.from(box.querySelectorAll('.task-row'));
  if (existing.length !== flat.length) {
    views.__updateDebug = `count ${existing.length} != ${flat.length}`;
    return false;
  }

  const byId = new Map(existing.map((el) => [el.dataset.id, el]));
  for (const t of flat) {
    const el = byId.get(String(t.id));
    // 顺序或被过滤掉了 → 交给调用方整体重建
    if (!el) { views.__updateDebug = `missing id ${t.id}`; return false; }
    if (el.dataset.taskKey !== taskKey(t)) {
      views.__updateDebug = `key changed @${t.episode_name}: "${el.dataset.taskKey}" vs "${taskKey(t)}"`;
      return false;
    }
  }
  // 顺序也必须一致，否则分组顺序变了
  for (let i = 0; i < flat.length; i += 1) {
    if (byId.get(String(flat[i].id)) !== existing[i]) {
      views.__updateDebug = `order differs at ${i}: dom=${existing[i].dataset.id} want=${flat[i].id}`;
      return false;
    }
  }
  views.__updateDebug = 'hit';

  for (const t of flat) {
    const el = byId.get(String(t.id));
    const progress = Math.max(0, Math.min(100, Number(t.progress || 0)));
    const running = t.status === 'running';
    const etaSeconds = running ? taskEtaSecondsSmoothed(t) : 0;
    const eta = etaSeconds > 0 ? fmtEtaShort(etaSeconds) : '';
    const detail = running && t.segments_total
      ? `${t.segments_done}/${t.segments_total} 分片 · ${fmtSpeed(t.speed)}`
      : `${fmtSize(t.done_bytes)}${t.total_bytes ? ' / ' + fmtSize(t.total_bytes) : ''}`;
    const rightText = `${eta ? '剩 ' + eta + ' · ' : ''}${progress.toFixed(0)}%`;
    const bar = el.querySelector('.bar');
    const fill = bar && bar.querySelector('i');
    if (fill) {
      const want = progress + '%';
      if (fill.style.width !== want) fill.style.width = want;   // 只有真的变了才写
    }
    const line = el.querySelector('.status-line');
    if (line && line.children.length >= 2) {
      const left = line.children[0];
      const right = line.children[1];
      if (left.textContent !== detail) left.textContent = detail;
      if (right.textContent !== rightText) right.textContent = rightText;
    }
    // 中列的消息（分片进度等）也是易变内容，按需原地更新，不靠重建
    const msgBox = el.querySelector('.ep[data-role="msg"]');
    if (msgBox) {
      const want = String(t.message || '').slice(0, 34);
      if (msgBox.textContent !== want) msgBox.textContent = want;
      if (msgBox.title !== String(t.message || '')) msgBox.title = String(t.message || '');
    }
    // 操作列可能随状态变化（暂停↔继续↔重试），这类变化已在 taskKey 里拦截，
    // 走到这里说明结构一致，无需再动。
  }
  return true;
};

/* ---------------------------------------------------------- 媒体库 */
views.library = function (data) {
  const box = $('#library-list');
  const items = (data.items || []).filter((i) => i.episode_count);
  state.libraryItems = items;          // 删除弹窗要显示集数和体积
  const totalSubs = items.reduce((sum, item) =>
    sum + item.episodes.filter((e) => (e.subtitles || []).length).length, 0);
  $('#library-summary').textContent = `${items.length} 部 · ${fmtSize(data.total_size)}`
    + (totalSubs ? ` · ${totalSubs} 集有字幕` : '');
  if (!items.length) {
    box.innerHTML = `<div class="empty"><i class="bi bi-folder2"></i>下载目录中还没有视频文件
      <div class="text-dim mono mt-2" style="font-size:11px">${esc(data.download_dir || '')}</div></div>`;
    return;
  }
  box.innerHTML = `<div class="lib-list">${items.map((item, index) => {
    const subCount = item.episodes.filter((e) => (e.subtitles || []).length).length;
    return `
    <details class="lib-anime" data-anime="${esc(item.anime_name)}" ${index === 0 ? 'open' : ''}>
      <summary>
        <span class="slot-no">${String(index + 1).padStart(2, '0')}</span>
        <span class="nm">${esc(item.anime_name)}</span>
        <span class="sm">${item.episode_count} 个文件 · ${fmtSize(item.size)}</span>
        ${subCount
          ? `<span class="slot-flag"><i class="bi bi-badge-cc"></i> ${subCount} 集有字幕</span>`
          : '<span class="slot-flag warn"><i class="bi bi-badge-cc"></i> 无字幕</span>'}
        <span class="spacer"></span>
        <button class="btn btn-outline-light btn-sm" data-act="check-episodes"
                data-anime="${esc(item.anime_name)}" title="用 B 站官方集数核对该番是否下全">
          <i class="bi bi-check2-square"></i> 校验集数</button>
        <button class="btn btn-outline-light btn-sm" data-act="subtitle"
                data-anime="${esc(item.anime_name)}" title="按集号搜索并下载字幕">
          <i class="bi bi-badge-cc"></i> 下载字幕</button>
        <button class="btn btn-outline-light btn-sm" data-act="danmaku"
                data-anime="${esc(item.anime_name)}" title="从 B 站抓这部番每一集的弹幕，存到视频旁边">
          <i class="bi bi-chat-square-text"></i> 下载弹幕</button>
        <button class="btn btn-outline-light btn-sm btn-icon" data-act="delete-anime"
                data-anime="${esc(item.anime_name)}" title="删除这部番（整部）">
          <i class="bi bi-trash3"></i></button>
        <i class="bi bi-chevron-down text-dim"></i>
      </summary>
      <div class="lib-check" hidden></div>
      <div class="lib-eps">
        ${item.episodes.map((ep) => `
          <div class="lib-ep" title="${esc(ep.path)}">
            <span class="nm">${esc(ep.episode_name || ep.name)}
              ${(ep.subtitles || []).map((s) => `
                <span class="sub-chip" data-act="open-sub" data-path="${esc(s.path)}"
                      title="${esc(s.path)}">
                  <i class="bi bi-badge-cc"></i> ${esc(s.name.slice(-14))}</span>`).join('')}
              ${(ep.danmaku || []).length ? `
                <span class="sub-chip" title="${esc((ep.danmaku || [])
                  .map((d) => (d.dir ? `${d.dir}/${d.name}` : d.name)).join(' / '))}">
                  <i class="bi bi-chat-square-text"></i> 弹幕</span>` : ''}
            </span>
            <span>${fmtSize(ep.size)}</span>
            <span>
              ${ep.exists ? `<button class="btn btn-outline-light btn-sm btn-icon" data-act="play"
                data-path="${esc(ep.path)}" data-name="${esc(ep.episode_name || '')}">
                <i class="bi bi-play-fill"></i></button>` : '<span class="badge badge-dim">缺失</span>'}
              <button class="btn btn-outline-light btn-sm btn-icon" data-act="delete"
                data-path="${esc(ep.path)}"><i class="bi bi-trash3"></i></button>
            </span>
          </div>`).join('')}
      </div>
    </details>`;
  }).join('')}</div>`;
};

/* ---------------------------------------------------------- 设置 */

/* ---------------------------------------------------------- 配色方案 */
/* 每套方案给出 3 个底色 + 1 个主色；空字符串表示「跟随亮/暗主题」。
   留空是有意的：这样「暗色主题 + 默认面板色」这种组合也能一键还原。 */
const THEME_PALETTES = [
  {
    key: 'default', label: '跟随主题',
    hint: '不覆盖任何颜色，完全由亮色/暗色主题决定',
    accent_color: '', bg_color: '', panel_color: '', panel_2_color: '',
  },
  {
    key: 'ink', label: '墨蓝',
    hint: '深色页面 + 更深的面板，接近 IDE/终端的观感',
    accent_color: '#4d84f5', bg_color: '#0f1115', panel_color: '#161a22', panel_2_color: '#1c2230',
  },
  {
    key: 'paper', label: '纸白',
    hint: '亮色高对比，卡片纯白、底色略灰',
    accent_color: '#2563eb', bg_color: '#eef1f7', panel_color: '#ffffff', panel_2_color: '#f5f7fb',
  },
  {
    key: 'sepia', label: '暖褐',
    hint: '低蓝光的暖色阅读感，长时间盯任务更舒服',
    accent_color: '#b45309', bg_color: '#f6f1e7', panel_color: '#fffdf8', panel_2_color: '#f1e9db',
  },
  {
    key: 'forest', label: '松绿',
    hint: '绿色主色 + 浅灰绿底，状态灯与主色不易混淆',
    accent_color: '#0f766e', bg_color: '#eef4f2', panel_color: '#ffffff', panel_2_color: '#e7f0ed',
  },
  {
    key: 'violet', label: '夜紫',
    hint: '深紫底 + 紫色主色，暗环境下对比柔和',
    accent_color: '#8b5cf6', bg_color: '#14111f', panel_color: '#1c1830', panel_2_color: '#241f3d',
  },
  {
    key: 'contrast', label: '高对比',
    hint: '纯黑底 + 纯白卡片，户外或投影时更清楚',
    accent_color: '#f59e0b', bg_color: '#000000', panel_color: '#14171c', panel_2_color: '#1c2027',
  },
];

SETTING_GROUPS = [
  {
    title: '下载位置', fields: [
      { key: 'download_dir', label: '下载目录', type: 'folder', span: 12,
        hint: '最终视频保存的位置，支持绝对路径' },
      { key: 'temp_dir', label: '临时目录（分片缓存）', type: 'text', span: 12,
        hint: '下载中的分片先放这里，完成后自动清理' },
      { key: 'organize_by_anime', label: '每部动漫单独建文件夹', type: 'switch', span: 6 },
      { key: 'skip_existing', label: '已存在的剧集自动跳过', type: 'switch', span: 6 },
    ],
  },
  {
    title: '命名规则', fields: [
      { key: 'naming_scheme', label: '命名方案', type: 'scheme', span: 6 },
      { key: 'default_season', label: '默认季数', type: 'number', span: 3, min: 1, max: 99 },
      { key: 'filename_max_length', label: '文件名最大长度', type: 'number', span: 3, min: 32, max: 200 },
    ],
  },
  {
    title: '并发与网络', fields: [
      { key: 'concurrent_tasks', label: '同时下载任务数', type: 'number', span: 3, min: 1, max: 20 },
      { key: 'segment_threads', label: '单任务分片并发', type: 'number', span: 3, min: 1, max: 32 },
      { key: 'search_concurrency', label: '检索并发源数', type: 'number', span: 3, min: 1, max: 20 },
      { key: 'max_retries', label: '失败重试次数', type: 'number', span: 3, min: 0, max: 10 },
      { key: 'request_timeout', label: '请求超时（秒）', type: 'number', span: 3, min: 5, max: 120 },
      { key: 'source_timeout', label: '源超时（秒）', type: 'number', span: 3, min: 5, max: 120 },
      { key: 'proxy', label: 'HTTP 代理', type: 'text', span: 6, placeholder: 'http://127.0.0.1:7890' },
      { key: 'verify_ssl', label: '校验 HTTPS 证书', type: 'switch', span: 6,
        hint: '部分动漫站证书异常，关闭可提高成功率' },
      { key: 'browser_resolver', label: '浏览器取流（播放器嗅探）', type: 'switch', span: 6,
        hint: '有些站的视频地址要执行站点自己的 JS 才算得出（会话签名、混淆解密）。'
            + '开启后用无头浏览器打开播放页、监听网络请求截获真实地址；'
            + '只对声明需要它的源生效，普通源仍走 HTTP。环境不满足时会自动判定不可用' },
      { key: 'user_agent', label: 'User-Agent', type: 'text', span: 12 },
    ],
  },
  {
    title: '下载后处理', fields: [
      { key: 'remux_to_mp4', label: '用 ffmpeg 转封装为 mp4', type: 'switch', span: 6,
        hint: '没有 ffmpeg 时跳过，直接拼接为 .ts（可播放）' },
      { key: 'delete_ts_after_remux', label: '自动清理多余 .ts 产物', type: 'switch', span: 6,
        hint: '同名视频已是 mp4 时，删掉旧的 .ts（早期未装 ffmpeg 时留下的）' },
      { key: 'keep_temp_segments', label: '保留临时分片（排错用）', type: 'switch', span: 6 },
      { key: 'verify_video', label: '完成后校验文件大小', type: 'switch', span: 6 },
      { key: 'dedup_on_organize', label: '整理后自动去重', type: 'switch', span: 6,
        hint: '自动整理 / 按季整理跑完后，把同一集多余的副本直接删除' },
      { key: 'danmaku_format', label: '弹幕保存格式', type: 'select', span: 6,
        options: [
          { value: 'xml', label: 'XML（播放器通用，推荐）' },
          { value: 'ass', label: 'ASS（可直接压进视频）' },
          { value: 'both', label: '两种都存' },
        ],
        hint: '弹幕存在视频旁边，文件名与视频相同' },
      { key: 'danmaku_service', label: '外部弹幕服务地址（可选）', type: 'text', span: 12,
        placeholder: 'http://192.168.1.7:9321/87654321',
        hint: '填 danmu_api 之类的弹弹play 兼容服务，就能抓爱奇艺/腾讯/优酷等平台的弹幕；'
          + '留空则只用 B 站。地址末尾的 token 段可省（默认 87654321）',
        action: { label: '测试连接', path: '/api/danmaku/service/test' } },
    ],
  },
  {
    title: '外观自定义', fields: [
      { key: 'theme', label: '界面主题', type: 'theme', span: 3 },
      { key: 'title_text', label: '页面标题（留空用默认）', type: 'text', span: 5,
        placeholder: '动漫下载机 Anime Downloader' },
      { key: 'title_color', label: '标题颜色', type: 'color', span: 4,
        hint: '留空跟随主题' },
      { key: 'palette', label: '配色方案', type: 'palette', span: 12,
        hint: '一键套用整套配色；套用后仍可逐项微调下面的颜色（改完会标记为「自定义」）' },
      { key: 'accent_color', label: '页面主色', type: 'color', span: 6,
        hint: '按钮、选中态、进度条、链接都用它；留空跟随主题',
        options: ['#2563eb', '#7c3aed', '#0d9488', '#db2777', '#ea580c', '#0f766e'] },
      { key: 'bg_color', label: '页面底色', type: 'color', span: 6,
        hint: '整页背景；留空跟随主题。填深色可得到「暗色页面 + 亮色卡片」这类组合',
        options: ['#f4f6fb', '#ffffff', '#eef1f7', '#161a22', '#111318', '#0b0d12'] },
      { key: 'panel_color', label: '卡片/面板底色', type: 'color', span: 6,
        hint: '卡片、弹窗、侧边栏；留空跟随主题',
        options: ['#ffffff', '#f8fafc', '#1a1f29', '#20262f', '#151a22'] },
      { key: 'panel_2_color', label: '次级底色', type: 'color', span: 6,
        hint: '表头、统计格、日志框这类浅一层的底；留空跟随主题',
        options: ['#f7f9fc', '#eef2f9', '#20262f', '#262d38'] },
      { key: 'animations', label: '界面动效', type: 'anim', span: 8,
        hint: '「仅状态」保留进度条流光与状态点呼吸（有助于判断是否在跑），关掉进场/悬停动画；'
              + '「关闭」则全部静止' },
      { key: 'bg_image', label: '背景图', type: 'background', span: 12,
        hint: '可拖拽图片到这里、点选本地文件，或直接填一个 http(s) 图片地址' },
      { key: 'bg_blur', label: '背景模糊', type: 'range', span: 3, min: 0, max: 40, unit: 'px' },
      { key: 'bg_dim', label: '背景遮罩', type: 'range', span: 3, min: 0, max: 100, unit: '%',
        hint: '越大画面越暗，文字越清楚' },
      { key: 'bg_panel_alpha', label: '面板不透明度', type: 'range', span: 3, min: 20, max: 100, unit: '%',
        hint: '调小可透出背景图' },
      { key: 'bg_fit', label: '背景填充方式', type: 'select', span: 3,
        options: [['cover', '铺满（裁剪）'], ['contain', '完整显示'], ['repeat', '平铺']] },
    ],
  },
  {
    title: '界面与服务', fields: [
      { key: 'log_level', label: '日志级别', type: 'logLevel', span: 3 },
      { key: 'web_port', label: 'Web 端口（重启生效）', type: 'number', span: 3, min: 1024, max: 65535 },
      { key: 'web_host', label: '监听地址（重启生效）', type: 'text', span: 3 },
      { key: 'access_token', label: '访问令牌（局域网访问时校验，留空=不校验）', type: 'text', span: 6,
        hint: '填了之后，本机以外的电脑必须带上令牌才能访问；本机不受影响' },
      { key: 'auto_open_browser', label: '启动后自动打开浏览器', type: 'switch', span: 6 },
      { key: 'merge_duplicates', label: '检索结果合并同名片源', type: 'switch', span: 6 },
      { key: 'auto_detect_sources', label: '允许自动探测生成配置', type: 'switch', span: 6 },
      { key: 'search_history_limit', label: '检索历史条数', type: 'number', span: 6, min: 5, max: 200 },
    ],
  },
];

views.settings = function (settings, schemes) {
  const html = SETTING_GROUPS.map((group) => `
    <div class="card">
      <div class="card-head"><h3>${group.title}</h3></div>
      <div class="card-body flush">
        <div class="set-grid">
          ${group.fields.map((field) => views.settingField(field, settings, schemes)).join('')}
        </div>
      </div>
    </div>`).join('') + `
    <div class="card">
      <div class="card-head"><h3>远程访问 · 其它电脑上看同一个库</h3>
        <div class="spacer"></div>
        <span id="lan-status" class="badge badge-dim">检测中…</span></div>
      <div class="card-body">
        <div class="hint-box mb-3">
          源、任务、下载历史都存在<b>运行服务的这台机器</b>上。想让另一台电脑看到同样的内容，
          不需要同步 —— 让那台电脑直接用浏览器打开这里的局域网地址即可，
          两边看到的是同一份数据，进度也是实时同步的。
        </div>
        <div id="lan-info" class="text-dim" style="font-size:12.5px">检测中…</div>
        <div class="d-flex gap-2 align-items-center flex-wrap mt-3">
          <button class="btn btn-outline-light btn-sm" id="btn-lan-enable">
            <i class="bi bi-broadcast"></i> 允许局域网访问</button>
          <button class="btn btn-outline-light btn-sm" id="btn-lan-local">
            <i class="bi bi-pc-display"></i> 仅本机</button>
          <button class="btn btn-outline-light btn-sm" id="btn-lan-refresh">
            <i class="bi bi-arrow-clockwise"></i> 刷新地址</button>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h3>外部依赖 · ffmpeg</h3>
        <div class="spacer"></div><span id="ffmpeg-status" class="badge badge-dim">检测中…</span></div>
      <div class="card-body">
        <div class="hint-box mb-3">
          ffmpeg 用于把 HLS 分片转封装为标准 <code>mp4</code>。没有它时程序会把分片直接拼接为
          <code>.ts</code>（同样可播放，兼容性略差）。
        </div>
        <div class="d-flex gap-2 align-items-center flex-wrap">
          <button class="btn btn-outline-light btn-sm" id="btn-ffmpeg-install">
            <i class="bi bi-download"></i> 一键下载安装 ffmpeg</button>
          <button class="btn btn-outline-light btn-sm" id="btn-ffmpeg-refresh">
            <i class="bi bi-arrow-clockwise"></i> 重新检测</button>
          <span class="text-dim mono" id="ffmpeg-path" style="font-size:11px"></span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h3>浏览器取流 · 播放器嗅探</h3>
        <div class="spacer"></div>
        <span id="browser-resolver-status" class="badge badge-dim">检测中…</span></div>
      <div class="card-body">
        <div class="hint-box mb-3">
          有些站的视频地址要<b>执行站点自己的 JS</b> 才算得出来 —— 会话签名、
          混淆解密、客户端渲染都发生在页面里，纯 HTTP 复刻会被 403。<br>
          本程序的做法是：用无头浏览器打开播放页，<b>监听网络请求</b>截获真正的
          <code>m3u8</code> / <code>mp4</code>，并把必需的
          <code>Referer</code> / <code>Cookie</code> 一并交给下载器 ——
          <b>不需要为每个站写适配</b>。<br>
          需要 <b>Node.js</b> + 一个 <b>Chromium 内核</b>（用已装的 Edge/Chrome 即可，
          不额外下载内核）。普通 HTTP 源不受影响，只有声明需要它的源才走这条路径。
        </div>
        <div class="d-flex gap-2 align-items-center flex-wrap">
          <button class="btn btn-outline-light btn-sm" id="btn-browser-resolver-refresh">
            <i class="bi bi-arrow-clockwise"></i> 重新检测</button>
          <button class="btn btn-outline-light btn-sm" id="btn-browser-resolver-clear">
            <i class="bi bi-x-circle"></i> 清空嗅探缓存</button>
          <span class="text-dim mono" id="browser-resolver-detail" style="font-size:11px"></span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h3>维护</h3></div>
      <div class="panel-body d-flex gap-2 flex-wrap">
        <button class="btn btn-outline-light btn-sm" id="btn-clean-temp"><i class="bi bi-recycle"></i> 清理临时分片</button>
        <button class="btn btn-outline-light btn-sm" id="btn-open-download-dir"><i class="bi bi-folder2-open"></i> 打开下载目录</button>
        <button class="btn btn-outline-light btn-sm" id="btn-purge-tasks"><i class="bi bi-trash3"></i> 清理未完成任务记录</button>
        <button class="btn btn-outline-light btn-sm" id="btn-export-sources"><i class="bi bi-box-arrow-up"></i> 导出全部源配置</button>
        <button class="btn btn-outline-danger btn-sm" id="btn-factory-reset">
          <i class="bi bi-exclamation-octagon"></i> 清除用户配置（恢复出厂）</button>
      </div>
      <div class="panel-body text-dim" style="font-size:12px;line-height:1.7;border-top:1px solid var(--line)">
        <b>清除用户配置</b>会清空：设置项、任务记录、下载历史、检索历史、自定义背景图、
        预览图缓存、源的「含广告」标记（源本身保留）。<br>
        <b>不会删除</b>已经下载好的视频文件。
      </div>
    </div>`;
  $('#settings-form').innerHTML = html;
  loadFfmpegStatus(true);
  // 浏览器取流能力状态（Anime1 那类源依赖它）
  if (typeof loadBrowserResolverStatus === 'function') loadBrowserResolverStatus();
};

/* ---------------------------------------------------------- ffmpeg 状态 */
let ffmpegTimer = null;

async function loadFfmpegStatus(silent) {
  const resp = await api('/api/system/ffmpeg');
  const box = $('#ffmpeg-status');
  if (!box) return;
  if (!resp.ok) {
    if (!silent) toastErr('获取 ffmpeg 状态失败：' + resp.error);
    return;
  }
  const data = resp.data;
  const job = data.job || {};
  if (job.running) {
    box.className = 'badge badge-soft';
    box.innerHTML = `<span class="spinner-border spinner-border-sm" style="width:10px;height:10px"></span> ${esc(job.message || '安装中…')}`;
    if (!ffmpegTimer) ffmpegTimer = setInterval(() => loadFfmpegStatus(true), 1500);
  } else {
    if (ffmpegTimer) { clearInterval(ffmpegTimer); ffmpegTimer = null; }
    box.className = data.available ? 'badge badge-soft' : 'badge badge-dim';
    box.innerHTML = data.available ? '<i class="bi bi-check2"></i> 已就绪' : '未安装';
    const pathNode = $('#ffmpeg-path');
    if (pathNode) pathNode.textContent = data.path || '未找到 ffmpeg.exe';
    if (job.message && job.ok === false && !silent) toastWarn(job.message);
    if (job.ok === true && !silent) toastOk(job.message);
  }
}

views.settingField = function (field, settings, schemes) {
  const value = settings[field.key];
  const wide = Number(field.span || 6) >= 12;
  const cls = wide ? 'set-field wide' : 'set-field';
  const hint = field.hint ? `<div class="form-text">${esc(field.hint)}</div>` : '';
  let control = '';

  if (field.type === 'switch') {
    control = `<div class="form-check form-switch">
      <input class="form-check-input setting-input" type="checkbox" id="set-${field.key}"
             data-key="${field.key}" data-type="bool" ${value ? 'checked' : ''}>
      <label class="form-check-label" for="set-${field.key}">${esc(field.label)}</label>
    </div>`;
    return `<div class="${cls}">${control}${hint}</div>`;
  }
  if (field.type === 'folder') {
    control = `<div class="dir-picker">
      <input class="form-control setting-input" id="set-${field.key}" data-key="${field.key}"
             value="${esc(value || '')}">
      <button class="btn btn-outline-light btn-sm" data-pick-folder="${field.key}">
        <i class="bi bi-folder2-open"></i> 浏览</button>
    </div>`;
  } else if (field.type === 'text' && field.action) {
    // 带动作按钮的输入框：比如「外部弹幕服务地址 + 测试连接」
    control = `<div class="dir-picker">
      <input class="form-control setting-input" id="set-${field.key}" data-key="${field.key}"
             value="${esc(value ?? '')}" placeholder="${esc(field.placeholder || '')}">
      <button class="btn btn-outline-light btn-sm" data-field-action="${field.key}"
              data-action-path="${esc(field.action.path || '')}"
              data-action-label="${esc(field.action.label || '测试')}">
        <i class="bi bi-plug"></i> ${esc(field.action.label || '测试')}</button>
    </div>`;
  } else if (field.type === 'scheme') {
    control = `<select class="form-select setting-input" id="set-${field.key}" data-key="${field.key}">
      ${Object.entries(schemes || {}).map(([key, label]) =>
        `<option value="${key}" ${key === value ? 'selected' : ''}>${esc(label)}</option>`).join('')}
    </select>`;
  } else if (field.type === 'theme') {
    control = `<select class="form-select setting-input" id="set-${field.key}" data-key="${field.key}">
      <option value="light" ${value !== 'dark' ? 'selected' : ''}>亮色（默认）</option>
      <option value="dark" ${value === 'dark' ? 'selected' : ''}>暗色</option>
    </select>`;
  } else if (field.type === 'color') {
    // 快捷色块：点一下直接填进输入框，省得去调色盘里找
    const swatches = (field.options || []).map((c) =>
      `<button type="button" class="color-chip" data-color-chip="${esc(c)}"
               style="background:${esc(c)}" title="用 ${esc(c)}"></button>`).join('');
    control = `<div class="d-flex gap-2 align-items-center flex-wrap">
      <input type="color" class="form-control form-control-color setting-input"
             id="set-${field.key}" data-key="${field.key}"
             value="${/^#[0-9a-f]{6}$/i.test(value || '') ? esc(value) : '#2563eb'}"
             style="width:46px;padding:2px">
      <input class="form-control setting-input" data-key="${field.key}" data-role="color-text"
             value="${esc(value ?? '')}" placeholder="留空跟随主题（也可填 #2563eb / red）"
             style="min-width:150px">
      ${swatches ? `<span class="color-chips">${swatches}
        <button type="button" class="color-chip none" data-color-chip=""
                title="清除，跟随主题">✕</button></span>` : ''}
    </div>`;
  } else if (field.type === 'range') {
    const num = Number(value);
    const val = Number.isFinite(num) ? num : (field.min || 0);
    control = `<div class="d-flex align-items-center gap-2">
      <input type="range" class="form-range setting-input" id="set-${field.key}"
             data-key="${field.key}" min="${field.min ?? 0}" max="${field.max ?? 100}"
             step="${field.step ?? 1}" value="${val}" style="flex:1">
      <span class="mono text-dim" style="min-width:46px;text-align:right"
            data-role="range-value">${val}${esc(field.unit || '')}</span>
    </div>`;
  } else if (field.type === 'select') {
    control = `<select class="form-select setting-input" id="set-${field.key}" data-key="${field.key}">
      ${(field.options || []).map(([k, label]) =>
        `<option value="${esc(k)}" ${k === value ? 'selected' : ''}>${esc(label)}</option>`).join('')}
    </select>`;
  } else if (field.type === 'palette') {
    // 整套配色一键套用：写进 4 个隐藏输入，保存时随其它设置一起提交
    control = `<input type="hidden" class="setting-input" id="set-${field.key}"
                       data-key="${field.key}" value="${esc(value ?? '')}">
      <div class="palette-grid">${THEME_PALETTES.map((p) => `
        <button type="button" class="palette-card${value === p.key ? ' active' : ''}"
                data-palette="${esc(p.key)}" title="${esc(p.hint || '')}">
          <span class="palette-swatch">
            <i style="background:${esc(p.bg_color || '#f4f6fb')}"></i>
            <i style="background:${esc(p.panel_color || '#ffffff')}"></i>
            <i style="background:${esc(p.accent_color)}"></i>
          </span>
          <span class="palette-name">${esc(p.label)}</span>
        </button>`).join('')}</div>`;
  } else if (field.type === 'anim') {
    const val = ['full', 'subtle', 'off'].includes(value) ? value : 'full';
    control = `<select class="form-select setting-input" id="set-${field.key}"
                       data-key="${field.key}">
      <option value="full" ${val === 'full' ? 'selected' : ''}>全部（推荐）</option>
      <option value="subtle" ${val === 'subtle' ? 'selected' : ''}>仅状态（少动效）</option>
      <option value="off" ${val === 'off' ? 'selected' : ''}>关闭（完全静止）</option>
    </select>`;
  } else if (field.type === 'background') {
    const current = (value || '').trim();
    const isRemote = /^https?:\/\//i.test(current);
    const previewSrc = current
      ? (isRemote ? current : '/api/appearance/background/file/' + encodeURIComponent(current))
      : '';
    control = `
      <input type="hidden" class="setting-input" id="set-${field.key}" data-key="${field.key}"
             value="${esc(current)}">
      <div class="row g-2 align-items-start">
        <div class="col-md-7">
          <div class="bg-drop" id="bg-drop">
            <i class="bi bi-cloud-arrow-up"></i> 拖拽图片到这里，或点此选择本地文件
          </div>
          <input type="file" id="bg-file" accept="image/*" style="display:none">
          <div class="input-group input-group-sm mt-2">
            <input class="form-control" id="bg-url" placeholder="或粘贴 http(s) 图片地址"
                   value="${isRemote ? esc(current) : ''}">
            <button class="btn btn-outline-light" type="button" id="bg-url-apply">使用</button>
          </div>
          <div class="d-flex gap-2 mt-2">
            <button class="btn btn-outline-danger btn-sm" type="button" id="bg-clear">
              <i class="bi bi-trash3"></i> 清除背景图</button>
            <span class="text-dim" style="font-size:11.5px;align-self:center"
                  id="bg-status">${current ? '当前：' + esc(current.slice(0, 46)) : '未设置背景图'}</span>
          </div>
        </div>
        <div class="col-md-5">
          ${previewSrc
            ? `<img class="bg-thumb" src="${esc(previewSrc)}" alt="背景预览">`
            : '<div class="bg-thumb d-flex align-items-center justify-content-center text-dim">'
              + '<i class="bi bi-image"></i></div>'}
        </div>
      </div>
      <div class="appearance-preview mt-3" id="appearance-preview">
        <div class="pv-bg"></div>
        <div class="pv-dim"></div>
        <div class="pv-card"></div>
        <span class="pv-title">动漫下载机 Anime Downloader</span>
      </div>`;
  } else if (field.type === 'logLevel') {
    control = `<select class="form-select setting-input" id="set-${field.key}" data-key="${field.key}">
      ${['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((lv) =>
        `<option value="${lv}" ${lv === value ? 'selected' : ''}>${lv}</option>`).join('')}
    </select>`;
  } else if (field.type === 'number') {
    control = `<input type="number" class="form-control setting-input" id="set-${field.key}"
      data-key="${field.key}" value="${esc(value ?? '')}" min="${field.min ?? ''}" max="${field.max ?? ''}">`;
  } else {
    control = `<input class="form-control setting-input" id="set-${field.key}" data-key="${field.key}"
      value="${esc(value ?? '')}" placeholder="${esc(field.placeholder || '')}">`;
  }
  return `<div class="${cls}">
    <label class="form-label" for="set-${field.key}">${esc(field.label)}</label>
    ${control}${hint}</div>`;
};

views.collectSettings = function () {
  const out = {};
  const colorKeys = new Set();
  $$('.setting-input').forEach((input) => {
    const key = input.dataset.key;
    if (!key) return;
    // 颜色是「色板 + 文本框」两个控件共用同一个 key，只取文本框的值
    if (input.dataset.role === 'color-text') {
      colorKeys.add(key);
      out[key] = input.value.trim();
      return;
    }
    if (input.type === 'color' && colorKeys.has(key)) return;
    if (input.dataset.type === 'bool') out[key] = input.checked;
    else if (input.type === 'range') out[key] = Number(input.value || 0);
    else if (input.type === 'number') out[key] = Number(input.value || 0);
    else out[key] = input.value;
  });
  return out;
};

/* ---------------------------------------------------------- 日志 */
views.appendLog = function (entry, autoScroll) {
  const box = $('#log-box');
  if (!box) return;
  const level = String(entry.level || 'INFO').toUpperCase();
  const line = document.createElement('div');
  line.className = 'log-line'
    + (level === 'WARNING' ? ' warn-line' : '')
    + (level === 'ERROR' || level === 'CRITICAL' ? ' err-line' : '');
  line.innerHTML = `<span class="t">${esc(entry.time || '')}</span>
    <span class="lv ${esc(level)}">${esc(level)}</span>
    <span class="msg">${views.logMessage(entry.message || '')}</span>`;
  box.appendChild(line);
  while (box.childElementCount > 800) box.removeChild(box.firstChild);
  const auto = $('#btn-log-autoscroll');
  if (autoScroll || (auto && auto.classList.contains('active'))) {
    box.scrollTop = box.scrollHeight;
  }
};

/* 日志正文着色：集数/大小/速度 用琥珀，路径用蓝色，方便扫读 */
views.logMessage = function (message) {
  let html = esc(message);
  html = html.replace(/([A-Za-z]:\\[^\s"]+|\.\.\.\/[^\s"]+)/g, '<span class="path">$1</span>');
  html = html.replace(/(第\s*\d+\s*[集话]|\d+(?:\.\d+)?\s*(?:MB|GB|KB|B)\b|\d+\s*个分片|命中\s*\d+\s*条)/g,
    '<span class="hl">$1</span>');
  return html;
};

views.renderLogs = function (logs) {
  const box = $('#log-box');
  box.innerHTML = '';
  const auto = $('#btn-log-autoscroll').classList.contains('active');
  (logs || []).forEach((entry) => views.appendLog(entry, false));
  if (auto) box.scrollTop = box.scrollHeight;
};

/* ---------------------------------------------------------- 文件夹 */
views.folderList = function (data) {
  const box = $('#folder-list');
  $('#folder-path').value = data.path || '';
  const rows = [];
  if (data.parent !== undefined && data.parent !== null && data.path) {
    rows.push(`<div class="dir-row" data-path="${esc(data.parent)}">
      <i class="bi bi-arrow-return-left"></i> <span>返回上一级</span></div>`);
  }
  rows.push(...(data.entries || []).map((item) => `
    <div class="dir-row" data-path="${esc(item.path)}">
      <i class="bi bi-folder-fill" style="color:var(--amber)"></i> <span>${esc(item.name)}</span></div>`));
  box.innerHTML = rows.length ? rows.join('')
    : '<div class="dir-row text-dim">该目录下没有子文件夹</div>';
  const free = data.free ? ` · 可用 ${fmtSize(data.free.free)} / 共 ${fmtSize(data.free.total)}` : '';
  $('#folder-info').innerHTML = data.is_drive_list ? '请选择盘符'
    : `${esc(data.path)}${free}${data.writable === false ? ' · <span style="color:var(--magenta)">不可写</span>' : ''}`;
};

/* ---------------------------------------------------------- 源测试报告 */
views.testReport = function (report) {
  if (report.apis) {
    const rows = report.apis.map((row) => `
      <div class="d-flex gap-2 align-items-center mb-1" style="border-bottom:1px solid var(--line-soft);padding:5px 0">
        <span class="led ${row.ok ? (row.m3u8 ? 'on' : 'err') : 'off'}"></span>
        <b style="min-width:88px">${esc(row.name)}</b>
        <span class="mono text-dim" style="font-size:11px">
          ${row.ok ? `${row.count} 条 · ${row.episodes} 集 · ${row.m3u8 ? 'm3u8 直链' : '需二次解析'}`
                   : esc(String(row.error || '不可用').slice(0, 70))}
        </span>
      </div>`).join('');
    return `<div class="mb-2"><b>接口自检</b>
      <span class="badge ${report.ok ? 'badge-soft' : 'badge-dim'}">${report.ok ? '可用' : '不可用'}</span>
      <span class="text-dim mono" style="font-size:11px">关键字 ${esc(report.keyword || '')}</span></div>
      ${rows}`;
  }
  const steps = (report.steps || []).map((step) => {
    const head = `<b>${esc(step.step)}</b>：${step.ok
      ? '<span style="color:var(--green)">成功</span>'
      : `<span style="color:var(--magenta)">失败</span> ${esc(step.error || '')}`}
      ${step.count !== undefined ? ` · ${step.count} 条` : ''}
      ${step.elapsed !== undefined ? ` · ${step.elapsed}s` : ''}`;
    let extra = '';
    if (step.sample && step.sample.length) {
      extra = `<div class="mono mt-1" style="font-size:11px">${step.sample.slice(0, 3).map((item) =>
        esc(`· ${item.name || item.episode || ''} ${item.url || ''}`)).join('<br>')}</div>`;
    }
    if (step.url) extra = `<div class="mono mt-1" style="font-size:11px">${esc(step.url)}</div>`;
    return `<div class="mb-2">${head}${extra}</div>`;
  }).join('');
  return `${report.ok
    ? '<div class="mb-2" style="color:var(--green)">整体测试通过</div>'
    : '<div class="mb-2" style="color:var(--magenta)">测试未完全通过</div>'}
    ${steps}${report.hint ? `<div style="color:var(--amber)" class="mt-2">${esc(report.hint)}</div>` : ''}`;
};
