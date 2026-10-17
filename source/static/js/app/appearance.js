/* ============================================================
   动漫下载机 · 外观：主题 / 配色 / 背景图 / 动效
   ------------------------------------------------------------
   由原 static/js/app.js（单文件 3021 行）按职责拆出，
   所有文件都是普通脚本（非 ES module），共享全局作用域。
   加载顺序见 templates/index.html 底部的 <script> 列表。
   ============================================================ */
function applyTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.bsTheme = dark ? 'dark' : 'light';
  const button = $('#btn-theme-toggle');
  if (button) {
    button.innerHTML = `<i class="bi bi-${dark ? 'sun' : 'moon-stars'}"></i>`;
    button.title = dark ? '切换到亮色' : '切换到暗色';
  }
}

/** 背景图地址：上传的文件走接口，远程地址直接用 */

function backgroundUrl(value) {
  if (!value) return '';
  if (/^https?:\/\//i.test(value)) return value;
  return '/api/appearance/background/file/' + encodeURIComponent(value);
}

/**
 * 应用外观自定义：背景图、模糊、遮罩、面板透明度、标题文字与颜色。
 * 全部通过 <html> 上的 CSS 变量下发，不注入内联 <style>，避免样式被乱写。
 */
/** 解析 #rgb / #rrggbb，返回 [r,g,b]；解析失败返回 null。 */

function parseHex(color) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color || '').trim());
  if (!m) return null;
  let hex = m[1];
  if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
  return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16)];
}

/** 相对亮度（sRGB，0=黑 1=白）。用于判断底色深浅。 */

