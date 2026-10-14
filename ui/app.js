/* ============================================================
   音乐下载器 —— 前端逻辑
   两种运行环境共用：
     · pywebview 窗口（window.pywebview.api）
     · 浏览器 + 本地服务（POST /api/<method> + SSE /api/events）
   ============================================================ */

/* 运行环境判定。
   注意：pywebview 也是通过内部 HTTP 服务加载本地页面的，所以不能靠
   location.protocol 判断。改为由我们自己的网页服务在 </head> 前注入标记；
   原生窗口里没有这个标记。 */
const IS_SERVE = !!window.__MODE_SERVE__;

/* ---------------- 图标 ---------------- */
const I = {
  music: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`,
  down: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 20h16"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>`,
  warn: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  tag: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`,
  play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9z"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>`,
};

/* ---------------- 桥接 ---------------- */
window.__errs = [];
window.addEventListener('error', e => window.__errs.push(String(e.message || e)));
window.addEventListener('unhandledrejection',
  e => window.__errs.push('promise: ' + String(e.reason)));

async function call(method, ...args) {
  if (IS_SERVE) {
    const r = await fetch('/api/' + method, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    });
    return await r.json();
  }
  return await window.pywebview.api[method](...args);
}

/** pywebview 的 pywebviewready 时机不总是可靠，轮询等桥接就绪 */
async function waitForBridge(timeout = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (window.pywebview && window.pywebview.api) return true;
    await new Promise(r => setTimeout(r, 80));
  }
  return false;
}

let pushHandler = () => {};
if (IS_SERVE) {
  const es = new EventSource('/api/events');
  es.onmessage = e => { try { pushHandler(JSON.parse(e.data)); } catch (_) {} };
}
window.__push = evt => pushHandler(evt);

/* ---------------- 工具 ---------------- */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
}
function fmtSpeed(bps) {
  if (!bps) return '—';
  return fmtBytes(bps) + '/s';
}
function fmtEta(sec) {
  if (!sec || sec < 1) return '';
  if (sec < 60) return Math.round(sec) + ' 秒';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m + ' 分 ' + s + ' 秒';
}
function fmtDur(ms) {
  if (!ms) return '';
  const t = Math.round(ms / 1000);
  return String(Math.floor(t / 60)).padStart(2, '0') + ':' +
         String(t % 60).padStart(2, '0');
}
function basename(p) { return String(p || '').split(/[\\/]/).pop(); }

function toast(msg, kind = '', ms = 3200) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = (kind === 'ok' ? I.check : kind === 'err' ? I.alert : I.info) +
    '<span>' + esc(msg) + '</span>';
  $('#toasts').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s, transform .25s';
    el.style.opacity = '0'; el.style.transform = 'translateY(8px)';
    setTimeout(() => el.remove(), 260);
  }, ms);
}

/* ---------------- 状态 ---------------- */
const S = {
  settings: null,
  results: [],
  jobs: new Map(),   // jobId -> {data, refs}
  busy: false,
};

/* ============================================================
   初始化
   ============================================================ */
async function init() {
  bindEvents();
  const h = await call('hello');
  if (h && h.ok) {
    S.settings = await call('get_settings');
    syncUIFromSettings();
    updateSourceChip();
  }
  $('#cfgPath').textContent = '配置保存在程序目录的 config.json';
}

function syncUIFromSettings() {
  const d = S.settings || {};
  $('#outDirText').textContent = d.outputDir || 'D:\\音乐下载';
  setSeg('#segFormat', d.format || 'both');
  setSeg('#setFormat', d.format || 'both');
  $('#setOutDir').value = d.outputDir || '';
  $('#setSource').value = d.sourceOrder || 'official_first';
  $('#setCoverMax').value = d.coverMax ?? 1200;
  $('#setCover').checked = d.embedCover !== false;
  $('#setLyrics').checked = d.embedLyrics !== false;
  $('#setLrc').checked = d.writeLrc !== false;
  renderCookieList();
}

function setSeg(sel, val) {
  $$(sel + ' button').forEach(b =>
    b.classList.toggle('active', b.dataset.v === val));
}
function getSeg(sel) {
  const b = $(sel + ' button.active');
  return b ? b.dataset.v : '';
}

