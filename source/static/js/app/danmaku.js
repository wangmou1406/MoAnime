/* ============================================================
   弹幕下载

   目前只有 B 站能自动抓：番剧名 → 搜季 → 每集 cid → 弹幕接口。
   抓下来存在视频旁边，文件名跟视频相同（第01集.mp4 → 第01集.xml），
   播放器打开视频会自动找同名文件。

   进度：整集批量是逐集调接口的，每集结束就把后台作业面板往前推一格，
   日志面板里也能看到「弹幕 番名 第N集：xxx 条」。

   格式在「设置 → 下载后处理 → 弹幕保存格式」里选：XML / ASS / 两种都存。
   ============================================================ */

async function downloadDanmakuFor(button, animeName) {
  if (!animeName) return;
  const original = button ? button.innerHTML : '';
  const setBusy = (text) => {
    if (!button) return;
    button.disabled = true;
    button.innerHTML = `<span class="spinner-border spinner-border-sm"></span> ${text}`;
  };
  const restore = () => {
    if (!button) return;
    button.disabled = false;
    button.innerHTML = original;
  };

  let job = null;
  try {
    setBusy('匹配中…');
    job = jobs.start(`弹幕 · ${animeName}`, { total: 1, message: '正在匹配 B 站季…' });

    const preview = await api('/api/danmaku/preview', 'POST', { anime_name: animeName });
    if (!preview.ok) {
      jobs.fail(job, preview.error || '匹配失败');
      toastErr(preview.error);
      return;
    }
    const info = preview.data || {};
    const plan = info.plan || [];
    if (!plan.length) {
      jobs.fail(job, `《${info.season_title || ''}》没有能对上的集数`);
      toastWarn(`B 站《${info.season_title || ''}》没有能对上的集数（本地 ${info.local_count} 集）`);
      return;
    }
    // 作业启动时还不知道有多少集，预览拿到 plan 后才定下分母
    job.total = plan.length;
    jobs.set(job, 0, `${plan.length} 集 · 季 ${info.season_id}`);

    let doneCount = 0;
    let totalDanmaku = 0;
    let emptyCount = 0;
    const failures = [];

    for (let i = 0; i < plan.length; i += 1) {
      const ep = plan[i];
      const label = `第${ep.number}集`;
      jobs.set(job, i, `${label}（${i + 1}/${plan.length}）`);
      setBusy(`${i + 1}/${plan.length}`);

      // 单集失败不能中断整批：网络抖一下、某集没弹幕都很正常，
      // 记下来继续下一集（之前这里没有 try，一次异常整批就停了）
      try {
        const resp = await api('/api/danmaku/episode', 'POST', {
          cid: ep.cid || null,
          path: ep.path,
          anime_name: animeName,
          number: ep.number,
          season_id: info.season_id,
          source: 'auto',       // 后端：配了外部弹幕服务就先用它，拿不到再退回 B 站
        });
        if (!resp.ok) {
          failures.push(`${label}：${resp.error}`);
          continue;
        }
        const got = (resp.data || {}).count || 0;
        if (!got) { emptyCount += 1; continue; }
        doneCount += 1;
        totalDanmaku += got;
        const fromService = ((resp.data || {}).source || '') === 'service';
        jobs.set(job, i + 1, `${label}：${got.toLocaleString()} 条${fromService ? '（外服）' : ''}`);
      } catch (err) {
        failures.push(`${label}：${(err && err.message) || err}`);
      }
    }

    const summary = `${doneCount} 集 / ${totalDanmaku.toLocaleString()} 条`
      + (emptyCount ? `（${emptyCount} 集无弹幕）` : '')
      + (failures.length ? `（失败 ${failures.length} 集）` : '');
    if (!doneCount) {
      jobs.fail(job, failures[0] || '没有抓到弹幕');
      toastErr(failures[0] || '没有抓到弹幕');
      return;
    }
    if (failures.length) {
      jobs.finish(job, summary);
      toastWarn(`弹幕完成：${summary}`);
      console.warn('[AnimeDownloader] 弹幕失败明细', failures);
    } else {
      jobs.finish(job, summary);
      toastOk(`弹幕完成：${summary}`);
    }
    await loadLibrary();
  } catch (err) {
    if (job) jobs.fail(job, String(err && err.message ? err.message : err));
    toastErr(`弹幕下载失败：${err}`);
  } finally {
    restore();
  }
}
