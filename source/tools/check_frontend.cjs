/**
 * 在最小 DOM 桩上真实执行 app.js 的启动流程，捕获「启动即抛错」。
 * 这比语法检查更能发现「设置打不开」这类问题。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const BASE = path.resolve(__dirname, '..');

// ---------- 最小 DOM 桩 ----------
function makeStyle() {
  const store = {};
  return new Proxy(store, {
    get(target, key) {
      if (key === 'setProperty') return (k, v) => { target[k] = v; };
      if (key === 'removeProperty') return (k) => { delete target[k]; };
      if (key === 'getPropertyValue') return (k) => target[k] || '';
      if (key === 'cssText') return '';
      return target[key] === undefined ? '' : target[key];
    },
    set(target, key, value) { target[key] = value; return true; },
  });
}

function makeEl(id) {
  const el = {
    id: id || '',
    tagName: 'DIV',
    style: makeStyle(),
    dataset: {},
    children: [],
    childElementCount: 0,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (on) { this._s.add(c); } else { this._s.delete(c); } return this._s.has(c); },
      contains(c) { return this._s.has(c); },
    },
    value: '',
    checked: false,
    textContent: '',
    innerHTML: '',
    disabled: false,
    title: '',
    files: [],
    addEventListener() {},
    removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { return c; },
    insertAdjacentHTML() {},
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    getAttribute() { return null; },
    setAttribute() {},
    removeAttribute() {},
    scrollIntoView() {},
    focus() {},
    click() {},
    play() { return Promise.resolve(); },
    pause() {},
    load() {},    canPlayType() { return ''; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
    contains() { return false; },
  };
  return el;
}

const elementCache = new Map();

// 记录 document 上注册的事件监听，便于验证「boot 是否真的挂在 DOMContentLoaded 上」
const documentListeners = new Map();

const document = {
  documentElement: makeEl('html'),
  body: makeEl('body'),
  title: '',
  addEventListener(type, handler) {
    console.log('[桩] document 事件注册:', type);
    if (!documentListeners.has(type)) documentListeners.set(type, []);
    documentListeners.get(type).push(handler);
  },
  getElementById(id) {
    if (!elementCache.has(id)) elementCache.set(id, makeEl(id));
    return elementCache.get(id);
  },
  querySelector(sel) {
    // '#id' 形式要指向与 getElementById 相同的对象，否则读不到写入的内容
    const m = /^#([\w-]+)$/.exec(sel);
    if (m) return this.getElementById(m[1]);
    if (!elementCache.has('sel:' + sel)) elementCache.set('sel:' + sel, makeEl('sel:' + sel));
    return elementCache.get('sel:' + sel);
  },
  querySelectorAll() { return []; },
  createElement(tag) { const e = makeEl(); e.tagName = String(tag).toUpperCase(); return e; },
  addEventListener() {},
  removeEventListener() {},
};

const window = {
  document,
  location: { search: '', hash: '', origin: 'http://127.0.0.1:8848', href: 'http://127.0.0.1:8848/' },
  localStorage: { _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } },
  addEventListener() {},
  innerWidth: 1300,
  innerHeight: 800,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  URLSearchParams, URL, TextDecoder, TextEncoder, AbortController,
  JSON, Math, Date, Object, Array, String, Number, Boolean, Error, RegExp, Map, Set,
  Promise, Symbol, Intl, isNaN, parseInt, parseFloat,
  fetch: () => Promise.reject(new Error('stub')),
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  CSS: { supports: () => true },
  navigator: { clipboard: { writeText: () => Promise.resolve() }, userAgent: 'node' },
  history: { replaceState() {} },
  io: () => ({ on() {}, emit() {}, disconnect() {} }),
};

const ctx = {
  window, document, console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: window.localStorage, location: window.location,
  navigator: window.navigator, history: window.history,
  fetch: window.fetch, URLSearchParams, URL, TextDecoder, AbortController,
  JSON, Math, Date, Object, Array, String, Number, Boolean, Error, RegExp, Map, Set,
  Promise, Symbol, Intl, isNaN, parseInt, parseFloat,
  CSS: window.CSS, getComputedStyle: window.getComputedStyle,
  requestAnimationFrame: window.requestAnimationFrame,
  bootstrap: { Modal: function () { return { show() {}, hide() {} }; }, Toast: function () { return { show() {} }; } },
  Hls: undefined,
  alert: () => {}, confirm: () => false, prompt: () => null,
};
ctx.globalThis = ctx;
ctx.self = ctx;
ctx.window.document = document;
ctx.document = document;

vm.createContext(ctx);

// 依次加载 util.js / views.js / app.js
const errors = [];
// 主逻辑已按职责拆到 static/js/app/ 下（顺序与 index.html 一致）
const JS_FILES = [
  'static/js/util.js',
  'static/js/views.js',
  'static/js/app/boot.js',
  'static/js/app/appearance.js',
  'static/js/app/settings.js',
  'static/js/app/sources.js',
  'static/js/app/search.js',
  'static/js/app/player.js',
  'static/js/app/subtitles.js',
  'static/js/app/tasks.js',
  'static/js/app/organize.js',
  'static/js/app/events.js',
  'static/js/app/main.js',
];
for (const rel of JS_FILES) {
  const src = fs.readFileSync(path.join(BASE, rel), 'utf8');
  try {
    vm.runInContext(src, ctx, { filename: rel });
console.log('  [执行成功] ' + rel);
    if (rel === 'static/js/app/main.js') {
      // app.js 结尾会注册 DOMContentLoaded，这里立刻核对是否走的是我们的桩
      console.log('  [桩] 此刻已记录的事件:', [...documentListeners.keys()].join(',') || '(无)');
      console.log('  [桩] ctx.document 是同一个对象:', ctx.document === document);
    }
  } catch (err) {
    console.log('  [执行抛错] ' + rel + ' -> ' + err.message);
    const stack = (err.stack || '').split('\n').slice(1, 4).join('\n      ');
    console.log('      ' + stack);
    errors.push(rel);
  }
}

console.log();
// 独立验证：boot 必须注册在 DOMContentLoaded 上。
// 用干净的沙箱单独跑一次 app.js 的最后一行会比在完整桩里更可靠
// （完整桩里 window/document 的互相引用容易让记录器失效）。
(() => {
  const probeCalls = [];
  const probeDoc = { addEventListener(type) { probeCalls.push(type); } };
  const probeCtx = {
    console, document: probeDoc, window: { document: probeDoc },
    setTimeout, clearTimeout, setInterval, clearInterval,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    location: { search: '', hash: '' },
    JSON, Math, Date, Object, Array, String, Number, Boolean, Error, RegExp,
    Map, Set, Promise, isNaN, parseInt, parseFloat, URLSearchParams, URL,
  };
  probeCtx.globalThis = probeCtx;
  vm.createContext(probeCtx);
  // 独立探针：把拆分后的所有主逻辑文件拼起来，验证 DOMContentLoaded 注册
  const appSrc = JS_FILES.filter((f) => f.startsWith('static/js/app/'))
    .map((f) => fs.readFileSync(path.join(BASE, f), 'utf8')).join('\n');
  let ok = false;
  let detail = '';
  try {
    // 跑完整 app.js（顶层只有函数声明与最后那行注册，不会触碰 DOM）
    vm.runInContext(appSrc, probeCtx, { filename: 'app.js-probe' });
    ok = probeCalls.includes('DOMContentLoaded');
    detail = probeCalls.join(',') || '(未注册任何事件)';
  } catch (e) {
    detail = '抛错: ' + e.message;
  }
  console.log('  [%s] boot 注册在 DOMContentLoaded 上（%s）',
    ok ? 'OK  ' : 'FAIL', detail);
  if (!ok) errors.push('boot 未注册到 DOMContentLoaded');
})();

// 在 ctx 内求值（const 声明不会挂到 ctx 上，必须用表达式读取）
function evalIn(expr) {
  try { return vm.runInContext(expr, ctx); } catch (e) { return 'ERR: ' + e.message; }
}

const checks = [
  ['jobs 模块', "typeof jobs === 'object' && jobs !== null"],
  ['installEvents 函数', "typeof installEvents === 'function'"],
  ['bindAppearanceControls 函数', "typeof bindAppearanceControls === 'function'"],
  ['factoryReset 函数', "typeof factoryReset === 'function'"],
  ['orderPayload 函数', "typeof orderPayload === 'function'"],
  ['applyAppearance 函数', "typeof applyAppearance === 'function'"],
  ['applyTheme 函数', "typeof applyTheme === 'function'"],
  ['views.settings 渲染器', "typeof views.settings === 'function'"],
  ['loadSettings 函数', "typeof loadSettings === 'function'"],
];
for (const [label, expr] of checks) {
  const v = evalIn(expr);
  console.log('  [%s] %s', v === true ? 'OK  ' : 'FAIL', label + (v === true ? '' : ' -> ' + v));
}

// 真正渲染一次设置页 + 绑定外观控件，验证不抛错
console.log();
const renderTest = evalIn(`(function () {
  try {
    var sample = {
      download_dir: 'D:/dl', temp_dir: 'D:/dl/.tmp', naming_scheme: 'simple',
      default_season: 1, filename_max_length: 120, concurrent_tasks: 3,
      segment_threads: 8, search_concurrency: 5, max_retries: 3,
      request_timeout: 15, source_timeout: 15, proxy: '', verify_ssl: false,
      user_agent: 'UA', remux_to_mp4: true, delete_ts_after_remux: true,
      keep_temp_segments: false, verify_video: true, theme: 'dark',
      bg_image: '', bg_blur: 10, bg_dim: 40, bg_panel_alpha: 80, bg_fit: 'cover',
      title_text: '测试', title_color: '#ff9f45', log_level: 'INFO',
      web_port: 8848, web_host: '127.0.0.1', auto_open_browser: false,
      merge_duplicates: false, auto_detect_sources: true, search_history_limit: 30,
    };
    views.settings(sample, {});
    var html = document.getElementById('settings-form').innerHTML || '';
    var wanted = ['bg-drop', 'bg-file', 'bg-url-apply', 'bg-clear',
                  'appearance-preview', 'btn-factory-reset',
                  // 新增：配色方案、4 个颜色项、动效档位
                  'set-palette', 'set-accent_color', 'set-bg_color',
                  'set-panel_color', 'set-panel_2_color', 'set-animations'];
    var hasControls = wanted.filter(function (id) {
      return html.indexOf('id="' + id + '"') >= 0;
    });
    var palettes = (html.match(/class="palette-card/g) || []).length;
    bindAppearanceControls();
    applyTheme('dark');
    applyAppearance(sample);
    // 颜色与动效应当真的落到 <html> 上
    var root = document.documentElement;
    applyAppearance({ accent_color: '#db2777', bg_color: '#101010',
                      panel_color: '#181818', panel_2_color: '#222222',
                      animations: 'off' });
    var applied = root.style.getPropertyValue('--primary') === '#db2777'
      && root.style.getPropertyValue('--bg') === '#101010'
      && root.dataset.anim === 'off';
    // 清空后应当把内联变量移除，交回主题
    applyAppearance({ accent_color: '', bg_color: '', panel_color: '',
                      panel_2_color: '', animations: 'full' });
    var cleared = !root.style.getPropertyValue('--primary')
      && !root.style.getPropertyValue('--bg')
      && root.dataset.anim === 'full';
    return 'RENDERED controls=' + hasControls.length + '/' + wanted.length
      + ' palettes=' + palettes + ' htmlLen=' + html.length
      + ' 配色生效=' + (applied ? 'OK' : 'FAIL')
      + ' 清空回主题=' + (cleared ? 'OK' : 'FAIL');
  } catch (e) {
    return 'THREW: ' + e.message;
  }
})()`);
console.log('  设置页渲染 + 外观绑定:', renderTest);

// 搜索结果里的「直链可下载」必须是可点按钮（曾经只是不可点的 span）
// 注意：views.slot 现在接收「同一个标题的多个源条目」这种变体结构
console.log('  搜索结果按钮:', evalIn(`(function () {
  try {
    var entry = function (extra) {
      return Object.assign({ name: '测试番剧', url: 'http://x/detail/1', cover: '',
        source_id: 1, source: '测试源', playable: true, episode_count: 12 }, extra || {});
    };
    var html = views.slot({ name: '测试番剧', entries: [entry()] }, 0);
    var hasBtn = html.indexOf('slot-flag-btn') >= 0
      && html.indexOf('data-act="quick-download"') >= 0
      && html.indexOf('<button') >= 0;
    var warnSlot = views.slot(
      { name: 'x', entries: [entry({ name: 'x', url: 'http://x', playable: false })] }, 0);
    var warnOk = warnSlot.indexOf('仅分享页') >= 0 && warnSlot.indexOf('slot-flag-btn') < 0;
    // 多个源有同一部时，应当渲染出可点的源标签
    var multi = views.slot({ name: '测试番剧', entries: [
      entry(), entry({ source: '另一个源', source_id: 2, url: 'http://y/detail/2' }),
    ] }, 0);
    var chips = (multi.match(/class="src-chip/g) || []).length;
    return 'quickDownload=' + (hasBtn ? 'OK' : 'FAIL')
      + ' shareOnly=' + (warnOk ? 'OK' : 'FAIL')
      + ' multiSource=' + (chips === 2 ? 'OK(2)' : 'FAIL(' + chips + ')');
  } catch (e) { return 'THREW: ' + e.message; }
})()`));

// 检索结果：失败源清单只在「完全没结果」时显示，有结果时不能挂在列表下面
console.log('  检索结果失败提示:', evalIn(`(function () {
  try {
    var mkItem = function (name, src, sid) {
      return { name: name, url: 'http://x/' + sid, cover: '', source: src,
               source_id: sid, playable: true, episode_count: 12 };
    };
    var withResult = {
      keyword: '测试', total: 2, source_count: 2, elapsed: 1,
      groups: [
        { source: '源A', source_id: 1, ok: true, results: [mkItem('测试番剧', '源A', 1)],
          count: 1, elapsed: 1 },
        { source: '源B', source_id: 2, ok: true, results: [mkItem('测试番剧', '源B', 2)],
          count: 1, elapsed: 1 },
      ],
      errors: [{ source: '源C', error: 'Connection aborted' }],
    };
    views.searchResults(withResult);
    var htmlOk = document.getElementById('search-results').innerHTML || '';
    var summaryOk = document.getElementById('search-summary').textContent || '';

    var noResult = {
      keyword: '不存在的东西', total: 0, source_count: 2, elapsed: 1,
      groups: [
        { source: '源A', source_id: 1, ok: false, results: [], count: 0,
          error: 'Connection aborted' },
        { source: '源B', source_id: 2, ok: false, results: [], count: 0,
          error: 'Connection aborted' },
      ],
      errors: [{ source: '源A', error: 'Connection aborted' },
               { source: '源B', error: 'Connection aborted' }],
    };
    views.searchResults(noResult);
    var htmlNo = document.getElementById('search-results').innerHTML || '';

    var hiddenWithResult = htmlOk.indexOf('以下源检索失败') < 0;
    var shownWithout = htmlNo.indexOf('以下源检索失败') >= 0;
    var countInSummary = summaryOk.indexOf('1 个源未返回') >= 0;
    return 'hiddenWhenHasResult=' + (hiddenWithResult ? 'OK' : 'FAIL')
      + ' shownWhenEmpty=' + (shownWithout ? 'OK' : 'FAIL')
      + ' countInSummary=' + (countInSummary ? 'OK' : 'FAIL');
  } catch (e) { return 'THREW: ' + e.message; }
})()`));

// 任务行原地更新：结构没变时必须返回 true（否则每次轮询都重建 DOM → 列表抽搐）
console.log('  任务行原地更新:', evalIn(`(function () {
  try {
    var mkTask = function (progress, speed) {
      return { id: 't1', anime_name: '测试番剧', episode_name: '第01集', source: '测试源',
               status: 'running', progress: progress, speed: speed, done_bytes: 4096,
               total_bytes: 8192, segments_done: Math.round(progress), segments_total: 100,
               message: '', error: '', save_path: '' };
    };
    // 先整体渲染一次，从结果里取出 taskKey（它是模块内私有函数）
    views.tasks([mkTask(10, 1)]);
    var rendered = document.getElementById('task-list').innerHTML || '';
    var m = /data-task-key="([^"]*)"/.exec(rendered);
    if (!m) return 'FAIL 渲染结果里没有 data-task-key';

    var row = document.createElement('div');
    row.className = 'task-row';
    row.dataset.id = 't1';
    row.dataset.taskKey = m[1];
    var statusLine = document.createElement('div');
    statusLine.className = 'status-line';
    var left = document.createElement('span'); left.textContent = '旧值';
    var right = document.createElement('span'); right.textContent = '0%';
    statusLine.appendChild(left); statusLine.appendChild(right);
    var bar = document.createElement('div');
    var fill = document.createElement('i');
    bar.appendChild(fill);
    row.appendChild(statusLine); row.appendChild(bar);
    row.querySelector = function (sel) {
      if (sel === '.status-line') return statusLine;
      if (sel === '.bar') return bar;
      return null;
    };
    bar.querySelector = function (sel) { return sel === 'i' ? fill : null; };

    var box = document.getElementById('task-list');
    box.querySelectorAll = function () { return [row]; };
    var hit = views.updateTaskRows([mkTask(42, 2097152)]);
    return 'hit=' + (hit ? 'true(OK)' : 'false(FAIL)')
      + ' width=' + (fill.style.width || '未写入')
      + ' 百分比=' + right.textContent;
  } catch (e) { return 'THREW: ' + e.message; }
})()`));

// ------------------------------------------------------------------
// 真实跑一遍 boot()：这一步能替代浏览器验证「设置打不开」这类问题
// ------------------------------------------------------------------
console.log();
const API = {
  '/api/meta': { version: '1.0.0', ffmpeg: { available: true, path: 'tools/ffmpeg.exe' },
                 source_types: [], naming_schemes: { simple: '简洁' } },
  '/api/settings': { settings: {
      download_dir: 'D:/dl', temp_dir: 'D:/dl/.tmp', naming_scheme: 'simple',
      default_season: 1, filename_max_length: 120, concurrent_tasks: 3,
      segment_threads: 8, search_concurrency: 5, max_retries: 3, request_timeout: 15,
      source_timeout: 15, proxy: '', verify_ssl: false, user_agent: 'UA',
      remux_to_mp4: true, delete_ts_after_remux: true, keep_temp_segments: false,
      verify_video: true, theme: 'light', bg_image: '', bg_blur: 0, bg_dim: 0,
      bg_panel_alpha: 88, bg_fit: 'cover', title_text: '', title_color: '',
      log_level: 'INFO', web_host: '127.0.0.1', web_port: 8848,
      auto_open_browser: false, merge_duplicates: false, auto_detect_sources: true,
      search_history_limit: 30 },
    schemes: { simple: '简洁' } },
  '/api/sources': { sources: [{ id: 1, name: '测试源', type: 'maccms', enabled: true,
                                hidden: false, builtin: true, ad_flag: false,
                                base_url: 'http://x', note: '', config: {} }],
                    types: [] },
  '/api/disclaimer': { accepted: true, version: 1, current_version: 1 },
  '/api/stats': {},
  '/api/logs': { lines: [] },
  '/api/search/history': [],
  '/api/tasks': { tasks: [], stats: { total: 0, running: 0, queued: 0, paused: 0,
                                      completed: 0, failed: 0, canceled: 0,
                                      speed: 0, total_bytes: 0 } },
  '/api/logs': [],
  '/api/stats': { sources: 1, tasks: 0, speed: 0 },
  '/api/system/ffmpeg': { available: true, path: 'tools/ffmpeg.exe' },
};

// 关键检查：boot 必须挂在 DOMContentLoaded 上（曾因写成 DOoContentLoaded 而整体不启动）
const bootHandlers = documentListeners.get('DOMContentLoaded') || [];
console.log();
// 注意：下面这个记录器在「完整桩」里会失效（window/document 互相引用），
// 记到 0 是常态，不代表 boot 没注册 —— 以上面那个独立探针的结果为准。
console.log('  [信息] 本桩记录到的 DOMContentLoaded 监听数:', bootHandlers.length,
            '(0 属正常；以「boot 注册在 DOMContentLoaded 上」那项为准)');
for (const type of documentListeners.keys()) {
  console.log('    document 事件:', type);
}

const requested = [];
ctx.fetch = (url) => {
  const key = String(url).split('?')[0];
  requested.push(key);
  const payload = Object.prototype.hasOwnProperty.call(API, key) ? API[key] : {};
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({ ok: true, data: payload }),
    text: () => Promise.resolve(JSON.stringify({ ok: true, data: payload })),
    headers: { get: () => 'application/json' },
  });
};

(async () => {
  let bootRes;
  try {
    await vm.runInContext('boot()', ctx);
    bootRes = 'boot() 执行完成（无抛错）';
  } catch (e) {
    bootRes = 'boot() 抛错: ' + e.message;
    const st = (e.stack || '').split('\n').slice(1, 3).join(' | ');
    bootRes += '\n      ' + st;
  }
  await new Promise((r) => setTimeout(r, 250));

  console.log('  启动流程:', bootRes);
  console.log('  boot 请求顺序:', requested.slice(0, 9).join(' > '));
  console.log('  启动后状态:', evalIn(`(function () {
    var form = document.getElementById('settings-form');
    return JSON.stringify({
      formHtmlLen: form ? (form.innerHTML || '').length : -1,
      bgDrop: !!document.getElementById('bg-drop'),
      factoryBtn: !!document.getElementById('btn-factory-reset'),
      settingsTabActive: (document.getElementById('view-settings') || {}).className || 'N/A',
    });
  })()`));
  console.log();
  console.log(errors.length ? '存在加载错误: ' + errors.join(', ') : '全部加载成功 ✓');
  // boot() 里有 setInterval（刷新统计/任务），必须显式退出，否则进程不结束
  // 真实触发 DOMContentLoaded，验证 boot 会被启动
  const handlers = documentListeners.get('DOMContentLoaded') || [];
  let fired = 0;
  for (const h of handlers) {
    try { h(); fired += 1; } catch (e) { console.log('    触发监听抛错: ' + e.message); }
  }
  console.log('  触发 DOMContentLoaded: 执行了', fired, '个监听');
  process.exit(errors.length ? 1 : 0);
})();
