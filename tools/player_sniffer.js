#!/usr/bin/env node
/**
 * 播放器嗅探器（常驻进程）
 * ------------------------------------------------------------
 * 用无头浏览器打开播放页，监听网络请求，截获真正的视频地址。
 * 不需要复刻任何解密算法 —— 站点自己的 JS 在浏览器里跑，
 * 我们只看它最终请求了哪个视频地址。
 *
 * 为什么常驻：启动浏览器要 2~3 秒，逐集解析会累积很慢。
 * 这里保持一个浏览器实例，通过 stdin 收任务、stdout 回结果（NDJSON）。
 *
 * 协议（stdin 每行一个 JSON）：
 *   {"id":1,"url":"https://...","wait":12000}
 * 输出（stdout 每行一个 JSON）：
 *   {"id":1,"ok":true,"video":"https://...","kind":"m3u8",
 *    "referer":"...","cookies":"e=...; p=...","ua":"...","candidates":[...]}
 *   {"id":1,"ok":false,"error":"..."}
 *   {"type":"ready"}  启动完成
 *
 * 用法: node player_sniffer.js [--browser <chromium可执行文件>]
 */
'use strict';
const path = require('path');
const fs = require('fs');

// 必须在 require playwright 之前改 TEMP：Node 的 os.tmpdir() 会被 Playwright
// 用来 mkdtemp 放浏览器 profile，而某些环境（沙箱/受限账户）不允许写系统 TEMP，
// 会报 EPERM mkdtemp。提前指到项目内的目录即可。
const TMP_ROOT = process.env.AD_SNIFFER_TMP
  || path.join(__dirname, '..', 'data', '.sniffer-tmp');
try { fs.mkdirSync(TMP_ROOT, { recursive: true }); } catch (e) { /* 已存在 */ }
process.env.TEMP = TMP_ROOT;
process.env.TMP = TMP_ROOT;
process.env.TMPDIR = TMP_ROOT;

const readline = require('readline');

const ARGS = process.argv.slice(2);
let BROWSER = ARGS[ARGS.indexOf('--browser') + 1] || '';
if (!BROWSER) {
  const candidates = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
  ];
  BROWSER = candidates.find((p) => { try { return fs.existsSync(p); } catch (e) { return false; } }) || '';
}

// playwright-core 位置：优先环境变量（由 Python 传），其次项目内 data/_pwt
function loadPlaywright() {
  const tries = [
    process.env.AD_PLAYWRIGHT_DIR,
    path.join(__dirname, '..', 'data', '_pwt', 'node_modules', 'playwright-core'),
    path.join(__dirname, '..', 'node_modules', 'playwright-core'),
    'playwright-core',
  ].filter(Boolean);
  for (const t of tries) {
    try { return require(t); } catch (e) { /* 继续试 */ }
  }
  throw new Error('找不到 playwright-core，请先安装：npm i playwright-core');
}

const VIDEO_RX = /\.(m3u8|mp4|flv|mkv|m4s|mpd|ts)(\?|$|\/)/i;
const M3U8_RX = /\.m3u8(\?|$|\/)/i;
const AD_RX = /doubleclick|googlesyndication|google-analytics|adsystem|adservice|cnzz|51\.la|umeng|gtag|wdbed\.vip\/image/i;

function pickBest(list) {
  const usable = list.filter((v) => !AD_RX.test(v.url));
  const m3u8 = usable.filter((v) => M3U8_RX.test(v.url));
  if (m3u8.length) return m3u8[m3u8.length - 1];
  const mp4 = usable.filter((v) => /\.mp4/i.test(v.url));
  if (mp4.length) return mp4[mp4.length - 1];
  return usable.length ? usable[usable.length - 1] : null;
}

