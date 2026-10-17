/**
 * 用 web-access 的 CDP Proxy 采样任务进度，判断是否还在"抽搐"。
 * 抽搐的表现：同一行的进度条宽度来回跳（回退），或行元素被反复重建。
 */
const { execFileSync } = require('child_process');

const TARGET = process.argv[2];
if (!TARGET) { console.error('用法: node _jitter_probe.cjs <targetId>'); process.exit(1); }

function evalJs(expression) {
  const out = execFileSync('curl.exe', [
    '-s', '--max-time', '25', '-X', 'POST',
    `http://localhost:3456/eval?target=${TARGET}`,
    '-d', expression,
  ], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  const parsed = JSON.parse(out);
  return parsed.value;
}

const SNAPSHOT = `(() => {
  const rows = Array.from(document.querySelectorAll('#task-list .task-row'));
  const running = rows.filter((e) => e.classList.contains('is-running'));
  return JSON.stringify({
    total: rows.length,
    running: running.length,
    visible: rows.filter((e) => getComputedStyle(e).opacity !== '0').length,
    // 用 DOM 节点身份判断有没有被重建：打标记后下一轮还能读到就说明没重建
    items: running.slice(0, 3).map((el) => {
      const fill = el.querySelector('.bar > i');
      const pct = el.querySelector('.status-line span:last-child');
      if (!el.dataset.probeId) el.dataset.probeId = 'p' + Math.random().toString(36).slice(2, 8);
      return { id: el.dataset.id, probe: el.dataset.probeId,
               w: fill ? fill.style.width : '?', pct: pct ? pct.textContent : '?' };
    }),
  });
})()`;

(async () => {
  const samples = [];
  for (let i = 0; i < 8; i += 1) {
    samples.push(JSON.parse(evalJs(SNAPSHOT)));
    await new Promise((r) => setTimeout(r, 2000));
  }

  console.log('=== 采样（每 2 秒，共 8 次）===');
  samples.forEach((s, i) => {
    console.log('  #%d 行=%d 可见=%d 进行中=%d | %s', i + 1, s.total, s.visible, s.running,
      s.items.map((x) => `${x.id.slice(0, 6)}=${x.w}`).join(' '));
  });

  // 1) 进度是否单调（不回跳）
  let back = 0;
  for (let c = 0; c < 3; c += 1) {
    for (let i = 1; i < samples.length; i += 1) {
      const a = samples[i - 1].items[c];
      const b = samples[i].items[c];
      if (!a || !b || a.id !== b.id) continue;
      const av = parseFloat(String(a.w).replace('%', '')) || 0;
      const bv = parseFloat(String(b.w).replace('%', '')) || 0;
      if (bv < av - 0.01) back += 1;
    }
  }
  console.log();
  console.log('  进度回跳次数（0 = 不抽搐）:', back);

  // 2) 行元素是否被重建（probe id 变化 = DOM 被重建）
  let rebuilt = 0;
  for (let c = 0; c < 3; c += 1) {
    const ids = new Set();
    for (const s of samples) {
      const it = s.items[c];
      if (it) ids.add(it.probe);
    }
    if (ids.size > 1) rebuilt += 1;
  }
  console.log('  行被重建的次数（0 = 原地更新生效）:', rebuilt);

  // 3) 可见性
  const minVisible = Math.min(...samples.map((s) => s.visible));
  console.log('  最少可见行数:', minVisible, '(应等于总行数', samples[0].total + ')');
  console.log();
  console.log(back === 0 && rebuilt === 0 && minVisible === samples[0].total
    ? '结论：进度平滑推进、行未被重建、内容全程可见 ✓'
    : '结论：仍有问题，需要继续排查');
})();
