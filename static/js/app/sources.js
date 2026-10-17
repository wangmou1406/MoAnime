/* ============================================================
   动漫下载机 · 源管理：列表、排序、测活测源、表单编辑、自动识别、导入导出
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
function renderSourcesAndPicker() {
  views.sources(state.sources);
  views.searchSourcePicker(state.sources);
  const total = $('#source-total');
  const enabled = $('#source-enabled');
  if (total) total.textContent = state.sources.length;
  if (enabled) enabled.textContent = state.sources.filter((s) => s.enabled).length;
  initSortable();
}

/** 标出当前生效的排序方式；「智能排序」下顺带把质量分徽标显示出来。 */

function markActiveSort(mode) {
  const group = $('#source-sort-group');
  if (!group) return;
  group.querySelectorAll('button[data-sort]').forEach((b) => {
    b.classList.toggle('active', b.dataset.sort === mode);
  });
  document.body.classList.toggle('show-quality', mode === 'smart');
  const hint = $('#source-sort-hint');
  if (!hint) return;
  if (mode === 'smart') {
    const top = state.sources.filter((s) => !s.ad_flag).slice(0, 3)
      .map((s) => `${s.name} ${Number(s.quality || 0).toFixed(0)}`).join(' · ');
    hint.textContent = top ? `质量分：${top}` : '';
  } else {
    hint.textContent = '';
  }
}

/** 质量采样：并发测活 + 抽样检索。
 *  智能排序里的「内容量 25 分」和「响应速度 20 分」依赖这次采样写入的数据；
 *  没有采样时这两项为 0，排序会退化成只看成功率。 */