async function main() {
  const { chromium } = loadPlaywright();
  if (!BROWSER) {
    console.log(JSON.stringify({ type: 'error', error: '找不到浏览器内核（Edge/Chrome），可用 --browser 指定' }));
    process.exit(2);
  }
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: BROWSER,
      headless: true,
      args: ['--no-first-run', '--disable-gpu', '--mute-audio',
             '--autoplay-policy=no-user-gesture-required', '--no-sandbox',
             '--disable-dev-shm-usage'],
    });
  } catch (e) {
    console.log(JSON.stringify({ type: 'error', error: '启动浏览器失败: ' + e.message }));
    process.exit(2);
  }
  console.log(JSON.stringify({ type: 'ready', browser: BROWSER, tmp: TMP_ROOT }));

  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      + '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 800 },
  });

  const rl = readline.createInterface({ input: process.stdin });
  const queue = [];
  let busy = false;

  rl.on('line', (line) => {
    line = line.trim();
    if (!line) return;
    let task;
    try { task = JSON.parse(line); } catch (e) {
      console.log(JSON.stringify({ ok: false, error: '任务不是合法 JSON' }));
      return;
    }
    queue.push(task);
    pump();
  });

  async function pump() {
    if (busy) return;
    const task = queue.shift();
    if (!task) return;
    busy = true;
    try {
      const out = await sniff(ctx, task);
      console.log(JSON.stringify(out));
    } catch (e) {
      console.log(JSON.stringify({ id: task.id, ok: false, error: String(e.message || e).slice(0, 200) }));
    } finally {
      busy = false;
      pump();
    }
  }

  rl.on('close', async () => {
    try { await browser.close(); } catch (e) { /* ignore */ }
    process.exit(0);
  });
}

/** 多轮尝试点击播放入口（主页面 + 各 iframe 内），尽快触发视频请求。 */
async function pokePlayButtons(page, rounds) {
  const SELS = ['video', '.vjs-big-play-button', '.dplayer-play-icon',
                '[class*="big-play"]', '[class*="play-btn"]', '[class*="play-icon"]',
                '.play', '.btn-play', '[class*="play"]', 'button'];
  for (let r = 0; r < rounds; r += 1) {
    const sel = SELS[r % SELS.length];
    await page.evaluate((s) => {
      const els = [...document.querySelectorAll(s)];
      for (const el of els) {
        try { el.click(); return; } catch (e) { /* ignore */ }
      }
    }, sel).catch(() => {});
    // iframe 内部也点一遍（播放器常在 iframe 里）；跨域会抛，忽略
    for (const f of page.frames()) {
      if (f === page.mainFrame()) continue;
      try {
        await f.evaluate((s) => {
          const els = [...document.querySelectorAll(s)];
          for (const el of els) { try { el.click(); return; } catch (e) { /* ignore */ } }
          const v = document.querySelector('video');
          if (v && v.play) { v.play().catch(() => {}); }
        }, sel);
      } catch (e) { /* 跨域 iframe 忽略 */ }
    }
    await page.waitForTimeout(900);
  }
}