function updateSourceChip() {
  const d = S.settings || {};
  const n = (d.cookies || []).filter(c => c.enabled && c.hasValue).length;
  const chip = $('#sourceChip');
  chip.classList.remove('is-ok', 'is-warn', 'is-err');
  if (d.sourceOrder === 'aggregator_only') {
    chip.classList.add('is-warn');
    $('#sourceText').textContent = '仅用聚合接口';
  } else if (n > 0) {
    chip.classList.add('is-ok');
    $('#sourceText').textContent = `官方 VIP 通道已就绪（${n} 个账号）`;
  } else {
    chip.classList.add('is-warn');
    $('#sourceText').textContent = '未配置 VIP 账号 · 自动使用聚合接口';
  }
}

/* ============================================================
   事件绑定
   ============================================================ */
function bindEvents() {
  $('#btnSearch').onclick = doSearch;
  $('#q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  $('#q').addEventListener('input', () => {
    $('#btnClear').hidden = !$('#q').value;
  });
  $('#btnClear').onclick = () => {
    $('#q').value = ''; $('#btnClear').hidden = true; $('#q').focus();
  };
  $('#segFormat').addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    setSeg('#segFormat', b.dataset.v);
    S.settings.format = b.dataset.v;
    setSeg('#setFormat', b.dataset.v);
    await call('save_settings', { format: b.dataset.v });
  });
  $('#sourceChip').onclick = openSettings;
  $('#btnSettings').onclick = openSettings;
  $('#btnOutDir').onclick = openSettings;

  $('#btnClearResults').onclick = () => {
    S.results = []; $('#resultsSection').hidden = true; renderResults();
  };
  $('#btnClearJobs').onclick = () => {
    for (const [id, j] of S.jobs) {
      if (j.data.status === 'done' || j.data.status === 'error' ||
          j.data.status === 'cancelled') {
        j.el.remove(); S.jobs.delete(id);
      }
    }
    updateJobCount();
  };

  $$('[data-close]').forEach(b => b.onclick = () => closeModal(b.dataset.close));
  $$('.modal-backdrop').forEach(m => m.addEventListener('mousedown', e => {
    if (e.target === m) closeModal(m.id);
  }));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $$('.modal-backdrop').forEach(m => closeModal(m.id));
  });

  // 设置弹窗
  $('#btnSaveSettings').onclick = saveSettings;
  $('#btnPickDir').onclick = async () => {
    const r = await call('pick_folder', $('#setOutDir').value);
    if (r.ok && r.path) $('#setOutDir').value = r.path;
    else if (r.error) toast(r.error.message, 'err');
  };
  $('#setFormat').addEventListener('click', e => {
    const b = e.target.closest('button'); if (b) setSeg('#setFormat', b.dataset.v);
  });
  $('#btnAddCookie').onclick = addCookie;
  $('#btnTestNew').onclick = async () => {
    const v = $('#ckValue').value.trim();
    if (!v) return toast('先填入 Cookie 内容', 'err');
    const box = $('#testResult');
    box.innerHTML = `<div class="test-out">正在测试…</div>`;
    const r = await call('test_cookie', v);
    box.innerHTML = renderTestResult(r);
  };
}

function openSettings() {
  syncUIFromSettings();
  openModal('settingsModal');
}
function openModal(id) { $('#' + id).hidden = false; }
function closeModal(id) { $('#' + id).hidden = true; }

/* ============================================================
   搜索
   ============================================================ */
async function doSearch() {
  const q = $('#q').value.trim();
  if (!q) { toast('请输入歌名或粘贴链接', 'err'); return; }
  if (S.busy) return;
  S.busy = true;
  const btn = $('#btnSearch');
  btn.disabled = true;
  btn.querySelector('.spinner').hidden = false;
  btn.querySelector('.btn-label').textContent = '搜索中';

  try {
    const r = await call('search', q);
    if (!r.ok) {
      showSearchError(r.error);
      return;
    }
    S.results = r.results || [];
    $('#resultsSection').hidden = false;
    renderResults();
    const best = S.results.find(x => x._best);
    if (best && S.results.length > 1) {
      toast(`找到 ${S.results.length} 条，已标出最匹配的一条`);
    }
  } catch (e) {
    showSearchError({ message: '搜索请求异常：' + e.message });
  } finally {
    S.busy = false;
    btn.disabled = false;
    btn.querySelector('.spinner').hidden = true;
    btn.querySelector('.btn-label').textContent = '搜索';
  }
}

