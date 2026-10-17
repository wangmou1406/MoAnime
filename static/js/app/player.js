/* ============================================================
   动漫下载机 · 播放器：在线 / 本地预览、hls.js 接入
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
const playerState = { hls: null, url: '', events: [], index: -1 };

/**
 * 播放诊断（在浏览器 Console 里用）：
 *   playerDiag()  —— 打印当前状态与 hls.js 事件轨迹
 *   playerRetry() —— 重新加载一次
 */

function playerDiag() {
  const video = $('#preview-video');
  const info = {
    解析到的地址: playerState.url || '(还没有)',
    hls实例: playerState.hls ? '已创建' : '未创建',
    清晰度档位: playerState.hls && playerState.hls.levels
      ? playerState.hls.levels.length : 0,
    当前档位: playerState.hls ? playerState.hls.currentLevel : null,
    弹窗显示中: (() => {
      const m = document.getElementById('player-modal');
      return m ? (m.classList.contains('show') ? '是' : '否') : 'N/A';
    })(),
    video_readyState: video ? video.readyState : 'N/A',
    video_networkState: video ? video.networkState : 'N/A',
    video_currentTime: video ? Number(video.currentTime.toFixed(2)) : 'N/A',
    video_duration: video && isFinite(video.duration)
      ? Number(video.duration.toFixed(1)) : String(video && video.duration),
    video_error: video && video.error ? `${video.error.code}: ${video.error.message}` : '无',
    最新画面尺寸: video ? `${video.videoWidth}x${video.videoHeight}` : 'N/A',
    hls事件轨迹: playerState.events.slice(-14),
  };
  console.table ? console.table(info) : console.log(info);
  return info;
}

window.playerDiag = playerDiag;

window.playerState = playerState;

function playerLog(name, extra) {
  playerState.events.push(extra ? `${name} ${extra}` : name);
  if (playerState.events.length > 60) playerState.events.shift();
}

function playerMask(show, text) {
  const mask = $('#player-mask');
  if (!mask) return;
  mask.style.display = show ? '' : 'none';
  if (text !== undefined) {
    $('#player-mask-text').textContent = text;
    playerLog('遮罩:' + text);
  }
}

async function openPlayer(index) {
  const ep = state.episodes[index];
  const anime = state.currentAnime;
  if (!ep || !anime) return;
  playerState.url = '';
  playerState.events = [];
  playerState.index = index;
  playerLog('openPlayer', `第${index + 1}集`);
  $('#player-title').textContent = `${anime.name} · ${ep.name}`;
  $('#player-source').textContent = anime.source || '';
  $('#player-info').innerHTML = '';
  playerMask(true, '正在解析播放地址…');
  getModal('player-modal').show();

  const video = $('#preview-video');
  destroyPlayerForNewVideo();

  const resp = await api('/api/video/resolve', 'POST', {
    source_id: anime.source_id, episode_url: ep.url, anime_url: anime.url,
  });
  if (!resp.ok) {
    playerLog('解析失败', resp.error);
    playerMask(true, `解析失败：${resp.error}`);
    return;
  }
  const url = resp.data.url;
  playerState.url = url;
  playerLog('解析成功', url.slice(0, 50));
  $('#player-info').innerHTML =
    `<i class="bi bi-link-45deg"></i>
     <span class="mono" style="word-break:break-all">${esc(url.slice(0, 140))}</span>`;

  // 走本机代理：视频站的分片不允许跨域，浏览器直连会被 CORS 拦
  const proxied = `/api/video/stream?url=${encodeURIComponent(url)}`;
  attachPlayer(video, proxied, url);
}