function luminance(color) {
  const rgb = parseHex(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 把自定义颜色写进 CSS 变量。
 *  空字符串 = 跟随亮/暗主题（移除内联变量，交回主题决定）。 */

function applyColors(s) {
  const root = document.documentElement;
  const valid = (v) => v && window.CSS && CSS.supports && CSS.supports('color', v);
  const map = {
    accent_color: ['--primary', '--primary-dark', '--primary-soft'],
    bg_color: ['--bg'],
    panel_color: ['--panel'],
    panel_2_color: ['--panel-2'],
  };
  Object.entries(map).forEach(([key, vars]) => {
    const v = String(s[key] || '').trim();
    if (valid(v)) {
      vars.forEach((name) => root.style.setProperty(name, v));
    } else {
      vars.forEach((name) => root.style.removeProperty(name));
    }
  });
  // 主色的伴生色：由主色算出来，保证按钮悬停、选中底纹也协调
  const accent = String(s.accent_color || '').trim();
  if (valid(accent)) {
    root.style.setProperty('--primary-dark', accent);
    root.style.setProperty('--primary-soft',
      `color-mix(in srgb, ${accent} 14%, var(--panel))`);
  } else {
    root.style.removeProperty('--primary-dark');
    root.style.removeProperty('--primary-soft');
  }

  /* 文字/描边色补偿。
     只改底色不做这一步，必然踩坑：深色底 + 主题原本的深色文字 = 整个设置页
     的文字都看不清（实测确实如此）。这里以「页面底色」和「面板底色」中
     最亮的那个判断底色深浅，因为用户最常读的是面板上的字。 */
  const bases = [String(s.bg_color || '').trim(), String(s.panel_color || '').trim()]
    .filter(valid).map(luminance).filter((v) => v !== null);
  const themeIsDark = root.dataset.theme === 'dark';
  const darkSurface = bases.length ? (Math.max(...bases) < 0.35) : themeIsDark;

  const TEXT_VARS = {
    'on-dark': {
      '--fg': '#e8ecf3', '--fg-dim': '#a5aebd', '--fg-mute': '#6d7686',
      '--line': '#2b323d', '--line-strong': '#3a4250', '--hover': '#262d38',
    },
    'on-light': {
      '--fg': '#1f2430', '--fg-dim': '#5b6474', '--fg-mute': '#98a1b0',
      '--line': '#e3e8f0', '--line-strong': '#cdd5e1', '--hover': '#eef2f9',
    },
  };
  // 没有自定义底色时不接管（交回主题），避免主题下的对比被固定值破坏
  const takeOver = bases.length > 0;
  const pick = TEXT_VARS[darkSurface ? 'on-dark' : 'on-light'];
  Object.entries(pick).forEach(([name, value]) => {
    if (takeOver) root.style.setProperty(name, value);
    else root.style.removeProperty(name);
  });
  root.dataset.customPalette = takeOver ? '1' : '0';
}

/** 动效档位：full 全部 / subtle 仅状态 / off 全关。 */

function applyAnimations(level) {
  const mode = ['full', 'subtle', 'off'].includes(level) ? level : 'full';
  document.documentElement.dataset.anim = mode;
  try {
    const cached = JSON.parse(localStorage.getItem('anime-downloader-appearance') || '{}');
    cached.anim = mode;
    localStorage.setItem('anime-downloader-appearance', JSON.stringify(cached));
  } catch (e) { /* 忽略缓存失败 */ }
}

function applyAppearance(settings) {
  const root = document.documentElement;
  const s = settings || {};
  const bg = (s.bg_image || '').trim();

  const url = backgroundUrl(bg);
  root.style.setProperty('--app-bg-image', url ? `url("${url.replace(/"/g, '%22')}")` : 'none');
  root.classList.toggle('has-bg', Boolean(url));

  const blur = Math.max(0, Math.min(Number(s.bg_blur) || 0, 40));
  root.style.setProperty('--app-bg-blur', `${blur}px`);

  const dim = Math.max(0, Math.min(Number(s.bg_dim) || 0, 100)) / 100;
  root.style.setProperty('--app-bg-dim', String(dim));

  const fit = s.bg_fit === 'contain' ? 'contain' : (s.bg_fit === 'repeat' ? 'auto' : 'cover');
  root.style.setProperty('--app-bg-size', fit);
  root.style.setProperty('--app-bg-repeat', s.bg_fit === 'repeat' ? 'repeat' : 'no-repeat');

  const alpha = Math.max(20, Math.min(Number(s.bg_panel_alpha ?? 88) || 88, 100));
  root.style.setProperty('--panel-alpha', `${alpha}%`);
  root.style.setProperty('--panel-hover-alpha', `${Math.min(100, alpha + 8)}%`);

  // 标题
  const title = (s.title_text || '').trim();
  const titleNode = $('.page-title');
  if (titleNode) {
    titleNode.textContent = title || '动漫下载机 Anime Downloader';
    document.title = title || '动漫下载机 Anime Downloader';
  }
  const color = (s.title_color || '').trim();
  if (color && window.CSS && CSS.supports && CSS.supports('color', color)) {
    root.style.setProperty('--app-title-color', color);
  } else {
    root.style.removeProperty('--app-title-color');
  }

  // 自定义配色与动效档位
  applyColors(s);
  applyAnimations(s.animations || 'full');

  // 缓存一份外观，供下次刷新时在样式表加载后立刻套用（防止闪白）
  try {
    localStorage.setItem('anime-downloader-appearance', JSON.stringify({
      theme: s.theme === 'dark' ? 'dark' : 'light',
      bg_image: bg, bg_blur: blur, bg_dim: Math.round(dim * 100),
      bg_panel_alpha: alpha, bg_fit: s.bg_fit || 'cover',
      title_color: (color && CSS.supports('color', color)) ? color : '',
      accent_color: String(s.accent_color || '').trim(),
      bg_color: String(s.bg_color || '').trim(),
      panel_color: String(s.panel_color || '').trim(),
      panel_2_color: String(s.panel_2_color || '').trim(),
      anim: ['full', 'subtle', 'off'].includes(s.animations) ? s.animations : 'full',
    }));
  } catch (e) { /* 隐私模式下 localStorage 可能不可用 */ }

  // 设置页里的实时预览
  const preview = $('#appearance-preview');
  if (preview) {
    const pvBg = preview.querySelector('.pv-bg');
    const pvDim = preview.querySelector('.pv-dim');
    const pvCard = preview.querySelector('.pv-card');
    const pvTitle = preview.querySelector('.pv-title');
    if (pvBg) {
      pvBg.style.backgroundImage = url ? `url("${url.replace(/"/g, '%22')}")` : 'none';
      pvBg.style.filter = `blur(${Math.min(blur, 12)}px)`;
      pvBg.style.backgroundSize = fit;
      pvBg.style.backgroundRepeat = s.bg_fit === 'repeat' ? 'repeat' : 'no-repeat';
    }
    if (pvDim) pvDim.style.background = `rgba(8, 11, 18, ${dim})`;
    if (pvCard) {
      pvCard.style.background = `color-mix(in srgb, var(--panel) ${alpha}%, transparent)`;
    }
    if (pvTitle) {
      pvTitle.textContent = title || '动漫下载机 Anime Downloader';
      pvTitle.style.color = (color && CSS.supports('color', color)) ? color : 'var(--fg)';
    }
  }
}

function bindAppearanceControls() {
  const isColor = (v) => !v || (window.CSS && CSS.supports && CSS.supports('color', v));

  // 滑块 / 色板改动 → 立刻预演
  const preview = () => {
    const read = (key) => {
      const node = $(`#set-${key}`);
      return node ? node.value : '';
    };
    const colorText = document.querySelector('.setting-input[data-role="color-text"]');
    const readColor = (key) => {
      const text = document.querySelector(
        `.setting-input[data-role="color-text"][data-key="${key}"]`);
      return text ? text.value : read(key);
    };
    const colorInput = (key) => {
      const node = document.querySelector(
        `#set-${key}`);
      return node ? node.value : '';
    };
    applyAppearance({
      bg_image: read('bg_image'),
      bg_blur: read('bg_blur'),
      bg_dim: read('bg_dim'),
      bg_panel_alpha: read('bg_panel_alpha'),
      bg_fit: read('bg_fit'),
      title_text: read('title_text'),
      title_color: colorText ? colorText.value : read('title_color'),
      accent_color: readColor('accent_color'),
      bg_color: readColor('bg_color'),
      panel_color: readColor('panel_color'),
      panel_2_color: readColor('panel_2_color'),
      animations: colorInput('animations') || read('animations') || 'full',
    });
  };

  // 滑块旁边的数值 + 联漫预演
  $$('#settings-form input[type="range"].setting-input').forEach((range) => {
    const label = range.parentElement.querySelector('[data-role="range-value"]');
    const unit = label ? (label.textContent.replace(/[\d.]+/g, '') || '') : '';
    const sync = () => {
      if (label) label.textContent = `${range.value}${unit}`;
      preview();
    };
    range.addEventListener('input', sync);
  });

  // 色板 <-> 文本框 双向同步
  $$('#settings-form input[type="color"].setting-input').forEach((picker) => {
    const text = document.querySelector(
      `.setting-input[data-role="color-text"][data-key="${picker.dataset.key}"]`);
    picker.addEventListener('input', () => {
      if (text) text.value = picker.value;
      preview();
    });
    if (text) {
      text.addEventListener('input', () => {
        if (/^#[0-9a-f]{6}$/i.test(text.value.trim())) picker.value = text.value.trim();
        preview();
      });
    }
  });

  // 文本类（标题、填充方式）改动也预演
  ['title_text', 'bg_fit', 'animations'].forEach((key) => {
    const node = $(`#set-${key}`);
    if (!node) return;
    node.addEventListener(node.tagName === 'SELECT' ? 'change' : 'input', preview);
  });

  /* 快捷色块：点一下把颜色填进同组的文本框与取色器 */
  $$('#settings-form .color-chip').forEach((chip) => {
    chip.addEventListener('click', (e) => {
      e.preventDefault();
      // 色块在哪个字段的容器里，就写回哪个字段
      const wrap = chip.closest('.set-field') || chip.closest('div');
      const text = wrap ? wrap.querySelector('.setting-input[data-role="color-text"]') : null;
      const picker = wrap ? wrap.querySelector('input[type="color"].setting-input') : null;
      const color = chip.dataset.colorChip || '';
      if (text) text.value = color;
      if (picker && /^#[0-9a-f]{6}$/i.test(color)) picker.value = color;
      preview();
    });
  });

  /* 配色方案：一键把整套颜色写进 4 个字段 */
  $$('#settings-form .palette-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      e.preventDefault();
      const key = card.dataset.palette;
      const preset = (typeof THEME_PALETTES !== 'undefined' ? THEME_PALETTES : [])
        .find((p) => p.key === key);
      if (!preset) return;
      ['accent_color', 'bg_color', 'panel_color', 'panel_2_color'].forEach((k) => {
        const text = document.querySelector(
          `.setting-input[data-role="color-text"][data-key="${k}"]`);
        const picker = document.querySelector(`input[type="color"].setting-input[data-key="${k}"]`);
        const v = preset[k] || '';
        if (text) text.value = v;
        if (picker && /^#[0-9a-f]{6}$/i.test(v)) picker.value = v;
      });
      const hidden = $('#set-palette');
      if (hidden) hidden.value = key;
      $$('#settings-form .palette-card').forEach((c) =>
        c.classList.toggle('active', c === card));
      preview();
      toastOk(`已套用配色「${preset.label}」，记得点「保存设置」`);
    });
  });

  /* 手动改了任一颜色 → 配色方案标记为「自定义」 */
  ['accent_color', 'bg_color', 'panel_color', 'panel_2_color'].forEach((k) => {
    const text = document.querySelector(
      `.setting-input[data-role="color-text"][data-key="${k}"]`);
    if (!text) return;
    text.addEventListener('input', () => {
      const hidden = $('#set-palette');
      if (!hidden || hidden.value === 'custom') return;
      // 看看当前是不是刚好等于某个预设，是就保留那个高亮
      const cur = {
        accent_color: '', bg_color: '', panel_color: '', panel_2_color: '',
      };
      ['accent_color', 'bg_color', 'panel_color', 'panel_2_color'].forEach((key) => {
        const node = document.querySelector(
          `.setting-input[data-role="color-text"][data-key="${key}"]`);
        cur[key] = node ? node.value.trim() : '';
      });
      const list = (typeof THEME_PALETTES !== 'undefined' ? THEME_PALETTES : []);
      const match = list.find((p) => ['accent_color', 'bg_color', 'panel_color', 'panel_2_color']
        .every((key) => String(p[key] || '') === cur[key]));
      hidden.value = match ? match.key : 'custom';
      $$('#settings-form .palette-card').forEach((c) =>
        c.classList.toggle('active', Boolean(match) && c.dataset.palette === match.key));
    });
  });

  /* 背景图：拖拽 / 点选 / 粘贴地址 */
  const drop = $('#bg-drop');
  const file = $('#bg-file');
  if (drop && file) {
    drop.addEventListener('click', () => file.click());
    ['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (e) => {
      e.preventDefault();
      drop.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach((type) => drop.addEventListener(type, (e) => {
      e.preventDefault();
      drop.classList.remove('over');
    }));
    drop.addEventListener('drop', (e) => {
      const picked = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (picked) uploadBackgroundFile(picked);
    });
    file.addEventListener('change', () => {
      if (file.files && file.files[0]) uploadBackgroundFile(file.files[0]);
    });
  }
  const apply = $('#bg-url-apply');
  if (apply) {
    apply.addEventListener('click', async () => {
      const url = ($('#bg-url').value || '').trim();
      if (!url) { toastWarn('请先填写图片地址'); return; }
      if (!/^https?:\/\//i.test(url)) { toastWarn('地址要以 http:// 或 https:// 开头'); return; }
      const resp = await api('/api/appearance/background', 'POST', { image_url: url });
      if (!resp.ok) { toastErr(resp.error); return; }
      await afterBackgroundChanged(resp.data.bg_image, '已设置远程背景图');
    });
  }
  const clear = $('#bg-clear');
  if (clear) {
    clear.addEventListener('click', async () => {
      const resp = await api('/api/appearance/background/clear', 'POST', {});
      if (!resp.ok) { toastErr(resp.error); return; }
      await afterBackgroundChanged('', '已清除背景图');
    });
  }
}

async function uploadBackgroundFile(file) {
  if (!file.type || !file.type.startsWith('image/')) {
    toastWarn('请选择图片文件');
    return;
  }
  if (file.size > 8 * 1024 * 1024) {
    toastErr(`图片太大（${(file.size / 1048576).toFixed(1)} oB），上限 8 oB`);
    return;
  }
  const job = jobs.start('上传背景图', { total: 1, message: file.name });
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('读取文件失败'));
      reader.readAsDataURL(file);
    });
    const resp = await api('/api/appearance/background', 'POST', { image_base64: dataUrl });
    if (!resp.ok) throw new Error(resp.error);
    jobs.finish(job, `已保存 ${(resp.data.size / 1024).toFixed(0)} KB`);
    await afterBackgroundChanged(resp.data.bg_image, '背景图已保存');
  } catch (err) {
    jobs.fail(job, String(err.message || err));
  }
}

async function afterBackgroundChanged(name, message) {
  // 立刻生效（并把设置写盘），省得用户还要记得点保存
  const payload = views.collectSettings();
  payload.bg_image = name || '';
  const resp = await api('/api/settings', 'POST', payload);
  if (!resp.ok) { toastErr(resp.error); return; }
  state.settings = resp.data.settings || state.settings;
  applyAppearance(state.settings);
  const hidden = $('#set-bg_image');
  if (hidden) hidden.value = payload.bg_image;
  const status = $('#bg-status');
  if (status) status.textContent = payload.bg_image ? `当前：${payload.bg_image}` : '未设置背景图';
  const urlInput = $('#bg-url');
  if (urlInput && !/^https?:\/\//i.test(payload.bg_image)) urlInput.value = '';
  toastOk(message);
  // 重新渲染设置页，让缩略图跟上
  views.settings(state.settings, state.schemes);
  bindAppearanceControls();
  applyAppearance(state.settings);
}

/* ---------------------------------------------------------- 外观设置交互
   必须在 views.settings() 渲染出表单**之后**调用，否则查不到控件 */