async function sniff(ctx, task) {
  const wait = Math.max(3000, Math.min(Number(task.wait) || 12000, 60000));
  const found = [];
  const t0 = Date.now();
  const page = await ctx.newPage();
  const debug = !!task.debug;
  const onReq = (req) => {
    const u = req.url();
    if (VIDEO_RX.test(u) && !AD_RX.test(u)) {
      found.push({ url: u, kind: M3U8_RX.test(u) ? 'm3u8'
        : (/\.mp4/i.test(u) ? 'mp4' : (/\.mpd/i.test(u) ? 'mpd' : 'other')) });
    }
  };
  page.on('request', onReq);
  // 记录带视频 Content-Type 的响应（有些站的地址没有 .m3u8 后缀）
  const typedMedia = [];
  const onResp = (resp) => {
    try {
      const ct = (resp.headers()['content-type'] || '').toLowerCase();
      if (/mpegurl|video\/|octet-stream/.test(ct)) {
        const u = resp.url();
        if (!AD_RX.test(u)) typedMedia.push({ url: u, kind: /mpegurl/.test(ct) ? 'm3u8' : 'media' });
      }
    } catch (e) { /* ignore */ }
  };
  page.on('response', onResp);

  const out = { id: task.id, ok: false };
  try {
    await page.goto(task.url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(2500);

    // 轮询等待：一旦拿到就提前结束，不用干等满
    const deadline = Date.now() + wait;
    let round = 0;
    while (Date.now() < deadline && !found.length) {
      if (round < 6) await pokePlayButtons(page, 1);
      round += 1;
      await page.waitForTimeout(700);
    }
    if (found.length) await page.waitForTimeout(1200);   // 收尾，避免漏掉紧随的请求

    let best = pickBest(found);
    // 回退 1：带视频 Content-Type 的响应（地址可能没有 .m3u8 后缀）
    if (!best && typedMedia.length) {
      found.push(...typedMedia);
      best = pickBest(found);
    }
    // 回退 2：有些站把 m3u8 喂给 MSE（video.src 是 blob:），网络层看不到独立请求，
    // 但它已经作为资源加载过 —— 从 performance 资源条目里捞。
    if (!best) {
      const seen = new Set(found.map((f) => f.url));
      const frames = page.frames();
      if (debug) out._frames = frames.length;
      for (const f of frames) {
        try {
          const entries = await f.evaluate(() => {
            try {
              return performance.getEntriesByType('resource').map((r) => r.name);
            } catch (e) { return []; }
          });
          if (debug) {
            out._perf = (out._perf || 0) + (entries ? entries.length : 0);
          }
          for (const u of (entries || [])) {
            if (seen.has(u) || AD_RX.test(u)) continue;
            if (!/\.(m3u8|mp4|flv|m4s|mpd|ts)(\?|$)/i.test(u)) continue;
            seen.add(u);
            found.push({ url: u, kind: M3U8_RX.test(u) ? 'm3u8' : 'other' });
          }
        } catch (e) {
          if (debug) out._frameErr = String(e.message).slice(0, 80);
        }
      }
    }
    // 回退 3：HTML 里明文写的地址（有些站直接把 m3u8 放在 player_aaaa 里）
    if (!best) {
      const seen = new Set(found.map((f) => f.url));
      for (const f of page.frames()) {
        try {
          const urls = await f.evaluate(() => {
            const h = document.documentElement.outerHTML;
            const re = /https?:\/\/[^\s"'<>\\]+\.(?:m3u8|mp4)[^\s"'<>\\]*/gi;
            return (h.match(re) || []);
          });
          for (const u of (urls || [])) {
            if (seen.has(u) || AD_RX.test(u)) continue;
            seen.add(u);
            found.push({ url: u, kind: M3U8_RX.test(u) ? 'm3u8' : 'other' });
          }
        } catch (e) { /* ignore */ }
      }
    }
    if (best) best = pickBest(found);
    if (debug) out._debug = { found: found.length, typed: typedMedia.length,
                              pollRounds: round, waited: Date.now() - t0 };

    out.candidates = found.slice(-6).map((f) => ({ kind: f.kind, url: f.url.slice(0, 200) }));
    if (best) {
      out.ok = true;
      out.video = best.url;
      out.kind = best.kind;
      out.referer = task.url;              // 多数站点校验 Referer
      // 关键：视频 CDN 往往校验会话 cookie（例如 anime1 的 v.anime1.me/api
      // 在返回地址的同时下发 cookie，其中 p 是路径绑定的 JWT，缺了它直连必然 403）
      try {
        const cookies = await page.context().cookies();
        out.cookies = cookies.map((c) => c.name + '=' + c.value).join('; ');
      } catch (e) { out.cookies = ''; }
      out.ua = await page.evaluate(() => navigator.userAgent).catch(() => '');
    } else {
      // 区分「页面根本没有播放器」与「播放器用了 MSE/blob（无可截获请求）」
      let blobOnly = false;
      for (const f of page.frames()) {
        try {
          const info = await f.evaluate(() => {
            const v = document.querySelector('video');
            return v ? { src: v.currentSrc || v.src || '' } : null;
          });
          if (info && /^blob:/.test(info.src)) { blobOnly = true; break; }
        } catch (e) { /* 跨域忽略 */ }
      }
      out.error = blobOnly
        ? '该页播放器用 MSE(blob:) 自行组建数据流，网络层没有可截获的视频地址'
        : '页面没有发出视频请求';
    }
  } catch (e) {
    out.error = String(e.message || e).slice(0, 200);
  } finally {
    page.off('request', onReq);
    await page.close().catch(() => {});
  }
  return out;
}

main().catch((e) => {
  console.log(JSON.stringify({ type: 'error', error: String(e.message || e) }));
  process.exit(1);
});