async function runQualityProbe(button) {
  const keyword = prompt('用哪个关键词抽样检索？（用于统计各源的命中数）', '海贼王');
  if (keyword === null) return;
  const kw = keyword.trim() || '海贼王';
  if (button) button.disabled = true;
  const job = jobs.start('源质量采样', `关键词「${kw}」`);
  try {
    const resp = await fetch('/api/sources/probe-quality', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: kw }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = null;
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
          jobs.log(job, `共 ${obj.total} 个源，开始并发测活与抽样检索`);
        } else if (obj.type === 'row') {
          const r = obj.row || {};
          jobs.set(job, obj.index, null);
          jobs.log(job,
            `${r.source}：${r.ok ? '可用' : '不可用'} ${r.ms}ms · 命中 ${r.hits} 条`
            + (r.note ? ` — ${r.note}` : ''),
            r.ok ? 'ok' : 'warn');
        } else if (obj.type === 'error') {
          throw new Error(obj.error);
        } else if (obj.type === 'done') {
          done = obj;
        }
      }
    }
    if (!done) throw new Error('没有收到完整结果');
    if (done.sources && done.sources.length) {
      state.sources = done.sources;
      renderSourcesAndPicker();
    }
    const usable = (done.rows || []).filter((r) => r.ok).length;
    jobs.finish(job, `${done.total} 个源 · 可用 ${usable} 个 · 质量分已更新`);
    toastOk('质量采样完成，可以点「智能排序」了');
  } catch (err) {
    jobs.fail(job, String(err.message || err));
    toastErr('质量采样失败：' + (err.message || err));
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadSources() {
  const resp = await api('/api/sources');
  if (!resp.ok) { toastErr('加载动漫源失败：' + resp.error); return; }
  state.sources = resp.data.sources || [];
  state.sourceTypes = resp.data.types || state.sourceTypes;
  views.sources(state.sources);
  views.searchSourcePicker(state.sources);
  $('#source-total').textContent = state.sources.length;
  $('#source-enabled').textContent = state.sources.filter((s) => s.enabled).length;
  $('#source-summary').textContent = state.sources.length
    ? `优先级：${state.sources.map((s) => s.name).slice(0, 3).join(' > ')}${state.sources.length > 3 ? ' …' : ''}`
    : '暂无源';
  $('#nav-source-count').textContent = state.sources.filter((s) => s.enabled).length;
  initSortable();
  markActiveSort(state.sourceSortMode || 'manual');
}

function orderPayload() {
  return state.sources.map((s) => ({ id: s.id, enabled: s.enabled,
                                     hidden: s.hidden, ad_flag: s.ad_flag }));
}

async function persistSourceState() {
  const resp = await api('/api/sources/reorder', 'POST', { items: orderPayload() });
  if (resp.ok) {
    state.sources = resp.data.sources || state.sources;
    $('#source-enabled').textContent = state.sources.filter((s) => s.enabled).length;
    $('#nav-source-count').textContent = state.sources.filter((s) => s.enabled).length;
  }
  return resp;
}

/* ============================================================
   检索
   ============================================================ */

function initSortable() {
  const box = $('#source-list');
  if (typeof Sortable === 'undefined' || !box) return;
  if (state.sortable) state.sortable.destroy();
  state.sortable = Sortable.create(box, {
    // 整行都能拖（手柄仍保留作为视觉提示），避免"只有 15px 小图标能拖"的困扰
    handle: undefined,
    draggable: '.rack-row',
    filter: 'button, input, a, .move-ops',   // 点按钮/勾选框时不触发拖拽
    preventOnFilter: false,
    animation: 140,
    ghostClass: 'dragging',
    onEnd: async (evt) => {
      // 读实际渲染的行，选择器必须与 views.sources() 保持一致
      const ids = $$('#source-list .rack-row').map((row) => Number(row.dataset.id));
      const byId = new Map(state.sources.map((s) => [s.id, s]));
      state.sources = ids.map((id) => byId.get(id)).filter(Boolean);
      const resp = await persistSourceState();
      if (resp.ok) toastOk('优先级顺序已保存');
      else toastErr(resp.error);
      views.sources(state.sources);
      views.searchSourcePicker(state.sources);
      initSortable();
    },
  });
}

/** 键盘/按钮方式调整优先级（拖拽之外的稳妥选择）。 */

async function moveSource(id, delta) {
  const list = state.sources.slice();
  const index = list.findIndex((s) => s.id === id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  state.sources = list;
  views.sources(state.sources);
  views.searchSourcePicker(state.sources);
  initSortable();
  const resp = await persistSourceState();
  if (resp.ok) toastOk('优先级顺序已保存');
  else toastErr(resp.error);
}

async function testSource(id, showReport) {
  const source = state.sources.find((s) => s.id === id);
  const isoac = source && source.type === 'maccms';
  // 测试关键字：优先用该源自己配的 test_keyword，其余情况问用户（不预设具体作品）
  const configured = source && source.config && source.config.test_keyword;
  let keyword = (configured || '').trim() || (isoac ? '漫画' : '');
  if (!isoac) {
    keyword = prompt(`测试「${source ? source.name : id}」，输入检索关键字：`, keyword || '漫画');
    if (keyword === null) return;
    keyword = keyword.trim() || '漫画';
  }
  const job = jobs.start(`测试源 · ${source ? source.name : id}`, {
    total: 1, message: `检索关键字「${keyword}」`,
  });
  const resp = await api(`/api/sources/${id}/test`, 'POST', { keyword });
  if (!resp.ok) { jobs.fail(job, resp.error); return; }
  const report = resp.data;
  const steps = (report.steps || []).filter((s) => s.ok).length;
  if (report.ok) jobs.finish(job, `通过（${steps} 个步骤成功）`);
  else jobs.fail(job, report.error || '未完全通过，详情见报告');
  if (showReport) showInfo(`源测试报告 · ${source ? source.name : ''}`, views.testReport(report));
  await loadSources();
}

async function checkAllSources() {
  const sources = state.sources.slice();
  if (!sources.length) { toastWarn('还没有动漫源'); return; }
  const button = $('#btn-source-check-all');
  if (button) button.disabled = true;
  const job = jobs.start('全部测活', { total: sources.length });
  jobs.log(job, `共 ${sources.length} 个源，逐个请求站点首页`);

  const results = [];
  for (const source of sources) {
    const resp = await api(`/api/sources/${source.id}/check`, 'GET');
    const node = (resp.data || {});
    const good = Boolean(resp.ok && node.ok);
    const detail = good
      ? `HTTP ${node.status} · ${fmtSize(node.length)} · ${node.elapsed}s`
      : (node.error || resp.error || '不可访问');
    results.push({ name: source.name, ok: good, detail });
    jobs.set(job, results.length, null);
    jobs.log(job, `${source.name}：${good ? '可访问' : detail}`, good ? 'ok' : 'warn');
  }
  if (button) button.disabled = false;

  const ok = results.filter((r) => r.ok).length;
  const bad = results.length - ok;
  if (bad === 0) jobs.finish(job, `${ok}/${results.length} 个源全部可访问`);
  else jobs.finish(job, `${ok}/${results.length} 可访问，${bad} 个不可访问`);

  // 明细直接展示，避免只看到一个数字不知道坏在哪
  showInfo('测活结果', `
    <div class="d-flex align-items-center gap-2 mb-3">
      <span class="badge ${ok === results.length ? 'badge-soft' : 'badge-dim'}">
        ${ok}/${results.length} 可访问</span>
      <span class="text-dim mono" style="font-size:11px">
        测活 = 请求“站点/接口地址”看是否可达；解析剧集请用“测试”</span>
    </div>
    ${results.map((r) => `
      <div class="d-flex gap-2 align-items-center mb-1"
           style="border-bottom:1px solid var(--line-soft);padding:5px 0">
        <span class="led ${r.ok ? 'on' : 'err'}"></span>
        <b style="min-width:92px">${esc(r.name)}</b>
        <span class="mono text-dim" style="font-size:11px">${esc(String(r.detail).slice(0, 96))}</span>
      </div>`).join('')}`);
  await loadSources();
}

/* ---------------------------------------------------------- 源编辑器 */

function fillSourceTypeSelect() {
  $('#src-type').innerHTML = state.sourceTypes.map((type) =>
    `<option value="${type.type}">${esc(type.display_name)}</option>`).join('');
  updateTypeDescription();
}

function updateTypeDescription() {
  const type = state.sourceTypes.find((t) => t.type === $('#src-type').value);
  $('#src-type-desc').textContent = type ? type.description : '';
}

function openSourceEditor(id, prefill) {
  state.editingSourceId = id;
  const source = id ? state.sources.find((s) => s.id === id) : null;
  const data = prefill || source || {
    name: '', type: state.sourceTypes[0] ? state.sourceTypes[0].type : 'maccms',
    base_url: '', note: '', enabled: true,
    config: { api: '', apis: [], request_timeout: 20, test_keyword: '',
              search: { mode: 'query', path: '/search', query_param: 'wd', pages: 1 },
              episodes: {}, video: {} },
  };
  $('#modal-source-title').textContent = id ? `编辑动漫源 · ${data.name}` : '添加动漫源';
  fillSourceForm(data);
  $('#src-test-output').style.display = 'none';
  showModal('modal-source');
}

function fillSourceForm(data) {
  const config = data.config || {};
  $('#src-name').value = data.name || '';
  $('#src-type').value = data.type || 'maccms';
  $('#src-base').value = data.base_url || config.base_url || '';
  $('#src-note').value = data.note || '';
  $('#src-enabled').checked = data.enabled !== false;
  $('#src-api').value = config.api || '';
  $('#src-apis').value = (config.apis && config.apis.length) ? JSON.stringify(config.apis) : '';
  updateTypeDescription();

  const search = config.search || {};
  $('#sr-mode').value = search.mode || 'query';
  $('#sr-path').value = search.path || search.url || '';
  $('#sr-param').value = search.query_param || 'wd';
  $('#sr-method').value = (search.method || 'GET').toUpperCase();
  $('#sr-pages').value = search.pages || 1;
  $('#sr-pageparam').value = search.page_param || 'page';
  $('#sr-urlpattern').value = search.url_pattern || '';
  $('#sr-item').value = search.item || '';
  $('#sr-url').value = search.url || 'a@href';
  $('#sr-name').value = search.name || '';
  $('#sr-cover').value = search.cover || 'img@data-src';
  $('#sr-update').value = search.update || '';

  const episodes = config.episodes || {};
  $('#ep-item').value = episodes.item || '';
  $('#ep-url').value = episodes.url || '@href';
  $('#ep-name').value = episodes.name || '';
  $('#ep-regex').value = episodes.url_regex || '';
  $('#ep-urlbase').value = episodes.url_base || '';
  $('#ep-reverse').checked = !!episodes.reverse;

  const video = config.video || {};
  $('#vd-type').value = video.type || 'auto';
  $('#vd-patterns').value = (video.patterns || []).join('\n');
  $('#vd-headers').value = video.headers ? JSON.stringify(video.headers) : '';
  const playlist = video.playlist || {};
  $('#vd-pl-item').value = playlist.item || '';
  $('#vd-pl-name').value = playlist.name || '';
  $('#vd-pl-url').value = playlist.url || '@href';

  $('#ad-encoding').value = config.encoding || '';
  $('#ad-timeout').value = config.request_timeout || '';
  $('#ad-keyword').value = config.test_keyword || '';
  $('#ad-headers').value = config.headers ? JSON.stringify(config.headers) : '';
  $('#ad-json').value = jsonPretty(config);
}

function collectSourceForm() {
  const patterns = $('#vd-patterns').value.split('\n').map((line) => line.trim()).filter(Boolean);
  const config = {
    base_url: $('#src-base').value.trim(),
    encoding: $('#ad-encoding').value.trim(),
    search: {
      mode: $('#sr-mode').value,
      path: $('#sr-path').value.trim(),
      query_param: $('#sr-param').value.trim(),
      method: $('#sr-method').value,
      pages: Number($('#sr-pages').value || 1),
      page_param: $('#sr-pageparam').value.trim(),
      url_pattern: $('#sr-urlpattern').value.trim(),
      item: $('#sr-item').value.trim(),
      url: $('#sr-url').value.trim(),
      name: $('#sr-name').value.trim(),
      cover: $('#sr-cover').value.trim(),
      update: $('#sr-update').value.trim(),
    },
    episodes: {
      item: $('#ep-item').value.trim(),
      url: $('#ep-url').value.trim(),
      name: $('#ep-name').value.trim(),
      url_regex: $('#ep-regex').value.trim(),
      url_base: $('#ep-urlbase').value.trim(),
      reverse: $('#ep-reverse').checked,
    },
    video: { type: $('#vd-type').value, patterns },
  };
  if ($('#vd-pl-item').value.trim()) {
    config.video.playlist = {
      item: $('#vd-pl-item').value.trim(),
      name: $('#vd-pl-name').value.trim(),
      url: $('#vd-pl-url').value.trim(),
    };
  }
  if ($('#vd-headers').value.trim()) config.video.headers = parseJsonInput($('#vd-headers').value, {});
  if ($('#ad-headers').value.trim()) config.headers = parseJsonInput($('#ad-headers').value, {});
  if ($('#ad-timeout').value) config.request_timeout = Number($('#ad-timeout').value);
  if ($('#ad-keyword').value.trim()) config.test_keyword = $('#ad-keyword').value.trim();

  const api = $('#src-api').value.trim();
  if (api) config.api = api;
  const apisRaw = $('#src-apis').value.trim();
  if (apisRaw) {
    const parsed = parseJsonInput(apisRaw, []);
    config.apis = Array.isArray(parsed) ? parsed : [parsed];
  } else {
    config.apis = config.apis || [];
  }

  return {
    name: $('#src-name').value.trim(),
    type: $('#src-type').value,
    base_url: config.base_url,
    note: $('#src-note').value.trim(),
    enabled: $('#src-enabled').checked,
    config,
  };
}

async function saveSource() {
  let payload;
  try { payload = collectSourceForm(); } catch (err) { toastErr(err.message); return; }
  if (!payload.name) { toastErr('请填写源名称'); return; }
  if (!payload.base_url) { toastErr('请填写站点网址'); return; }

  const resp = state.editingSourceId
    ? await api(`/api/sources/${state.editingSourceId}`, 'PUT', payload)
    : await api('/api/sources', 'POST', payload);
  if (!resp.ok) { toastErr(resp.error); return; }
  toastOk(state.editingSourceId ? '源已更新' : '源已添加');
  hideModal('modal-source');
  await loadSources();
}

async function previewDraft(step) {
  let payload;
  try { payload = collectSourceForm(); } catch (err) { toastErr(err.message); return; }
  const body = { source: payload, step, keyword: $('#ad-keyword').value.trim() || '漫画' };
  if (step === 'episodes' || step === 'video') {
    body.anime_url = prompt('请输入动漫详情页地址：',
      (state.currentAnime && state.currentAnime.url) || '') || '';
    if (!body.anime_url) return;
  }
  if (step === 'video') {
    body.episode_url = prompt('请输入剧集播放页地址：', '') || '';
    if (!body.episode_url) return;
  }
  const output = $('#src-test-output');
  output.style.display = '';
  output.textContent = '测试中…';
  const resp = await api('/api/sources/preview', 'POST', body);
  output.textContent = jsonPretty(resp.ok ? resp.data : { error: resp.error });
  if (resp.ok) toastOk(`${step} 测试完成`);
  else toastErr(resp.error);
}

/* ---------------------------------------------------------- 自动探测 */

function openDetectWizard() {
  showInfo('自动探测动漫源', `
    <div class="hint-box mb-3">
      输入任意动漫站首页地址，程序会识别检索表单、结果列表容器、剧集链接与视频地址正则，
      生成一份可直接使用的源配置草稿。
    </div>
    <div class="mb-2">
      <label class="form-label">站点网址</label>
      <input class="form-control" id="detect-url" placeholder="https://www.example.com">
    </div>
    <div class="mb-3">
      <label class="form-label">抓取页面（可选，默认首页）</label>
      <input class="form-control" id="detect-page" placeholder="留空则使用上面的网址">
    </div>
    <button class="btn btn-primary btn-sm" id="btn-detect-run"><i class="bi bi-magic"></i> 开始探测</button>
    <div id="detect-result" class="mt-3"></div>`);

  $('#btn-detect-run').addEventListener('click', async () => {
    const url = $('#detect-url').value.trim();
    if (!url) { toastWarn('请输入站点网址'); return; }
    const box = $('#detect-result');
    box.innerHTML = '<div class="text-dim"><span class="spinner-border spinner-border-sm"></span> 正在抓取并分析页面…</div>';
    const resp = await api('/api/sources/detect', 'POST',
      { url, page_url: $('#detect-page').value.trim() });
    if (!resp.ok) { box.innerHTML = `<div class="text-danger">${esc(resp.error)}</div>`; return; }
    const data = resp.data;
    const candidates = (data.candidates || []).map((item, index) => `
      <div class="mb-2 p-2" style="border:1px solid var(--border);border-radius:9px">
        <div><b>候选 ${index + 1}</b> · 容器 <code>${esc(item.container)}</code> · ${item.count} 条</div>
        <div class="mono text-dim" style="font-size:11.5px">剧集选择器：${esc(item.item)}<br>
          链接正则：${esc(item.url_regex || '（无）')}</div>
        <div class="mono mt-1" style="font-size:11.5px">${(item.samples || []).map((s) =>
          esc(`· ${s.name} -> ${s.url}`)).join('<br>')}</div>
      </div>`).join('');
    box.innerHTML = `
      <div class="mb-2"><b>页面标题：</b>${esc(data.page_title || '（无）')}</div>
      <div class="mb-2"><b>检索参数：</b><code>${esc(data.search.query_param || '')}</code>
        · 路径 <code>${esc(data.search.path || '')}</code> · 方法 ${esc(data.search.method || 'GET')}</div>
      <div class="mb-2"><b>结果容器：</b><code>${esc(data.search.item || '（未识别）')}</code></div>
      <div class="mb-2"><b>剧集规则：</b><code>${esc(data.episodes.item || '（未识别）')}</code>
        ${data.episodes.url_regex ? ` · 正则 <code>${esc(data.episodes.url_regex)}</code>` : ''}</div>
      ${candidates ? `<div class="mt-3"><b>剧集候选分组</b></div>${candidates}` : ''}
      ${(data.warnings || []).length ? `<div class="hint-box mt-3">
        ${data.warnings.map((w) => esc(w)).join('<br>')}</div>` : ''}
      <div class="d-flex gap-2 mt-3">
        <button class="btn btn-primary btn-sm" id="btn-detect-use"><i class="bi bi-check2"></i> 用该草稿创建源</button>
        <button class="btn btn-outline-light btn-sm" id="btn-detect-json"><i class="bi bi-braces"></i> 查看 JSON</button>
      </div>`;
    $('#btn-detect-use').addEventListener('click', () => {
      hideModal('modal-info');
      openSourceEditor(null, {
        name: (data.page_title || '新动漫源').slice(0, 24),
        type: 'generic',
        base_url: data.base_url,
        enabled: true,
        config: data.config,
      });
    });
    $('#btn-detect-json').addEventListener('click', () => {
      showInfo('探测生成的配置 JSON',
        `<pre class="mono" style="white-space:pre-wrap">${esc(jsonPretty(data.config))}</pre>`);
    });
  });
}

/* ---------------------------------------------------------- 导入导出 */

function openImportDialog() {
  showInfo('导入动漫源配置', `
    <div class="hint-box mb-3">支持粘贴单个源 JSON 对象，或 <code>{"sources":[...]}</code> 数组格式。</div>
    <textarea class="code-area" id="import-text" spellcheck="false"
      placeholder='{"sources": [{"name":"示例源","type":"generic","base_url":"https://example.com","config":{}}]}'></textarea>
    <div class="d-flex gap-2 mt-2 align-items-center">
      <button class="btn btn-primary btn-sm" id="btn-import-run"><i class="bi bi-upload"></i> 开始导入</button>
      <div class="form-check form-switch">
        <input class="form-check-input" type="checkbox" id="import-overwrite" checked>
        <label class="form-check-label" for="import-overwrite" style="font-size:12.5px">同名源覆盖</label>
      </div>
    </div>`);
  $('#btn-import-run').addEventListener('click', async () => {
    let payload;
    try {
      const parsed = JSON.parse($('#import-text').value.trim());
      payload = Array.isArray(parsed) ? parsed : (parsed.sources || [parsed]);
    } catch (err) { toastErr('JSON 解析失败：' + err.message); return; }
    const resp = await api('/api/sources/import', 'POST',
      { sources: payload, overwrite: $('#import-overwrite').checked });
    if (!resp.ok) { toastErr(resp.error); return; }
    toastOk(`导入完成：新增 ${resp.data.created}，更新 ${resp.data.updated}，失败 ${resp.data.failed.length}`);
    hideModal('modal-info');
    await loadSources();
  });
}

/* ============================================================
   字幕
   ============================================================ */