function showSearchError(err) {
  S.results = [];
  $('#resultsSection').hidden = false;
  $('#resultCount').textContent = '0';
  $('#resultList').innerHTML = errorBox(err, null);
}
function errorBox(err, jobId) {
  err = err || {};
  const detail = err.detail
    ? `<details><summary>技术详情</summary><pre>${esc(err.detail)}</pre></details>`
    : '';
  const retry = jobId
    ? `<div class="row-end" style="margin-top:10px">
         <button class="btn btn-ghost btn-sm" data-retry="${esc(jobId)}">重试</button>
       </div>` : '';
  return `<div class="err-box">
      <div class="err-head">${I.alert}
        <div>
          <div class="err-title">${esc(err.message || '出错了')}</div>
          ${err.hint ? `<div class="err-hint">${esc(err.hint)}</div>` : ''}
        </div>
      </div>${detail}${retry}</div>`;
}

function renderResults() {
  const list = $('#resultList');
  $('#resultCount').textContent = S.results.length;
  if (!S.results.length) { list.innerHTML = ''; return; }
  list.innerHTML = S.results.map((r, i) => `
    <div class="result ${r._best ? 'is-best' : ''}">
      ${r.cover
        ? `<img class="cover" src="${esc(r.cover)}" loading="lazy" alt="">`
        : `<div class="cover cover-ph">${I.music}</div>`}
      <div class="result-main">
        <div class="result-title">
          <span class="t">${esc(r.title)}</span>
          ${r.vip ? '<span class="badge badge-vip">VIP</span>' : ''}
          ${r._best ? '<span class="badge badge-best">最匹配</span>' : ''}
        </div>
        <div class="result-sub">${esc(r.artist)}${r.album ? ' · ' + esc(r.album) : ''}</div>
      </div>
      <div class="result-side">
        <span class="dur">${fmtDur(r.duration)}</span>
        <button class="btn btn-primary btn-sm" data-dl="${i}">
          ${I.down}<span>下载</span>
        </button>
      </div>
    </div>`).join('');

  // 封面加载失败 → 换成占位图（必须用 JS 绑定，SVG 里的双引号会截断内联属性）
  list.querySelectorAll('img.cover').forEach(img => {
    img.onerror = () => {
      const ph = document.createElement('div');
      ph.className = 'cover cover-ph';
      ph.innerHTML = I.music;
      img.replaceWith(ph);
    };
  });

  list.querySelectorAll('[data-dl]').forEach(b => {
    b.onclick = () => startDownload(S.results[+b.dataset.dl]);
  });
}

/* ============================================================
   任务
   ============================================================ */