function attachPlayer(video, proxied, originalUrl) {
  const isHls = /\.m3u8(\?|$)/i.test(originalUrl);

  if (isHls && typeof Hls !== 'undefined' && Hls.isSupported()) {
    playerMask(true, '正在缓冲…');
    const hls = new Hls({ lowLatencyMode: false, maxBufferLength: 30, enableWorker: true });
    playerState.hls = hls;
    hls.loadSource(proxied);
    playerLog('loadSource', proxied.slice(0, 60));
    hls.attachMedia(video);
    playerLog('attachMedia');
    hls.on(Hls.Events.MANIFEST_PARSED, (_e, data) => {
      playerLog('MANIFEST_PARSED', `${(data.levels || []).length} 档`);
      playerMask(false);
      const levels = (data.levels || []).length;
      if (levels) {
        $('#player-info').insertAdjacentHTML('beforeend',
          `<span class="badge badge-dim">${levels} 档清晰度</span>`);
      }
      video.play().catch((err) => {
        playerLog('play() 被拒绝', err && err.message);
        playerMask(true, `浏览器拒绝了自动播放（${err && err.name}）。请点一下画面开始播放。`);
      });
    });
    hls.on(Hls.Events.LEVEL_LOADED, () => playerLog('LEVEL_LOADED'));
    hls.on(Hls.Events.FRAG_LOADED, () => playerLog('FRAG_LOADED'));
    hls.on(Hls.Events.ERROR, (_e, data) => {
      playerLog('ERROR', `${data.type}/${data.details}${data.fatal ? ' (致命)' : ''}`);
      if (!data.fatal) return;
      const detail = data.details || data.type;
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        playerMask(true, `网络错误（${detail}），正在重试…`);
        try { hls.startLoad(); return; } catch (e) { /* 落到下面 */ }
      }
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        playerMask(true, '解码错误，正在恢复…');
        try { hls.recoverMediaError(); return; } catch (e) { /* 落到下面 */ }
      }
      playerMask(true, `播放失败：${detail}。可点右上角「下载」后用本地播放器看。`);
      destroyPlayer();
    });
    return;
  }

  // Safari 等原生支持 HLS，或 mp4 直链
  playerLog('回退到原生播放', isHls ? 'native HLS' : '直接地址');
  video.src = proxied;
  playerMask(true, '正在缓冲…');
  video.onloadeddata = () => {
    playerLog('onloadeddata');
    playerMask(false);
    video.play().catch(() => {});
  };
  video.onerror = () => {
    playerLog('video.onerror');
    playerMask(true, '这个源无法直接播放（可能限制来源或需要额外请求头）。'
      + '可点右上角「下载」后用本地播放器看。');
  };
}

function destroyPlayer() {
  if (playerState.hls) {
    try { playerState.hls.destroy(); } catch (e) { /* 忽略 */ }
    playerState.hls = null;
  }
  const video = $('#preview-video');
  if (video) {
    try { video.pause(); } catch (e) { /* 忽略 */ }
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* 忽略 */ }
  }
  // 弹窗隐藏时 Bootstrap 会给它加 aria-hidden；如果焦点还留在弹窗内的按钮上，
  // 浏览器就会报「Blocked aria-hidden on an element because its descendant retained focus」。
  // 所以在隐藏前先把焦点移走。
  try {
    const modal = document.getElementById('player-modal');
    const active = document.activeElement;
    if (modal && active && modal.contains(active) && typeof active.blur === 'function') {
      active.blur();
    }
  } catch (e) { /* 忽略 */ }
}

/** 换一集重播时用：释放上一个实例，但不要动弹窗焦点 */

function destroyPlayerForNewVideo() {
  if (playerState.hls) {
    try { playerState.hls.destroy(); } catch (e) { /* 忽略 */ }
    playerState.hls = null;
  }
  const video = $('#preview-video');
  if (video) {
    try { video.pause(); } catch (e) { /* 忽略 */ }
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* 忽略 */ }
  }
}

function closePlayer() {
  destroyPlayer();
  playerMask(false);
}

/* ============================================================
   广告体检：对比各源同一集的时长（流式，右下角显示逐源进度）
   ============================================================ */
/* ============================================================
   广告体检（合并版）：时长通道 + 文字通道，一次跑完自动排序
   ------------------------------------------------------------
   两条通道互补：
     时长：对比各源同一集的 m3u8 总时长 → 治「插播广告片 / 内容残缺」
     文字：只下开头 20 秒抽帧 OCR     → 治「叠字幕广告」（不改时长）
   跑完按结果给问题源打标记并自动沉底。
   ============================================================ */

function openLocalPlayer(path, name) {
  $('#local-player-title').textContent = name || '预览';
  const video = $('#player-video');
  video.src = `/api/library/stream?path=${encodeURIComponent(path)}`;
  showModal('modal-player');
  video.play().catch(() => {});
  $('#modal-player').addEventListener('hidden.bs.modal', () => {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }, { once: true });
}