async function startDownload(song) {
  const r = await call('start_download', song);
  if (!r.ok) { toast(r.error?.message || '启动失败', 'err'); return; }
  createJob(r.jobId, song);
  toast('已加入下载队列', 'ok', 1800);
  $('#jobEmpty').hidden = true;
  updateJobCount();
  // 滚到任务区
  document.querySelector('#jobList').lastElementChild
    ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

const STEPS = 6;

function createJob(id, song) {
  const el = document.createElement('div');
  el.className = 'job is-running';
  el.innerHTML = `
    <div class="job-top">
      <div class="cover cover-ph">${I.music}</div>
      <div class="job-main">
        <div class="job-title">${esc(song.title || '')}</div>
        <div class="job-sub">${esc(song.artist || '')}${song.album ? ' · ' + esc(song.album) : ''}</div>
      </div>
      <div class="job-side">
        <span class="state state-running">
          <span class="spin" style="width:12px;height:12px;border-radius:99px;border:2px solid var(--accent-line);border-top-color:var(--accent);animation:spin .7s linear infinite"></span>
          准备中
        </span>
      </div>
    </div>
    <div class="job-body">
      <div class="stage" hidden>
        <span class="spin"></span><span class="stage-text"></span>
        <span class="steps" style="margin-left:auto"></span>
      </div>
      <div class="bar" hidden><div class="bar-fill"></div></div>
      <div class="metrics" hidden></div>
      <div class="notes"></div>
      <div class="files"></div>
    </div>`;

  const data = {
    id, song, status: 'running', stage: '', step: 0, totalSteps: STEPS,
    progress: {}, notes: [], files: [], error: null,
    meta: null, cover: null, lyric: null, itunes: null, channels: [],
  };
  const refs = {
    el,
    title: el.querySelector('.job-title'),
    sub: el.querySelector('.job-sub'),
    side: el.querySelector('.job-side'),
    stage: el.querySelector('.stage'),
    stageText: el.querySelector('.stage-text'),
    steps: el.querySelector('.steps'),
    bar: el.querySelector('.bar'),
    fill: el.querySelector('.bar-fill'),
    metrics: el.querySelector('.metrics'),
    notes: el.querySelector('.notes'),
    files: el.querySelector('.files'),
    cover: el.querySelector('.cover'),
  };
  refs.steps.innerHTML = Array.from({ length: STEPS }, () => '<i></i>').join('');
  $('#jobList').appendChild(el);
  S.jobs.set(id, { data, refs, el });
  if (song.cover) {
    const img = new Image();
    img.className = 'cover'; img.src = song.cover; img.loading = 'lazy';
    img.onload = () => refs.cover.replaceWith(img);
  }
}

function updateJobCount() {
  const n = S.jobs.size;
  $('#jobCount').textContent = n;
  $('#btnClearJobs').hidden = n === 0;
  $('#jobEmpty').hidden = n > 0;
}

/* ---------------- 事件处理 ---------------- */
pushHandler = function (evt) {
  const j = S.jobs.get(evt.jobId);
  if (!j) return;
  const { data, refs } = j;

  switch (evt.type) {
    case 'status':
      if (evt.status === 'done') { data.status = 'done'; renderDone(j); }
      else if (evt.status === 'error') {
        data.status = 'error'; data.error = evt.error; renderError(j);
      }
      break;

    case 'stage':
      data.stage = evt.text;
      data.step = evt.step || data.step;
      data.totalSteps = evt.totalSteps || data.totalSteps;
      refs.stage.hidden = false;
      refs.stageText.textContent = evt.text;
      renderSteps(j);
      setRunningBadge(j);
      if (data.status === 'running') {
        refs.bar.hidden = false;
        if (!data.progress.total) {
          refs.fill.classList.add('indet');
        }
      }
      break;

    case 'meta':
      data.meta = evt.meta;
      refs.title.textContent = evt.meta.title;
      refs.sub.textContent = [evt.meta.artist, evt.meta.album,
        fmtDur(evt.meta.duration)].filter(Boolean).join(' · ');
      if (evt.meta.cover) {
        const img = new Image();
        img.className = 'cover'; img.src = evt.meta.cover; img.loading = 'lazy';
        img.onload = () => refs.cover.replaceWith(img);
      }
      break;

    case 'itunes': data.itunes = evt.info; break;
    case 'lyric': data.lyric = evt.info; break;
    case 'cover': data.cover = evt.info; break;

    case 'channel':
      data.channels.push(evt.info);
      break;

    case 'progress':
      data.progress = { ext: evt.ext, done: evt.done, total: evt.total,
                        speed: evt.speed, eta: evt.eta };
      renderProgress(j);
      break;

    case 'file':
      data.files.push(evt);
      renderFiles(j);
      break;

    case 'warn':
      data.notes.push(evt);
      renderNotes(j);
      break;
  }
};

function renderSteps(j) {
  const { data, refs } = j;
  const n = data.totalSteps || STEPS;
  if (refs.steps.children.length !== n) {
    refs.steps.innerHTML = Array.from({ length: n }, () => '<i></i>').join('');
  }
  Array.from(refs.steps.children).forEach((el, i) => {
    el.className = i < data.step - 1 ? 'ok' : (i === data.step - 1 ? 'on' : '');
  });
}

function renderProgress(j) {
  const { data, refs } = j;
  const p = data.progress;
  refs.bar.hidden = false;
  refs.fill.classList.remove('indet');
  const pct = p.total ? Math.min(100, p.done * 100 / p.total) : 0;
  refs.fill.style.width = pct.toFixed(1) + '%';
  refs.metrics.hidden = false;

  const parts = [];
  if (p.total) {
    parts.push(`<span class="m"><b>${pct.toFixed(0)}%</b>　${fmtBytes(p.done)} / ${fmtBytes(p.total)}</span>`);
  } else {
    parts.push(`<span class="m">已下载 <b>${fmtBytes(p.done)}</b></span>`);
  }
  if (p.speed) parts.push(`<span class="sep"></span><span class="m">${fmtSpeed(p.speed)}</span>`);
  const eta = fmtEta(p.eta);
  if (eta) parts.push(`<span class="sep"></span><span class="m">剩余 ${eta}</span>`);
  if (p.ext) parts.push(`<span class="sep"></span><span class="m">${p.ext.toUpperCase()}</span>`);
  refs.metrics.innerHTML = parts.join('');
}

/* 状态徽章文案跟着阶段走，比一直写「准备中」有用 */
function badgeText(stage) {
  if (/下载/.test(stage)) return '下载中';
  if (/标签/.test(stage)) return '写入标签';
  if (/封面/.test(stage)) return '取封面';
  if (/歌词/.test(stage)) return '取歌词';
  if (/音源|解析/.test(stage)) return '解析音源';
  if (/歌曲信息|信息/.test(stage)) return '读取信息';
  return '处理中';
}

function setRunningBadge(j) {
  const { data, refs } = j;
  if (data.status !== 'running') return;
  const txt = badgeText(data.stage || '');
  const el = refs.side.querySelector('.state-running');
  if (el && el.lastChild && el.lastChild.nodeType === 3) {
    el.lastChild.textContent = txt;
  }
}

function renderNotes(j) {
  const { data, refs } = j;
  refs.notes.innerHTML = data.notes.map(n =>
    `<div class="warn-row">${I.warn}<span>${esc(n.text)}${n.hint
      ? '　<span class="dim">' + esc(n.hint) + '</span>' : ''}</span></div>`).join('');
}

function renderFiles(j) {
  const { data, refs } = j;
  refs.files.innerHTML = data.files.map(f => {
    const tags = [];
    if (f.cover) tags.push(`<span class="tg">${I.tag}封面 ${esc(f.cover)}</span>`);
    if (f.lyricLines) tags.push(`<span class="tg">${I.tag}歌词 ${f.lyricLines} 行时间轴</span>`);
    tags.push(`<span class="tg">${I.tag}${esc(f.channel === 'official' ? '官方通道' : f.channel === 'aggregator' ? '聚合接口' : 'gequbao 兜底')}</span>`);
    return `<div class="file-row">
        <span class="file-ext ${esc(f.ext)}">${esc(f.ext.toUpperCase())}</span>
        <div class="file-info">
          <div class="file-name">${esc(basename(f.path))}</div>
          <div class="file-tags">
            <span class="tg">${fmtBytes(f.size)}</span>
            <span class="tg">${esc(f.level)}</span>${tags.join('')}
          </div>
        </div>
        <button class="btn btn-ghost btn-sm" data-open="${esc(f.path)}">打开</button>
        <button class="btn btn-ghost btn-sm" data-info="${esc(f.path)}">标签</button>
      </div>`;
  }).join('');

  refs.files.querySelectorAll('[data-open]').forEach(b =>
    b.onclick = () => call('open_folder', b.dataset.open));
  refs.files.querySelectorAll('[data-info]').forEach(b =>
    b.onclick = () => showFileInfo(b.dataset.info));
}

function renderDone(j) {
  const { data, refs, el } = j;
  el.className = 'job is-done';
  refs.stage.hidden = true;
  refs.bar.hidden = false;
  refs.fill.classList.remove('indet');
  refs.fill.style.width = '100%';
  refs.bar.classList.add('done');
  refs.side.innerHTML = `<span class="state state-done">${I.check}完成</span>
    <button class="btn btn-ghost btn-sm" data-folder="1">${I.folder}<span>文件夹</span></button>`;
  refs.side.querySelector('[data-folder]').onclick = () =>
    call('open_folder', data.files.length ? data.files[0].path : '');
  renderFiles(j);
}

function renderError(j) {
  const { data, refs, el } = j;
  el.className = 'job is-error';
  refs.stage.hidden = true;
  refs.bar.hidden = true;
  refs.side.innerHTML = `<span class="state state-error">${I.alert}失败</span>`;
  const box = document.createElement('div');
  box.innerHTML = errorBox(data.error, data.id);
  refs.notes.appendChild(box.firstElementChild);
  const rb = refs.notes.querySelector('[data-retry]');
  if (rb) rb.onclick = () => { el.remove(); S.jobs.delete(data.id); updateJobCount(); startDownload(data.song); };
  if (data.progress.done) {
    refs.metrics.hidden = false;
    refs.metrics.innerHTML = `<span class="m">已下载 <b>${fmtBytes(data.progress.done)}</b> 后失败</span>`;
  }
}

/* ============================================================
   文件标签详情
   ============================================================ */
async function showFileInfo(path) {
  const body = $('#fileModalBody');
  body.innerHTML = '<div class="test-out">读取中…</div>';
  openModal('fileModal');
  const r = await call('probe_file', path);
  if (!r.ok) { body.innerHTML = errorBox(r.error, null); return; }
  const i = r.info;
  const rows = [
    ['文件名', basename(i.path)],
    ['格式', (i.ext || '').toUpperCase() +
      (i.codec && i.codec !== i.ext ? ' / ' + i.codec : '')],
    ['大小', fmtBytes(i.size)],
    ['码率', i.bitrate ? Math.round(i.bitrate / 1000) + ' kbps' : '—'],
    ['采样率', i.sampleRate ? i.sampleRate + ' Hz' : '—'],
    ['时长', i.duration ? fmtDur(i.duration * 1000) : '—'],
    ['—', ''],
    ['标题', i.title || '—'],
    ['歌手', i.artist || '—'],
    ['专辑', i.album || '—'],
    ['年份', i.date || '—'],
    ['流派', i.genre || '—'],
    ['音轨', i.track || '—'],
    ['—', ''],
    ['内嵌封面', i.cover ? `${i.cover}${i.coverSize ? ' · ' + i.coverSize : ''}` : '无'],
    ['歌词行数', i.lyricLines ? i.lyricLines + ' 行' : '无'],
    ['同步时间轴', i.syncLines ? i.syncLines + ' 条' : '无'],
  ];
  body.innerHTML = '<dl class="kv">' + rows.map(([k, v]) =>
    k === '—' ? '<div style="grid-column:1/-1;height:1px;background:var(--line)"></div>'
      : `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + '</dl>';
}

/* ============================================================
   设置
   ============================================================ */
function renderCookieList() {
  const list = $('#cookieList');
  const d = S.settings || {};
  const cks = d.cookies || [];
  if (!cks.length) {
    list.innerHTML = `<div class="empty-mini">还没有配置账号。不配置也能下载，
      只是会走第三方聚合接口；配置后优先走官方无损通道。</div>`;
    return;
  }
  list.innerHTML = cks.map(c => `
    <div class="cookie-row ${c.id === d.activeCookieId ? 'active' : ''}" data-id="${esc(c.id)}">
      <span class="cookie-radio"></span>
      <div class="cookie-info">
        <div class="cookie-name">${esc(c.name)}${c.enabled ? '' : ' <span class="dim">（已停用）</span>'}</div>
        <div class="cookie-meta" data-meta="${esc(c.id)}">${esc(c.masked)}</div>
      </div>
      <div class="cookie-actions">
        <button class="btn btn-ghost btn-sm" data-test="${esc(c.id)}">测试</button>
        <button class="btn btn-ghost btn-sm" data-edit="${esc(c.id)}">编辑</button>
        <button class="btn btn-ghost btn-sm" data-del="${esc(c.id)}">删除</button>
      </div>
    </div>`).join('');

  list.querySelectorAll('.cookie-row').forEach(row => {
    row.querySelector('.cookie-radio').onclick = async () => {
      await call('set_active_cookie', row.dataset.id);
      S.settings.activeCookieId = row.dataset.id;
      renderCookieList(); updateSourceChip();
    };
  });
  list.querySelectorAll('[data-test]').forEach(b => b.onclick = async () => {
    const meta = list.querySelector(`[data-meta="${b.dataset.test}"]`);
    meta.className = 'cookie-meta';
    meta.textContent = '测试中…';
    const r = await call('test_cookie', null, b.dataset.test);
    if (!r.ok) { meta.className = 'cookie-meta err'; meta.textContent = r.error.message; return; }
    const t = r.result;
    meta.className = 'cookie-meta ' + (t.ok ? 'ok' : 'err');
    meta.textContent = `${t.nickname}　${t.vipText}　·　${t.reason}`;
    if (!t.ok) toast(t.reason + (t.hint ? '：' + t.hint : ''), 'err', 5200);
  });
  list.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    await call('remove_cookie', b.dataset.del);
    S.settings = await call('get_settings');
    renderCookieList(); updateSourceChip();
    toast('已删除', 'ok', 1600);
  });
  list.querySelectorAll('[data-edit]').forEach(b => b.onclick = async () => {
    const c = (S.settings.cookies || []).find(x => x.id === b.dataset.edit);
    if (!c) return;
    const r = await call('get_cookie_value', c.id);
    $('#ckName').value = c.name;
    $('#ckValue').value = r.value || '';
    $('#addCookieBox').open = true;
    $('#btnAddCookie').textContent = '更新账号';
    $('#btnAddCookie').onclick = () => updateCookie(c.id);
    $('#addCookieBox').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

async function addCookie() {
  const name = $('#ckName').value.trim() || '未命名账号';
  const value = $('#ckValue').value.trim();
  if (!value) { toast('请填入 MUSIC_U 的值', 'err'); return; }
  const r = await call('add_cookie', name, value);
  if (!r.ok) { toast(r.error.message, 'err'); return; }
  S.settings = await call('get_settings');
  S.settings.activeCookieId = r.id;
  await call('set_active_cookie', r.id);
  $('#ckName').value = ''; $('#ckValue').value = ''; $('#testResult').innerHTML = '';
  $('#addCookieBox').open = false;
  renderCookieList(); updateSourceChip();
  toast('账号已保存，正在后台测试…', 'ok', 2600);
  // 自动测一次
  const t = await call('test_cookie', value);
  if (t.ok && t.result) toast(t.result.reason, t.result.ok ? 'ok' : 'err', 5600);
}

async function updateCookie(cid) {
  const patch = { name: $('#ckName').value.trim() || '未命名账号',
                  value: $('#ckValue').value.trim() };
  const r = await call('update_cookie', cid, patch);
  if (!r.ok) { toast(r.error.message, 'err'); return; }
  S.settings = await call('get_settings');
  $('#ckName').value = ''; $('#ckValue').value = '';
  $('#addCookieBox').open = false;
  $('#btnAddCookie').textContent = '保存账号';
  $('#btnAddCookie').onclick = addCookie;
  renderCookieList(); updateSourceChip();
  toast('已更新', 'ok', 1800);
}

function renderTestResult(r) {
  if (!r.ok) return `<div class="test-out err"><b>测试失败</b>${esc(r.error.message)}</div>`;
  const t = r.result;
  return `<div class="test-out ${t.ok ? 'ok' : 'err'}">
      <b>${t.ok ? '✓ 这个账号可以用' : '✗ 这个账号不能用'}</b>
      昵称：${esc(t.nickname || '—')}　类型：${esc(t.vipText || '—')}<br>
      ${esc(t.reason || '')}${t.hint ? '<br><span class="dim">' + esc(t.hint) + '</span>' : ''}
    </div>`;
}

async function saveSettings() {
  const patch = {
    outputDir: $('#setOutDir').value.trim(),
    format: getSeg('#setFormat'),
    sourceOrder: $('#setSource').value,
    coverMax: parseInt($('#setCoverMax').value || '1200', 10) || 0,
    embedCover: $('#setCover').checked,
    embedLyrics: $('#setLyrics').checked,
    writeLrc: $('#setLrc').checked,
  };
  const r = await call('save_settings', patch);
  if (!r.ok) { toast(r.error.message, 'err'); return; }
  S.settings = await call('get_settings');
  syncUIFromSettings();
  updateSourceChip();
  closeModal('settingsModal');
  toast('设置已保存', 'ok', 2000);
}

/* ---------------- 启动 ---------------- */
if (IS_SERVE) {
  init();
} else {
  (async () => {
    await waitForBridge();
    try { await init(); }
    catch (e) { window.__errs.push('init: ' + (e && e.message ? e.message : e)); }
  })();
}
