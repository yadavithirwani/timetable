// Timetable Studio - upload -> review -> generate -> explore.
// Plain ES module, no build step. Everything read from the uploaded Excel is escaped
// with esc() before it touches innerHTML.

const API = '/api/v2';
const view = document.getElementById('view');

const state = {
  wsId: null,
  ws: null,          // workspace summary
  quotas: null,      // saved quotas
  rules: null,       // saved rules
  qDraft: null,      // quotas being edited
  reviewTab: 'quotas',
  band: null,
  job: null,
  poll: null,
  tt: null,          // { days, periods_per_day, subjects, teachers, classes, rooms, lessons, timetable }
  ttMode: 'class',
  ttEntity: null,
  ttSearch: '',
  focus: null,
  availOpen: null,   // teacher whose availability editor is open (Review)
  classOpen: null,   // class whose periods editor is open (Review)
  swap: false,       // swap-by-clicking mode
  swapPick: null,    // {day, period} first cell picked
  chatOpen: false,
  chat: [],          // [{role:'user'|'assistant', text} | {role:'proposal'|'cover'|'error'|'busy', ...}]
  chatBusy: false,
  proposal: null,    // latest live proposal (highlighted in the grid)
  ai: null,          // {ai_available, edits, undo_available}
};

// ---------------------------------------------------------------- helpers
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const title = (s) => String(s || '').toLowerCase().replace(/(^|[\s(.-])([a-z])/g, (m, a, b) => a + b.toUpperCase());
const clone = (o) => JSON.parse(JSON.stringify(o));
const fmtS = (s) => (s == null ? '–' : s < 10 ? `${(+s).toFixed(1)}s` : s < 90 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const body = isJson ? await res.json() : null;
  if (!res.ok) throw new Error(body?.detail || `Request failed (${res.status})`);
  return body;
}
const putJSON = (path, data) => api(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
const postJSON = (path, data) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data || {}) });

let toastTimer;
function toast(msg, kind = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = 'toast'), 3200);
}

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
function remember(ws) {
  const list = store.get('tts.recent', []).filter((x) => x.id !== ws.id);
  list.unshift({ id: ws.id, name: ws.name, filename: ws.filename, at: Date.now() });
  store.set('tts.recent', list.slice(0, 8));
}
function forget(id) {
  store.set('tts.recent', store.get('tts.recent', []).filter((x) => x.id !== id));
  store.del(`tts.job.${id}`);
}

// subject colours: stable hue per subject name
const HUES = [152, 28, 210, 340, 48, 265, 186, 8, 95, 300, 228, 125, 62, 320];
const isDark = () => document.documentElement.dataset.theme === 'dark' ||
  (document.documentElement.dataset.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
function subjColor(name) {
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = HUES[h % HUES.length];
  return isDark()
    ? { fg: `hsl(${hue} 55% 74%)`, bg: `hsl(${hue} 22% 19%)`, line: `hsl(${hue} 22% 27%)`, dot: `hsl(${hue} 50% 60%)` }
    : { fg: `hsl(${hue} 50% 27%)`, bg: `hsl(${hue} 52% 93%)`, line: `hsl(${hue} 40% 85%)`, dot: `hsl(${hue} 45% 50%)` };
}
const colorVars = (name) => { const c = subjColor(name); return `--c-fg:${c.fg};--c-bg:${c.bg};--c-line:${c.line}`; };

const ICON = {
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12m0 0 4.5-4.5M12 16l-4.5-4.5"/><path d="M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/></svg>',
  print: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="8" rx="2"/><path d="M7 14h10v6H7z"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.1A8 8 0 1 1 20 12z"/><path d="M9 11h.01M12 11h.01M15 11h.01"/></svg>',
  swap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4 3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8"/></svg>',
  undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
};

// ---------------------------------------------------------------- routing
const STEPS = [['upload', 'Upload'], ['review', 'Review'], ['generate', 'Generate'], ['timetable', 'Timetable']];

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'w' && parts[1]) return { wsId: parts[1], page: parts[2] || 'review' };
  return { wsId: null, page: 'upload' };
}
const go = (page, wsId = state.wsId) => { location.hash = page === 'upload' ? '#/' : `#/w/${wsId}/${page}`; };

function renderStepper(page) {
  const idx = STEPS.findIndex(([k]) => k === page);
  const hasWs = !!state.ws;
  const hasTT = !!state.ws?.has_timetable;
  document.getElementById('stepper').innerHTML = STEPS.map(([k, label], i) => {
    const disabled = (k !== 'upload' && !hasWs) || (k === 'timetable' && !hasTT);
    const cls = ['step', i === idx ? 'active' : '', i < idx ? 'done' : '', disabled ? 'disabled' : ''].join(' ');
    const href = k === 'upload' ? '#/' : `#/w/${state.wsId}/${k}`;
    return `<a class="${cls}" href="${href}" ${i === idx ? 'aria-current="step"' : ''}><span class="num">${i < idx ? '✓' : i + 1}</span><span class="label">${label}</span></a>`;
  }).join('');
  const chip = document.getElementById('ws-chip');
  if (hasWs) {
    chip.hidden = false;
    chip.innerHTML = `<span class="muted">School</span> <strong title="${esc(state.ws.workspace.name)}">${esc(state.ws.workspace.name)}</strong>`;
  } else chip.hidden = true;
}

async function route() {
  const { wsId, page } = parseRoute();
  if (state.poll && page !== 'generate') { clearInterval(state.poll); state.poll = null; }
  try {
    if (wsId && wsId !== state.wsId) {
      Object.assign(state, { wsId, ws: null, quotas: null, rules: null, qDraft: null, tt: null, ttEntity: null, job: null, band: null, focus: null,
        chat: [], proposal: null, ai: null, swap: false, swapPick: null });
    }
    if (page !== 'timetable' && page !== 'review' && state.chatOpen) openChat(false);
    if (!wsId) Object.assign(state, { wsId: null, ws: null, tt: null });
    if (wsId && !state.ws) {
      view.innerHTML = '<div class="empty"><div class="progress-bar" style="margin:auto"><i></i></div></div>';
      await loadWorkspace();
    }
    renderStepper(page);
    view.classList.remove('fade-in'); void view.offsetWidth; view.classList.add('fade-in');
    if (page === 'upload') renderUpload();
    else if (page === 'review') renderReview();
    else if (page === 'generate') renderGenerate();
    else if (page === 'timetable') await renderTimetable();
    else go('review');
  } catch (e) {
    if (wsId) forget(wsId);
    renderStepper('upload');
    view.innerHTML = `<div class="empty"><h2>Couldn't open this school</h2><p>${esc(e.message)}</p><a class="btn primary" href="#/">Start over</a></div>`;
  }
}

async function loadWorkspace() {
  const [ws, quotas, rules] = await Promise.all([
    api(`/workspaces/${state.wsId}`), api(`/workspaces/${state.wsId}/quotas`), api(`/workspaces/${state.wsId}/rules`)]);
  Object.assign(state, { ws, quotas, rules, qDraft: clone(quotas) });
  remember(ws.workspace);
}

function setSummary(ws) {
  state.ws = ws;
  state.tt = null;
  remember(ws.workspace);
}

// ---------------------------------------------------------------- 1. upload
function renderUpload() {
  const recent = store.get('tts.recent', []);
  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="eyebrow">Step 1 · Upload</div>
        <h1>Turn your class-wise sheet<br>into a clash-free timetable.</h1>
        <p>Upload the Excel export that lists, for every class, which teacher takes which subject. You'll check the period counts next, then generate. Every timetable is independently verified before you see it.</p>
      </div>
    </div>
    <div class="upload-grid">
      <label class="dropzone" id="drop">
        <input type="file" id="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="Choose Excel file">
        <div class="glyph">${ICON.upload}</div>
        <div id="drop-text">
          <h2>Drop your .xlsx here</h2>
          <p class="muted" style="margin:6px 0 0">or click to choose a file · up to 10 MB</p>
        </div>
      </label>
      <div class="card pad">
        <h3>Expected format</h3>
        <p class="muted" style="margin:4px 0 14px;font-size:14px">First sheet, three columns. This is the "Contracts Classes" export from most scheduling software.</p>
        <div class="sheet">
          <table>
            <tr><th class="rowhead"></th><th>Class</th><th>Teacher</th><th>Subject</th></tr>
            <tr><td class="rowhead">1</td><td class="cls hl">6 A</td><td></td><td></td></tr>
            <tr><td class="rowhead">2</td><td></td><td>Anita Sharma</td><td>CLASS TEACHER</td></tr>
            <tr><td class="rowhead">3</td><td></td><td>Anita Sharma</td><td>ENG</td></tr>
            <tr><td class="rowhead">4</td><td></td><td class="hl">Isha Malhotra,Deepak Saini</td><td>DANCE/YOGA</td></tr>
            <tr><td class="rowhead">5</td><td></td><td></td><td></td></tr>
            <tr><td class="rowhead">6</td><td class="cls hl">6 B</td><td></td><td></td></tr>
            <tr><td class="rowhead">7</td><td></td><td>Vikram Rao</td><td>MATHS</td></tr>
          </table>
        </div>
        <ul class="rules-list">
          <li><span class="dot">1</span><span><b>Class name on its own row</b> starts a block; the rows below belong to it.</span></li>
          <li><span class="dot">2</span><span><b>Several teachers, comma-separated,</b> means all of them teach that lesson together.</span></li>
          <li><span class="dot">3</span><span><b>Same multi-teacher row in two sections</b> of a grade is one joint lesson, taught at the same time.</span></li>
          <li><span class="dot">4</span><span><b>Periods per week aren't in the sheet</b>, so you set them on the next screen.</span></li>
        </ul>
        <div class="row" style="margin-top:18px">
          <a class="btn" href="${API}/template.xlsx" download>${ICON.download} Template</a>
          <button class="btn ghost" id="demo">${ICON.spark} Try a demo school</button>
        </div>
      </div>
    </div>
    ${recent.length ? `
    <section class="recent">
      <h3>Recent schools</h3>
      <div class="recent-list">
        ${recent.map((r) => `
          <a class="card recent-item" href="#/w/${esc(r.id)}/review">
            <span class="sw">${esc((r.name || '?').trim()[0] || '?').toUpperCase()}</span>
            <span style="min-width:0"><strong style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.name)}</strong>
            <span class="muted" style="font-size:13px">${esc(r.filename || '')} · ${new Date(r.at).toLocaleDateString()}</span></span>
          </a>`).join('')}
      </div>
    </section>` : ''}`;

  const drop = document.getElementById('drop');
  const input = document.getElementById('file');
  input.addEventListener('change', () => input.files[0] && upload(input.files[0]));
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('drag'); }));
  drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
  document.getElementById('demo').addEventListener('click', async (e) => {
    e.target.disabled = true;
    try { const ws = await postJSON('/demo'); openNew(ws); } catch (err) { toast(err.message, 'bad'); e.target.disabled = false; }
  });
}

async function upload(file) {
  const drop = document.getElementById('drop');
  const text = document.getElementById('drop-text');
  if (!/\.xlsx$/i.test(file.name)) { toast('Please choose an .xlsx file (Excel > Save As > Excel Workbook).', 'bad'); return; }
  drop.classList.add('busy');
  text.innerHTML = `<h2>Reading ${esc(file.name)}…</h2><p class="muted" style="margin:6px 0 12px">Finding classes, teachers and joint lessons</p><div class="progress-bar" style="margin:auto"><i></i></div>`;
  try {
    const ws = await api(`/workspaces?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: await file.arrayBuffer() });
    openNew(ws);
  } catch (e) {
    drop.classList.remove('busy');
    text.innerHTML = `<h2>That didn't work</h2><p style="margin:6px 0 0;color:var(--bad)">${esc(e.message)}</p><p class="muted" style="margin:8px 0 0">Choose another file to try again.</p>`;
  }
}

function openNew(ws) {
  state.wsId = ws.workspace.id;
  state.ws = null;
  remember(ws.workspace);
  go('review', ws.workspace.id);
}

// ---------------------------------------------------------------- 2. review
function renderReview() {
  const ws = state.ws;
  const c = ws.counts;
  const missing = Object.values(ws.quota_table).flat().filter((e) => e.source === 'missing').length;
  const offWeek = ws.classes.filter((x) => !x.self_contained && x.total !== ws.calendar.slots).length;
  const notes = [...ws.import_warnings, ...ws.warnings];
  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="eyebrow">Step 2 · Review</div>
        <h1>${esc(ws.workspace.name)}</h1>
        <p>Read ${c.excel_rows} rows from <b>${esc(ws.workspace.filename)}</b>. Check the period counts, then generate.</p>
      </div>
      <div class="row">
        <button class="btn primary" id="open-chat-review">${ICON.chat} Assistant</button>
        <label class="btn">${ICON.file} Replace file<input type="file" id="replace" accept=".xlsx" hidden></label>
        <button class="btn ghost danger" id="del">Delete</button>
      </div>
    </div>
    <div class="stats">
      ${stat('Classes', c.scheduled_classes, c.classes > c.scheduled_classes ? `+${c.classes - c.scheduled_classes} self-contained` : 'to schedule')}
      ${stat('Teachers', c.teachers, 'with lessons')}
      ${stat('Lessons', c.lessons, `${ws.calendar.days.length} days × ${ws.calendar.periods_per_day} periods`)}
      ${stat('Joint lessons', c.joint_lessons, 'shared by sections')}
      ${stat('Co-taught', c.co_taught_lessons, 'several teachers')}
    </div>
    <div style="margin-top:20px">
      ${ws.errors.length ? `<div class="alert bad"><span class="icon">⛔</span><div><b>Fix these before generating</b><ul>${ws.errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : ''}
      ${!ws.errors.length && !missing ? `<div class="alert good"><span class="icon">✓</span><div><b>Data looks consistent.</b> Every class fits in the week and no teacher is over capacity.</div></div>` : ''}
      ${notes.length ? `<details class="alert warn"><summary>${notes.length} note${notes.length > 1 ? 's' : ''} to look at</summary><ul>${notes.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></details>` : ''}
    </div>
    <div class="tabs" role="tablist">
      ${tab('quotas', 'Periods per week', missing ? `<span class="badge warn">${missing} missing</span>` : '')}
      ${tab('classes', 'Classes', offWeek ? `<span class="badge warn">${offWeek}</span>` : '')}
      ${tab('teachers', 'Teachers', Object.keys(state.quotas?.teacher_unavailable || {}).length ? `<span class="badge">${Object.keys(state.quotas.teacher_unavailable).length} part-time</span>` : '')}
      ${tab('joint', 'Joint lessons', `<span class="badge">${c.joint_lessons}</span>`)}
      ${tab('labs', 'Labs', `<span class="badge">${Object.values(ws.labs || {}).reduce((a, l) => a + (l.count || 0), 0)}</span>`)}
      ${tab('settings', 'Week & rules')}
    </div>
    <div id="tab-body"></div>
    <div class="next-cta">
      ${ws.errors.length ? '<span class="muted">Resolve the issues above to continue</span>' : ''}
      <button class="btn primary big" id="next" ${ws.errors.length ? 'disabled' : ''}>Continue to generate ${ICON.arrow}</button>
    </div>`;

  view.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => { state.reviewTab = t.dataset.tab; renderReview(); }));
  document.getElementById('next').addEventListener('click', () => go('generate'));
  document.getElementById('open-chat-review').addEventListener('click', () => openChat(true));
  document.getElementById('replace').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const ws2 = await api(`/workspaces/${state.wsId}/excel?filename=${encodeURIComponent(f.name)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: await f.arrayBuffer() });
      setSummary(ws2); toast('File replaced'); renderReview();
    } catch (err) { toast(err.message, 'bad'); }
  });
  document.getElementById('del').addEventListener('click', async () => {
    if (!confirm(`Delete "${state.ws.workspace.name}" and its timetable from the server? This cannot be undone.`)) return;
    try { await api(`/workspaces/${state.wsId}`, { method: 'DELETE' }); } catch { /* already gone */ }
    forget(state.wsId);
    toast('Deleted');
    go('upload');
  });

  const body = document.getElementById('tab-body');
  ({ quotas: reviewQuotas, classes: reviewClasses, teachers: reviewTeachers, joint: reviewJoint, labs: reviewLabs, settings: reviewSettings })[state.reviewTab](body);
}

const stat = (label, value, sub) => `<div class="card stat"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${esc(sub)}</div></div>`;
const tab = (key, label, extra = '') => `<button class="tab ${state.reviewTab === key ? 'active' : ''}" data-tab="${key}" role="tab" aria-selected="${state.reviewTab === key}">${label}${extra}</button>`;

function quotaValue(q, band, subject) {
  const b = q.band_quotas?.[band] || {};
  if (subject in b) return b[subject];
  if (subject in (q.default || {})) return q.default[subject];
  return null;
}

function countChanges() {
  let n = 0;
  for (const [band, entries] of Object.entries(state.ws.quota_table)) {
    for (const e of entries) if (quotaValue(state.qDraft, band, e.subject) !== quotaValue(state.quotas, band, e.subject)) n++;
  }
  return n + extraChanges();
}

function reviewQuotas(body) {
  const bands = Object.keys(state.ws.quota_table).sort((a, b) => bandOrder(a) - bandOrder(b));
  if (!bands.length) { body.innerHTML = '<div class="empty">No subjects found.</div>'; return; }
  if (!bands.includes(state.band)) state.band = bands[0];
  const band = state.band;
  const entries = state.ws.quota_table[band];
  const classesInBand = state.ws.classes.filter((c) => c.band === band && !c.self_contained);
  const slots = state.ws.calendar.slots;
  body.innerHTML = `
    <div class="band-pills">
      ${bands.map((b) => {
        const miss = state.ws.quota_table[b].filter((e) => e.source === 'missing').length;
        return `<button class="pill ${b === band ? 'active' : ''}" data-band="${esc(b)}">Grades ${esc(b)}${miss ? ` <span class="badge warn">${miss}</span>` : ''}</button>`;
      }).join('')}
    </div>
    <div class="card" style="overflow:hidden">
      <table class="qtable">
        <thead><tr><th>Subject</th><th>Periods / week</th><th>Taught in</th></tr></thead>
        <tbody>
          ${entries.map((e) => {
            const v = quotaValue(state.qDraft, band, e.subject);
            const changed = v !== quotaValue(state.quotas, band, e.subject);
            return `<tr>
              <td class="subj"><span class="subj-dot" style="background:${subjColor(e.subject).dot}"></span>${esc(e.subject)}
                ${e.source === 'missing' && v == null ? ' <span class="badge warn">not set</span>' : ''}</td>
              <td>${stepper(e.subject, v, changed, e.source === 'missing' && v == null)}</td>
              <td class="classes">${e.classes.length} class${e.classes.length > 1 ? 'es' : ''} · ${esc(e.classes.slice(0, 8).join(', '))}${e.classes.length > 8 ? '…' : ''}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
    <p class="muted" style="font-size:13px;margin-top:12px">
      Week fill for this band (saved values): ${classesInBand.map((c) => `<span class="badge ${c.total === slots ? 'good' : c.total > slots ? 'bad' : 'warn'}">${esc(c.name)} ${c.total}/${slots}</span>`).join(' ')}
      ${state.qDraft.fill_remaining_with_class_teacher ? '<br>Any gap is filled with class-teacher periods.' : ''}
    </p>
    <div id="savebar-slot"></div>`;

  body.querySelectorAll('.pill').forEach((p) => p.addEventListener('click', () => { state.band = p.dataset.band; reviewQuotas(body); }));
  body.querySelectorAll('.stepper-input').forEach((el) => {
    const subject = el.dataset.subject;
    const input = el.querySelector('input');
    const set = (n) => {
      const max = slots;
      const val = n === '' || n == null || Number.isNaN(n) ? null : Math.max(0, Math.min(max, Math.round(n)));
      state.qDraft.band_quotas = state.qDraft.band_quotas || {};
      state.qDraft.band_quotas[band] = state.qDraft.band_quotas[band] || {};
      if (val == null) delete state.qDraft.band_quotas[band][subject];
      else state.qDraft.band_quotas[band][subject] = val;
      const now = quotaValue(state.qDraft, band, subject);
      input.value = now ?? '';
      el.classList.toggle('changed', now !== quotaValue(state.quotas, band, subject));
      el.classList.toggle('missing', now == null);
      renderSavebar();
    };
    el.querySelector('[data-d="-"]').addEventListener('click', () => set((+input.value || 0) - 1));
    el.querySelector('[data-d="+"]').addEventListener('click', () => set((+input.value || 0) + 1));
    input.addEventListener('change', () => set(input.value === '' ? null : +input.value));
  });
  renderSavebar();
}

const stepper = (subject, v, changed, missing) => `
  <span class="stepper-input ${changed ? 'changed' : ''} ${missing ? 'missing' : ''}" data-subject="${esc(subject)}">
    <button type="button" data-d="-" aria-label="Fewer periods of ${esc(subject)}">−</button>
    <input type="number" min="0" value="${v ?? ''}" placeholder="?" aria-label="Periods per week of ${esc(subject)}">
    <button type="button" data-d="+" aria-label="More periods of ${esc(subject)}">+</button>
  </span>`;

function renderSavebar() {
  const slot = document.getElementById('savebar-slot');
  if (!slot) return;
  const n = countChanges();
  if (!n) { slot.innerHTML = ''; return; }
  slot.innerHTML = `<div class="savebar"><span><b>${n}</b> unsaved change${n > 1 ? 's' : ''}</span><span class="spacer"></span>
    <button class="btn ghost" id="discard">Discard</button><button class="btn primary" id="save">Save & re-check</button></div>`;
  document.getElementById('discard').onclick = () => { state.qDraft = clone(state.quotas); renderReview(); };
  document.getElementById('save').onclick = saveQuotas;
}

async function saveQuotas() {
  const btn = document.getElementById('save');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  try {
    const ws = await putJSON(`/workspaces/${state.wsId}/quotas`, state.qDraft);
    state.quotas = clone(state.qDraft);
    setSummary(ws);
    toast(ws.errors.length ? 'Saved - some issues need fixing' : 'Saved - data re-checked');
    renderStepper('review');
    renderReview();
  } catch (e) { toast(e.message, 'bad'); if (btn) { btn.disabled = false; btn.textContent = 'Save & re-check'; } }
}

const bandOrder = (b) => { const m = String(b).match(/-?\d+/); return b === 'pre' ? -5 : m ? +m[0] : 99; };

function reviewClasses(body) {
  const slots = state.ws.calendar.slots;
  const open = state.ws.classes.find((c) => c.name === state.classOpen && !c.self_contained);
  const overridden = state.qDraft.class_overrides || {};
  body.innerHTML = `<p class="muted" style="margin-top:0">Click a class to change its periods per week without changing the rest of its grade.</p>
    <div id="class-editor">${open ? classEditor(open) : ''}</div>
    <div class="class-grid">${state.ws.classes.map((c) => {
    const real = c.total - c.filled_by_class_teacher;
    const cls = c.self_contained ? '' : c.total > slots ? 'over' : c.total < slots ? 'under' : '';
    const nOver = Object.keys(overridden[c.name] || {}).length;
    return `<button type="button" class="card class-card ${c.self_contained ? '' : 'clickable'} ${c.name === state.classOpen ? 'active' : ''}" data-class="${esc(c.name)}" ${c.self_contained ? 'disabled' : ''}>
      <div class="top"><span class="name">${esc(c.name)}</span><span class="mono ${cls ? '' : 'muted'}" style="${cls === 'over' ? 'color:var(--bad)' : cls === 'under' ? 'color:var(--warn)' : ''}">${c.total}/${slots}</span></div>
      <div class="fill ${cls}"><i style="width:${Math.min(100, real / slots * 100)}%"></i><i class="ct" style="width:${Math.min(100, c.filled_by_class_teacher / slots * 100)}%"></i></div>
      <div class="muted" style="font-size:13px">${c.self_contained ? 'Self-contained · ' : ''}CT ${esc(title(c.class_teacher) || '–')}${c.filled_by_class_teacher ? ` · +${c.filled_by_class_teacher} CT periods` : ''}</div>
      ${nOver ? `<span class="badge accent" style="margin-top:6px">${nOver} own value${nOver > 1 ? 's' : ''}</span>` : ''}
    </button>`;
  }).join('')}</div>
    <div id="savebar-slot"></div>`;
  const redraw = () => reviewClasses(body);
  body.querySelectorAll('.class-card.clickable').forEach((b) => { b.onclick = () => {
    state.classOpen = state.classOpen === b.dataset.class ? null : b.dataset.class; redraw();
    if (state.classOpen) document.getElementById('class-editor').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }; });
  if (open) wireClassEditor(document.getElementById('class-editor'), open, redraw);
  renderSavebar();
}

function reviewTeachers(body) {
  body.innerHTML = `
    <div class="row" style="margin-bottom:12px"><input class="input" id="tsearch" placeholder="Search teachers or subjects" style="width:min(360px,100%)"></div>
    <div class="card" style="overflow:auto;max-height:70vh"><table class="list-table"><thead><tr><th>Teacher</th><th>Periods / week</th><th>Available</th><th>Subjects</th><th>Classes</th></tr></thead><tbody id="trows"></tbody></table></div>
    <div id="savebar-slot"></div>`;
  const rows = document.getElementById('trows');
  const draw = (q) => {
    const ql = q.toLowerCase();
    rows.innerHTML = state.ws.teachers.filter((t) => !ql || t.name.toLowerCase().includes(ql) || t.subjects.some((s) => s.toLowerCase().includes(ql)))
      .map((t) => {
        const pct = t.load / t.capacity;
        const offList = state.qDraft.teacher_unavailable?.[t.name] || [];
        const changed = !jsonEq(offList.length ? offList : null, state.quotas.teacher_unavailable?.[t.name] || null);
        const isOpen = state.availOpen === t.name;
        return `<tr class="${isOpen ? 'open' : ''}"><td><b>${esc(title(t.name))}</b></td>
          <td><div class="load" title="${t.load} of ${t.capacity} possible periods"><span class="mono">${t.load}/${t.capacity}</span><span class="bar"><i class="${pct > 1 ? 'over' : pct > .85 ? 'hot' : ''}" style="width:${Math.min(100, pct * 100)}%"></i></span></div></td>
          <td><button type="button" class="link avail-btn ${changed ? 'changed' : ''}" data-avail="${esc(t.name)}">${offList.length ? esc(availSummary(offList)) + ' off' : 'Every period'}</button></td>
          <td><div class="chips">${t.subjects.map((s) => `<span class="badge">${esc(s)}</span>`).join('')}</div></td>
          <td class="muted" style="font-size:13px">${esc(t.classes.join(', '))}</td></tr>
          ${isOpen ? `<tr class="avail-row"><td colspan="5">${availEditor(t.name)}</td></tr>` : ''}`;
      }).join('');
    rows.querySelectorAll('[data-avail]').forEach((b) => { b.onclick = () => {
      state.availOpen = state.availOpen === b.dataset.avail ? null : b.dataset.avail; draw(search.value); }; });
    const ed = rows.querySelector('.avail-row');
    if (ed) wireAvail(ed, state.availOpen, () => draw(search.value));
    renderSavebar();
  };
  const search = document.getElementById('tsearch');
  draw('');
  search.addEventListener('input', (e) => draw(e.target.value));
}

function reviewJoint(body) {
  const j = state.ws.joint_lessons;
  body.innerHTML = j.length ? `
    <p class="muted" style="margin-top:0">These rows had the same subject and the same teachers in several sections of one grade, so the sections are scheduled together.</p>
    <div class="card" style="overflow:auto"><table class="list-table"><thead><tr><th>Subject</th><th>Sections together</th><th>Teachers (all present)</th><th>Periods</th></tr></thead><tbody>
    ${j.map((l) => `<tr><td><span class="subj-dot" style="background:${subjColor(l.subject).dot}"></span><b>${esc(l.subject)}</b></td>
      <td>${l.classes.map((c) => `<span class="badge accent">${esc(c)}</span>`).join(' ')}</td>
      <td class="muted" style="font-size:13px">${esc(l.teachers.map(title).join(', '))}</td><td class="mono">${l.periods_per_week}</td></tr>`).join('')}
    </tbody></table></div>` : '<div class="empty"><h2>No joint lessons</h2><p>Every lesson belongs to a single class.</p></div>';
}

function reviewSettings(body) {
  const q = state.qDraft;
  const r = clone(state.rules);
  const ALL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const days = new Set(q.calendar.days);
  r.teacher_rules = r.teacher_rules || {};
  r.class_rules = r.class_rules || {};
  r.solver = r.solver || {};
  const num = (id, label, value, min, max, hint = '') => `<label class="field">${label}
    <input class="input mono" type="number" id="${id}" value="${value ?? ''}" min="${min}" max="${max}">${hint ? `<span class="muted" style="font-weight:400">${hint}</span>` : ''}</label>`;
  body.innerHTML = `
    <div class="settings-grid">
      <div class="card pad"><h3>School week</h3><div class="stack">
        <div class="field">Teaching days<div class="days">${ALL.map((d) => `<button type="button" class="day-toggle ${days.has(d) ? 'on' : ''}" data-day="${d}">${d.slice(0, 3)}</button>`).join('')}</div></div>
        ${num('ppd', 'Periods per day', q.calendar.periods_per_day, 1, 14)}
        <label class="switch"><input type="checkbox" id="fillct" ${q.fill_remaining_with_class_teacher !== false ? 'checked' : ''}> Fill gaps with class-teacher periods</label>
        <label class="switch"><input type="checkbox" id="ctfirst" ${q.class_teacher_first_period !== false ? 'checked' : ''}> Class teacher takes period 1 every day</label>
      </div></div>
      <div class="card pad"><h3>Teachers</h3><div class="stack">
        ${num('tmax', 'Most periods in a day (hard)', r.teacher_rules.max_periods_per_day, 1, 14)}
        ${num('tsoft', 'Preferred most periods in a day', r.teacher_rules.soft_max_periods_per_day, 1, 14, 'The solver tries to stay under this')}
        ${num('trun', 'Preferred most periods in a row', r.teacher_rules.max_consecutive_periods, 1, 14)}
      </div></div>
      <div class="card pad"><h3>Subjects & labs</h3><div class="stack">
        ${num('subday', 'Most periods of one subject per day', q.max_periods_per_day_per_lesson ?? 2, 1, 6)}
        <label class="switch"><input type="checkbox" id="labdbl" ${r.class_rules.lab_practicals_as_double_period ? 'checked' : ''}> Lab practicals as double periods</label>
        ${Object.entries(q.labs || {}).map(([name, spec], i) => num(`lab${i}`, `${title(name)}s available`, spec.count, 0, 50, esc(spec.subjects.join(', ')))).join('')}
      </div></div>
      <div class="card pad"><h3>Generation</h3><div class="stack">
        ${num('secs', 'Default thinking time (seconds)', r.solver.time_limit_seconds ?? 60, 5, 600, 'A first valid timetable usually appears in seconds; extra time polishes it')}
      </div></div>
    </div>
    <div class="next-cta" style="justify-content:flex-start;margin-top:16px"><button class="btn primary" id="save-settings">Save settings</button></div>`;

  body.querySelectorAll('.day-toggle').forEach((b) => b.addEventListener('click', () => b.classList.toggle('on')));
  document.getElementById('save-settings').addEventListener('click', async (e) => {
    const v = (id) => { const el = document.getElementById(id); return el && el.value !== '' ? parseInt(el.value, 10) : null; };
    const nq = clone(state.qDraft);
    nq.calendar.days = ALL.filter((d) => body.querySelector(`.day-toggle[data-day="${d}"]`).classList.contains('on'));
    if (!nq.calendar.days.length) { toast('Pick at least one teaching day', 'bad'); return; }
    nq.calendar.periods_per_day = v('ppd');
    nq.fill_remaining_with_class_teacher = document.getElementById('fillct').checked;
    nq.class_teacher_first_period = document.getElementById('ctfirst').checked;
    nq.max_periods_per_day_per_lesson = v('subday') || 2;
    Object.keys(nq.labs || {}).forEach((k, i) => { const n = v(`lab${i}`); if (n != null) nq.labs[k].count = n; });
    r.teacher_rules.max_periods_per_day = v('tmax') || nq.calendar.periods_per_day;
    r.teacher_rules.soft_max_periods_per_day = v('tsoft');
    r.teacher_rules.max_consecutive_periods = v('trun');
    r.class_rules.max_periods_per_subject_per_day = nq.max_periods_per_day_per_lesson;
    r.class_rules.lab_practicals_as_double_period = document.getElementById('labdbl').checked;
    r.solver.time_limit_seconds = v('secs') || 60;
    e.target.disabled = true;
    try {
      await putJSON(`/workspaces/${state.wsId}/rules`, r);
      const ws = await putJSON(`/workspaces/${state.wsId}/quotas`, nq);
      Object.assign(state, { rules: r, quotas: clone(nq), qDraft: clone(nq) });
      setSummary(ws);
      toast('Settings saved - data re-checked');
      renderStepper('review');
      renderReview();
    } catch (err) { toast(err.message, 'bad'); e.target.disabled = false; }
  });
}

// ---------------------------------------------------------------- 3. generate
const PHASES = [['checking', 'Checking the data'], ['solving', 'Searching for a clash-free timetable'], ['verifying', 'Independently verifying every rule']];

function renderGenerate() {
  const ws = state.ws;
  const secs = state.rules?.solver?.time_limit_seconds ?? 60;
  const savedJob = store.get(`tts.job.${state.wsId}`, null);
  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="eyebrow">Step 3 · Generate</div>
        <h1>Build the timetable</h1>
        <p>${ws.counts.lessons} lessons across ${ws.counts.scheduled_classes} classes and ${ws.counts.teachers} teachers. The solver finds a valid timetable first, then keeps polishing it, spreading subjects across the week and avoiding long runs for teachers, until time runs out or you stop it.</p>
      </div>
    </div>
    ${ws.errors.length ? `<div class="alert bad"><span class="icon">⛔</span><div><b>The data has problems.</b> <a href="#/w/${state.wsId}/review">Go back and fix them</a> first.</div></div>` : ''}
    <div class="gen-grid">
      <div class="card pad">
        <h3>Thinking time</h3>
        <div class="budget">
          <div class="row"><span class="mono" style="font-size:28px" id="secs-label">${fmtS(secs)}</span><span class="muted">maximum</span></div>
          <input type="range" id="secs" min="15" max="300" step="15" value="${Math.min(300, Math.max(15, secs))}" aria-label="Thinking time in seconds">
          <div class="ticks"><span>15s · quick draft</span><span>5m · most polished</span></div>
        </div>
        <button class="btn primary big" id="go" style="width:100%" ${ws.errors.length ? 'disabled' : ''}>${ICON.spark} Generate timetable</button>
        <button class="btn big hidden" id="stop" style="width:100%">${ICON.stop} Stop and keep the best so far</button>
        <ul class="phases" id="phases" style="margin-top:20px">${PHASES.map(([k, l]) => `<li data-phase="${k}"><span class="ic"></span>${l}</li>`).join('')}</ul>
      </div>
      <div class="card chart-card">
        <div class="row" style="align-items:flex-start">
          <div><h3>Penalty score</h3><p class="muted" style="margin:2px 0 0;font-size:13px">Lower is better. Each dot is a better timetable found.</p></div>
          <span class="spacer"></span>
          <svg class="ring" viewBox="0 0 120 120" id="ring"><circle class="bg" cx="60" cy="60" r="50"/><circle class="fg" cx="60" cy="60" r="50" stroke-dasharray="314.16" stroke-dashoffset="314.16"/><text x="60" y="60" id="ring-t">0s</text></svg>
        </div>
        <svg class="chart" id="chart" viewBox="0 0 600 260" preserveAspectRatio="none" role="img" aria-label="Penalty score over time"></svg>
        <div class="live-stats">
          <div><div class="k">First valid timetable</div><div class="v" id="st-first">–</div></div>
          <div><div class="k">Improvements</div><div class="v" id="st-n">0</div></div>
          <div><div class="k">Current penalty</div><div class="v" id="st-obj">–</div></div>
        </div>
      </div>
    </div>
    <div id="result" style="margin-top:24px"></div>`;

  const range = document.getElementById('secs');
  range.addEventListener('input', () => { document.getElementById('secs-label').textContent = fmtS(+range.value); });
  document.getElementById('go').addEventListener('click', () => startJob(+range.value));
  document.getElementById('stop').addEventListener('click', async () => {
    if (!state.job) return;
    document.getElementById('stop').disabled = true;
    try { await postJSON(`/jobs/${state.job.id}/stop`); } catch (e) { toast(e.message, 'bad'); }
  });
  drawChart([], 1, 0);

  if (savedJob) {
    api(`/jobs/${savedJob}`).then((job) => { state.job = job; updateJob(job); if (['queued', 'running'].includes(job.state)) pollJob(); })
      .catch(() => store.del(`tts.job.${state.wsId}`));
  }
}

async function startJob(seconds) {
  try {
    document.getElementById('result').innerHTML = '';
    const job = await postJSON(`/workspaces/${state.wsId}/solve`, { seconds });
    state.job = job;
    store.set(`tts.job.${state.wsId}`, job.id);
    updateJob(job);
    pollJob();
  } catch (e) { toast(e.message, 'bad'); }
}

function pollJob() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    try {
      const job = await api(`/jobs/${state.job.id}`);
      state.job = job;
      updateJob(job);
      if (!['queued', 'running'].includes(job.state)) {
        clearInterval(state.poll); state.poll = null;
        state.ws = await api(`/workspaces/${state.wsId}`);
        state.tt = null;
        renderStepper('generate');
      }
    } catch (e) { clearInterval(state.poll); state.poll = null; toast('Lost contact with the server', 'bad'); }
  }, 700);
}

function updateJob(job) {
  if (!document.getElementById('phases')) return;
  const running = ['queued', 'running'].includes(job.state);
  document.getElementById('go').classList.toggle('hidden', running);
  const stop = document.getElementById('stop');
  stop.classList.toggle('hidden', !running);
  stop.disabled = job.stop_requested;
  if (job.stop_requested && running) stop.innerHTML = 'Stopping…';

  const order = PHASES.map(([k]) => k);
  const cur = order.indexOf(job.phase);
  document.querySelectorAll('#phases li').forEach((li, i) => {
    li.className = '';
    const ic = li.querySelector('.ic');
    ic.textContent = '';
    if (job.state === 'done' || i < cur) { li.className = 'ok'; ic.textContent = '✓'; }
    else if (i === cur && running) li.className = 'now';
    else if (i === cur && !running) { li.className = job.state === 'done' ? 'ok' : 'fail'; ic.textContent = job.state === 'done' ? '✓' : '!'; }
  });

  const elapsed = running ? job.elapsed : (job.result?.duration ?? job.elapsed);
  const frac = Math.min(1, elapsed / job.seconds);
  document.querySelector('#ring .fg').setAttribute('stroke-dashoffset', String(314.16 * (1 - (running ? frac : 1))));
  document.getElementById('ring-t').textContent = fmtS(elapsed);
  const tr = job.trace;
  document.getElementById('st-first').textContent = tr.length ? fmtS(tr[0][0]) : running ? '…' : '–';
  document.getElementById('st-n').textContent = String(tr.length);
  document.getElementById('st-obj').textContent = tr.length ? Math.round(tr[tr.length - 1][1]).toLocaleString() : '–';
  drawChart(tr, job.seconds, elapsed);
  if (!running) renderResult(job);
}

function drawChart(trace, seconds, elapsed) {
  const svg = document.getElementById('chart');
  if (!svg) return;
  const W = 600, H = 260, L = 46, R = 12, T = 12, B = 26;
  const xMax = Math.max(seconds, elapsed, 1);
  const ys = trace.map((p) => p[1]);
  let yMin = ys.length ? Math.min(...ys) : 0, yMax = ys.length ? Math.max(...ys) : 1;
  if (yMax === yMin) { yMax += 1; yMin = Math.max(0, yMin - 1); }
  const pad = (yMax - yMin) * 0.08;
  yMin = Math.max(0, yMin - pad); yMax += pad;
  const x = (t) => L + (t / xMax) * (W - L - R);
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);
  const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => yMin + f * (yMax - yMin));
  const ticksX = [0, 0.25, 0.5, 0.75, 1].map((f) => f * xMax);
  let path = '', area = '';
  if (trace.length) {
    const pts = [];
    trace.forEach(([t, v], i) => { if (i) pts.push([t, trace[i - 1][1]]); pts.push([t, v]); });
    pts.push([Math.max(elapsed, trace[trace.length - 1][0]), trace[trace.length - 1][1]]);
    path = pts.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    area = `${path} L${x(pts[pts.length - 1][0]).toFixed(1)},${H - B} L${x(pts[0][0]).toFixed(1)},${H - B} Z`;
  }
  svg.innerHTML = `
    <g class="grid">${ticksY.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join('')}</g>
    <g class="axis">${ticksY.map((v) => `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${trace.length ? Math.round(v) : ''}</text>`).join('')}
      ${ticksX.map((t) => `<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${Math.round(t)}s</text>`).join('')}</g>
    ${trace.length ? `<path class="area" d="${area}"/><path class="line" d="${path}"/>
      ${trace.map(([t, v]) => `<circle class="dot" cx="${x(t).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.6"/>`).join('')}` :
      `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="var(--ink-3)" font-size="13">Waiting for the first timetable…</text>`}`;
}

function renderResult(job) {
  const box = document.getElementById('result');
  if (!box || box.dataset.job === job.id + job.state) return;
  box.dataset.job = job.id + job.state;
  const r = job.result;
  if (job.state === 'error') {
    box.innerHTML = `<div class="alert bad"><span class="icon">⛔</span><div><b>Something went wrong.</b><ul><li>${esc(job.error)}</li></ul></div></div>`;
    return;
  }
  if (!r) return;
  if (!r.success) {
    const reasons = r.status === 'INFEASIBLE' ? r.explanation : r.violations;
    box.innerHTML = `<div class="alert bad fade-in"><span class="icon">⛔</span><div>
      <b>${r.status === 'INVALID_INPUT' ? 'The data has problems' : r.status === 'INFEASIBLE' ? 'No timetable can satisfy all the rules together' : r.status === 'UNKNOWN' ? 'No timetable found in the time given' : 'Generation failed'}</b>
      ${r.status === 'INFEASIBLE' ? '<p style="margin:6px 0 0">These rules clash with each other. Relax one of them (change a quota, a teacher, or a setting) and try again:</p>' : ''}
      ${r.status === 'UNKNOWN' ? '<p style="margin:6px 0 0">Try giving it more thinking time.</p>' : ''}
      <ul>${(reasons || []).filter((v) => v !== 'No timetable satisfies all the rules.').map((v) => `<li>${esc(v)}</li>`).join('')}</ul>
      <div class="row" style="margin-top:10px"><a class="btn" href="#/w/${state.wsId}/review">Back to review</a></div></div></div>`;
    return;
  }
  const q = r.quality || {};
  const rules = state.rules || {};
  box.innerHTML = `<div class="card pad fade-in">
    <div class="row"><span class="badge good" style="font-size:13px;padding:4px 10px">✓ Verified</span>
      <h2 style="font-size:22px">${r.stopped_early ? 'Stopped early with a valid timetable' : 'Timetable ready'}</h2><span class="spacer"></span>
      <a class="btn" href="${API}/workspaces/${state.wsId}/export.xlsx">${ICON.download} Excel</a>
      <a class="btn primary" href="#/w/${state.wsId}/timetable">View timetable ${ICON.arrow}</a></div>
    <ul class="checks">
      <li>No teacher, class or room is ever double-booked</li>
      <li>Every lesson has exactly its periods per week, with all of its teachers present</li>
      <li>Joint lessons are taught to all their sections at the same time</li>
      ${state.quotas?.class_teacher_first_period !== false ? '<li>The class teacher takes period 1 every day</li>' : ''}
      ${rules.class_rules?.lab_practicals_as_double_period ? '<li>Lab practicals are back-to-back double periods</li>' : ''}
    </ul>
    <div class="quality">
      ${qual('Free class periods', q.free_class_periods)}
      ${qual('Subject twice in a day', q.same_subject_twice_a_day)}
      ${qual(`Teacher-days over ${rules.teacher_rules?.max_consecutive_periods ?? '–'} in a row`, q.teacher_days_over_max_run)}
      ${qual(`Main subjects after P${rules.class_rules?.heavy_subjects_not_after_period ?? '–'}`, q.heavy_subject_periods_late)}
      ${qual('Busiest teacher', q.busiest_teacher_week != null ? `${q.busiest_teacher_week}/wk` : '–')}
    </div></div>`;
}
const qual = (k, v) => `<div><div class="k">${esc(k)}</div><div class="v">${esc(v ?? '–')}</div></div>`;

// ---------------------------------------------------------------- 4. timetable
async function renderTimetable() {
  if (!state.tt) state.tt = await api(`/workspaces/${state.wsId}/timetable`);
  const T = state.tt;
  if (!T.timetable || !T.timetable.success) {
    view.innerHTML = `<div class="empty"><h2>No timetable yet</h2><p>Generate one first.</p><a class="btn primary" href="#/w/${state.wsId}/generate">Go to generate</a></div>`;
    return;
  }
  indexTimetable(T);
  const list = entities();
  if (!state.ttEntity || !list.some((e) => e.id === state.ttEntity)) state.ttEntity = list[0]?.id;

  view.innerHTML = `
    <div class="tt-layout">
      <aside class="card sidebar no-print">
        <div class="head">
          <div class="seg" role="tablist">
            ${['class', 'teacher', 'overview'].map((m) => `<button class="${state.ttMode === m ? 'active' : ''}" data-mode="${m}">${m === 'class' ? 'Classes' : m === 'teacher' ? 'Teachers' : 'Overview'}</button>`).join('')}
          </div>
          ${state.ttMode !== 'overview' ? `<input class="input" id="search" placeholder="Search ${state.ttMode === 'class' ? 'classes' : 'teachers'}" value="${esc(state.ttSearch)}">` : ''}
        </div>
        <div class="entity-list" id="elist"></div>
      </aside>
      <section id="tt-main"></section>
    </div>`;
  view.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => {
    state.ttMode = b.dataset.mode; state.ttEntity = null; state.ttSearch = ''; state.focus = null; renderTimetable();
  }));
  const search = document.getElementById('search');
  if (search) search.addEventListener('input', () => { state.ttSearch = search.value; drawEntityList(); });
  drawEntityList();
  drawMain();
}

function indexTimetable(T) {
  if (T._idx) return;
  const subj = Object.fromEntries(T.subjects.map((s) => [s.id, s.name]));
  const teachers = Object.fromEntries(T.teachers.map((t) => [t.id, t]));
  const classes = Object.fromEntries(T.classes.map((c) => [c.id, c]));
  const rooms = Object.fromEntries(T.rooms.map((r) => [r.id, r]));
  const byLessonSlot = {};
  for (const a of T.timetable.assignments) {
    const k = `${a.lesson_id}|${a.day}|${a.period}`;
    const e = byLessonSlot[k] || (byLessonSlot[k] = { lesson: a.lesson_id, subject: subj[a.subject_id] || a.subject_id, day: a.day, period: a.period, classes: new Set(), teachers: new Set(), rooms: new Set() });
    e.classes.add(a.class_id); if (a.teacher_id) e.teachers.add(a.teacher_id); if (a.room_id) e.rooms.add(a.room_id);
  }
  const byClass = {}, byTeacher = {}, loads = {};
  for (const e of Object.values(byLessonSlot)) {
    for (const c of e.classes) ((byClass[c] ||= {})[`${e.day}|${e.period}`] ||= []).push(e);
    for (const t of e.teachers) {
      ((byTeacher[t] ||= {})[`${e.day}|${e.period}`] ||= []).push(e);
      if (!e.lesson.startsWith('HOME_')) { (loads[t] ||= {})[e.day] = (loads[t][e.day] || 0) + 1; }
    }
  }
  T._idx = { subj, teachers, classes, rooms, byClass, byTeacher, loads };
}

function entities() {
  const { _idx: I } = state.tt;
  if (state.ttMode === 'class') {
    return state.tt.classes.map((c) => ({ id: c.id, name: c.name, group: c.grade <= 0 ? 'Pre-primary' : `Grade ${c.grade}`, sort: [c.grade, c.section] }))
      .sort((a, b) => a.sort[0] - b.sort[0] || String(a.sort[1]).localeCompare(b.sort[1]));
  }
  if (state.ttMode === 'teacher') {
    return Object.keys(I.byTeacher).map((id) => ({ id, name: title(I.teachers[id]?.name || id), group: (I.teachers[id]?.name || '?')[0].toUpperCase(),
      meta: Object.values(I.loads[id] || {}).reduce((a, b) => a + b, 0) })).sort((a, b) => a.name.localeCompare(b.name));
  }
  return [];
}

function drawEntityList() {
  const el = document.getElementById('elist');
  if (state.ttMode === 'overview') {
    el.innerHTML = '<p class="muted" style="padding:10px;font-size:13px;margin:0">Teacher workload across the week. Click a name to open their timetable.</p>';
    return;
  }
  const q = state.ttSearch.toLowerCase();
  let last = null, html = '';
  for (const e of entities().filter((x) => !q || x.name.toLowerCase().includes(q))) {
    if (e.group !== last) { html += `<div class="group">${esc(e.group)}</div>`; last = e.group; }
    html += `<button class="entity ${e.id === state.ttEntity ? 'active' : ''}" data-id="${esc(e.id)}"><span>${esc(e.name)}</span>${e.meta != null ? `<span class="muted">${e.meta}</span>` : ''}</button>`;
  }
  el.innerHTML = html || '<p class="muted" style="padding:10px">No matches</p>';
  el.querySelectorAll('.entity').forEach((b) => b.addEventListener('click', () => {
    state.ttEntity = b.dataset.id; state.focus = null;
    el.querySelectorAll('.entity').forEach((x) => x.classList.toggle('active', x === b));
    drawMain();
  }));
}

function cellsFor(mode, id) {
  const I = state.tt._idx;
  return (mode === 'class' ? I.byClass : I.byTeacher)[id] || {};
}

function gridHTML(mode, id, { interactive = true } = {}) {
  const T = state.tt, I = T._idx;
  const cells = cellsFor(mode, id);
  const P = T.periods_per_day;
  let html = `<div class="tt-grid ${state.focus && interactive ? 'focus' : ''} ${state.swap && interactive && mode === 'class' ? 'swapping' : ''}" style="grid-template-columns:84px repeat(${P}, minmax(0,1fr))"><div></div>`;
  for (let p = 1; p <= P; p++) html += `<div class="h">P${p}</div>`;
  for (const d of T.days) {
    html += `<div class="d">${esc(d.slice(0, 3))}</div>`;
    for (let p = 1; p <= P; p++) {
      const list = cells[`${d}|${p}`] || [];
      const mark = interactive ? cellMark(mode, id, d, p) : '';
      if (!list.length) { html += `<div class="cell free ${mark}" data-day="${esc(d)}" data-period="${p}">free</div>`; continue; }
      const e = list[0];
      const joint = e.classes.size > 1;
      const lab = [...e.rooms].map((r) => I.rooms[r]).find((r) => r?.is_lab);
      const who = mode === 'class'
        ? [...e.teachers].map((t) => title(I.teachers[t]?.name || t)).join(', ')
        : [...e.classes].map((c) => I.classes[c]?.name || c).join(' + ');
      const tip = `${e.subject} · ${[...e.classes].map((c) => I.classes[c]?.name).join(' + ')} · ${[...e.teachers].map((t) => title(I.teachers[t]?.name)).join(', ')}${lab ? ` · ${lab.name}` : ''}`;
      html += `<div class="cell ${state.focus === e.subject ? 'lit' : ''} ${mark}" style="${colorVars(e.subject)}" title="${esc(tip)}" data-subject="${esc(e.subject)}" data-day="${esc(d)}" data-period="${p}">
        <div class="tags">${joint && mode === 'class' ? `<span class="tag">+${e.classes.size - 1}</span>` : ''}${lab ? '<span class="tag">LAB</span>' : ''}${list.length > 1 ? '<span class="tag" style="color:var(--bad)">!</span>' : ''}</div>
        <div class="s">${esc(e.subject)}</div><div class="w">${esc(who)}</div></div>`;
    }
  }
  return html + '</div>';
}

function drawMain() {
  const main = document.getElementById('tt-main');
  const T = state.tt, I = T._idx, meta = T.timetable.metadata || {};
  if (state.ttMode === 'overview') { drawOverview(main); return; }
  const id = state.ttEntity;
  if (!id) { main.innerHTML = '<div class="card empty">Nothing to show.</div>'; return; }
  let name, sub;
  if (state.ttMode === 'class') {
    const c = I.classes[id];
    name = `Class ${c.name}`;
    sub = `Class teacher ${title(I.teachers[c.class_teacher_id]?.name || '–')}${c.self_contained ? ' · self-contained' : ''}`;
  } else {
    const t = I.teachers[id];
    const load = Object.values(I.loads[id] || {}).reduce((a, b) => a + b, 0);
    name = title(t?.name || id);
    sub = `${load} periods a week`;
  }
  const counts = {};
  for (const list of Object.values(cellsFor(state.ttMode, id))) counts[list[0].subject] = (counts[list[0].subject] || 0) + 1;
  main.innerHTML = `
    <div class="tt-head">
      <div><h2>${esc(name)}</h2><div class="sub">${esc(sub)} · <span class="badge good">✓ verified</span>${T.timetable.edited ? ' <span class="badge accent">edited</span>' : ''} <span class="muted">${T.timetable.edited ? 'updated' : 'generated'} ${esc(new Date((T.timetable.generated_at || 0) * 1000).toLocaleString())}</span></div></div>
      <div class="row tt-toolbar no-print">
        <button class="btn primary" id="open-chat">${ICON.chat} Assistant</button>
        ${state.ttMode === 'class' ? `<button class="btn ${state.swap ? 'on' : ''}" id="swap-mode" aria-pressed="${state.swap}">${ICON.swap} ${state.swap ? 'Pick two periods…' : 'Swap'}</button>` : ''}
        ${T.undo_available ? `<button class="btn" id="undo">${ICON.undo} Undo</button>` : ''}
        <a class="btn" href="${API}/workspaces/${state.wsId}/export.xlsx">${ICON.download} Excel</a>
        <button class="btn" id="print-one">${ICON.print} Print</button>
        <button class="btn" id="print-all">${ICON.print} Print all ${state.ttMode === 'class' ? 'classes' : 'teachers'}</button>
        <a class="btn ghost" href="#/w/${state.wsId}/generate">Regenerate</a>
      </div>
    </div>
    <div class="card grid-wrap" id="gridbox">${gridHTML(state.ttMode, id)}</div>
    <div class="legend no-print">${Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([s, n]) => {
      const c = subjColor(s);
      return `<button class="${state.focus === s ? 'active' : ''}" data-s="${esc(s)}"><span class="subj-dot" style="background:${c.dot};margin:0"></span>${esc(s)} <span class="n">${n}</span></button>`;
    }).join('')}</div>
    <p class="muted no-print" style="font-size:13px;margin-top:14px">Solved in ${fmtS(meta.solve_duration_seconds)} · ${esc((meta.quality || {}).same_subject_twice_a_day ?? '–')} same-day doubles school-wide · Click a subject to highlight it.</p>`;

  main.querySelectorAll('.legend button').forEach((b) => b.addEventListener('click', () => {
    state.focus = state.focus === b.dataset.s ? null : b.dataset.s; drawMain();
  }));
  main.querySelectorAll('.cell[data-day]').forEach((c) => c.addEventListener('click', () => {
    if (state.swap && state.ttMode === 'class') { pickSwap(c.dataset.day, +c.dataset.period); return; }
    if (!c.dataset.subject) return;
    state.focus = state.focus === c.dataset.subject ? null : c.dataset.subject; drawMain();
  }));
  document.getElementById('open-chat').addEventListener('click', () => openChat(true));
  document.getElementById('swap-mode')?.addEventListener('click', () => {
    state.swap = !state.swap; state.swapPick = null; state.focus = null;
    if (state.swap) toast('Click two periods of this class to swap them');
    drawMain();
  });
  document.getElementById('undo')?.addEventListener('click', undoEdit);
  document.getElementById('print-one').addEventListener('click', () => { const f = state.focus; state.focus = null; drawMain(); window.print(); state.focus = f; });
  document.getElementById('print-all').addEventListener('click', printAll);
}

function drawOverview(main) {
  const T = state.tt, I = T._idx;
  const P = T.periods_per_day;
  const teachers = Object.keys(I.loads).map((id) => ({ id, name: title(I.teachers[id]?.name || id), days: I.loads[id], total: Object.values(I.loads[id]).reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.total - a.total);
  const q = T.timetable.metadata?.quality || {};
  const shade = (n) => {
    const f = n / P;
    return isDark() ? `background:hsl(158 ${20 + f * 40}% ${14 + f * 26}%);color:${f > .6 ? '#fff' : 'var(--ink-2)'}`
      : `background:hsl(158 ${25 + f * 35}% ${96 - f * 50}%);color:${f > .6 ? '#fff' : 'var(--ink-2)'}`;
  };
  main.innerHTML = `
    <div class="tt-head"><div><h2>Week at a glance</h2><div class="sub">${teachers.length} teachers · busiest ${q.busiest_teacher_week ?? '–'} periods a week</div></div>
      <div class="row tt-toolbar no-print"><a class="btn" href="${API}/workspaces/${state.wsId}/export.xlsx">${ICON.download} Excel</a></div></div>
    <div class="quality" style="margin:0 0 16px">
      ${qual('Free class periods', q.free_class_periods)}${qual('Subject twice in a day', q.same_subject_twice_a_day)}
      ${qual('Teacher idle gaps', q.teacher_idle_gaps)}${qual('Longest teaching run', q.longest_teacher_run)}
    </div>
    <div class="card pad" style="overflow-x:auto">
      <div class="heat" style="grid-template-columns:minmax(140px,1.4fr) repeat(${T.days.length}, minmax(36px,1fr)) 56px;min-width:520px">
        <div></div>${T.days.map((d) => `<div class="h muted mono" style="text-align:center;font-size:11px">${esc(d.slice(0, 3))}</div>`).join('')}<div class="muted mono" style="text-align:right;font-size:11px">week</div>
        ${teachers.map((t) => `<button class="hn entity" data-id="${esc(t.id)}" style="padding:0 8px 0 0;font-size:12.5px">${esc(t.name)}</button>
          ${T.days.map((d) => `<div class="hc" style="${shade(t.days[d] || 0)}">${t.days[d] || ''}</div>`).join('')}
          <div class="mono" style="text-align:right;font-size:12px;align-self:center">${t.total}</div>`).join('')}
      </div>
    </div>`;
  main.querySelectorAll('.hn').forEach((b) => b.addEventListener('click', () => {
    state.ttMode = 'teacher'; state.ttEntity = b.dataset.id; renderTimetable();
  }));
}

function printAll() {
  const area = document.getElementById('print-area');
  const I = state.tt._idx;
  const list = entities();
  const f = state.focus; state.focus = null;
  area.innerHTML = list.map((e) => {
    const heading = state.ttMode === 'class' ? `Class ${esc(e.name)} <span style="font-size:11pt;font-weight:400">· Class teacher ${esc(title(I.teachers[I.classes[e.id]?.class_teacher_id]?.name || ''))}</span>` : esc(e.name);
    return `<div class="print-page"><h2>${heading}</h2>${gridHTML(state.ttMode, e.id, { interactive: false })}</div>`;
  }).join('');
  state.focus = f;
  document.body.classList.add('printing-all');
  const done = () => { document.body.classList.remove('printing-all'); area.innerHTML = ''; window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
}

// ---------------------------------------------------------------- setup: labs, availability, per-class periods
// All three edit the quotas draft (state.qDraft) and are saved with the same "Save & re-check" bar.

const jsonEq = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
function diffKeys(a, b) {
  a = a || {}; b = b || {};
  let n = 0;
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) if (!jsonEq(a[k], b[k])) n++;
  return n;
}
function extraChanges() {
  const d = state.qDraft, s = state.quotas;
  let n = diffKeys(d.labs, s.labs) + diffKeys(d.teacher_unavailable, s.teacher_unavailable);
  const co = d.class_overrides || {}, so = s.class_overrides || {};
  for (const c of new Set([...Object.keys(co), ...Object.keys(so)])) n += diffKeys(co[c], so[c]);
  return n;
}
const slotSort = (a, b) => state.ws.calendar.days.indexOf(a.day) - state.ws.calendar.days.indexOf(b.day) || a.period - b.period;

// --- labs
function reviewLabs(body) {
  const q = state.qDraft;
  q.labs = q.labs || {};
  const usage = state.ws.lab_usage || {};
  const slots = state.ws.calendar.slots;
  const taken = new Set(Object.values(q.labs).flatMap((l) => l.subjects || []));
  const free = (state.ws.all_subjects || []).filter((s) => !taken.has(s));
  body.innerHTML = `
    <p class="muted" style="margin-top:0">Practical subjects are booked into a lab room. A lab can hold one class at a time, so the number of rooms limits how many practicals can run in the same period.</p>
    <div class="lab-grid">
      ${Object.entries(q.labs).map(([name, spec]) => {
        const u = usage[name];
        const cap = (spec.count || 0) * slots;
        const pct = u && cap ? u.needed / cap : u?.needed ? 2 : 0;
        return `<div class="card pad lab-card" data-lab="${esc(name)}">
          <div class="row" style="justify-content:space-between"><h3>${esc(title(name))}</h3><button class="btn ghost danger" data-del-lab>Remove</button></div>
          <div class="row" style="margin:10px 0"><span>Rooms</span>
            <span class="stepper-input ${spec.count !== state.quotas.labs?.[name]?.count ? 'changed' : ''}">
              <button type="button" data-lab-d="-" aria-label="Fewer ${esc(name)} rooms">−</button>
              <input type="number" min="0" max="50" value="${spec.count ?? 0}" aria-label="Number of ${esc(name)} rooms" data-lab-count>
              <button type="button" data-lab-d="+" aria-label="More ${esc(name)} rooms">+</button></span></div>
          <div class="field" style="margin-bottom:8px">Subjects taught here</div>
          <div class="chips">${(spec.subjects || []).map((s) => `<span class="badge accent chip-x">${esc(s)}<button type="button" data-rm="${esc(s)}" aria-label="Remove ${esc(s)}">×</button></span>`).join('') || '<span class="muted">None yet</span>'}</div>
          <select class="input" data-add style="margin-top:10px"><option value="">+ Add a subject…</option>${free.map((s) => `<option>${esc(s)}</option>`).join('')}</select>
          ${u ? `<div class="load" style="margin-top:12px"><span class="mono">${u.needed}/${cap}</span><span class="bar"><i class="${pct > 1 ? 'over' : pct > .85 ? 'hot' : ''}" style="width:${Math.min(100, pct * 100)}%"></i></span></div>
            <div class="muted" style="font-size:12.5px">lab periods needed per week / available (as last saved)</div>` : '<div class="muted" style="font-size:12.5px;margin-top:10px">Save to see how busy it is.</div>'}
        </div>`;
      }).join('')}
      <div class="card pad lab-card new-lab"><h3>Add a lab</h3>
        <div class="row" style="margin-top:10px;flex-wrap:nowrap"><input class="input" id="new-lab" placeholder="e.g. PHYSICS LAB"><button class="btn" id="add-lab">Add</button></div></div>
    </div>
    <div id="savebar-slot"></div>`;
  const redraw = () => { reviewLabs(body); };
  body.querySelectorAll('.lab-card[data-lab]').forEach((card) => {
    const name = card.dataset.lab, spec = q.labs[name];
    const setCount = (n) => { spec.count = Math.max(0, Math.min(50, Math.round(+n || 0))); redraw(); };
    card.querySelector('[data-lab-d="-"]').onclick = () => setCount(spec.count - 1);
    card.querySelector('[data-lab-d="+"]').onclick = () => setCount(spec.count + 1);
    card.querySelector('[data-lab-count]').onchange = (e) => setCount(e.target.value);
    card.querySelectorAll('[data-rm]').forEach((b) => { b.onclick = () => { spec.subjects = spec.subjects.filter((s) => s !== b.dataset.rm); redraw(); }; });
    card.querySelector('[data-add]').onchange = (e) => { if (e.target.value) { spec.subjects = [...(spec.subjects || []), e.target.value]; redraw(); } };
    card.querySelector('[data-del-lab]').onclick = () => { delete q.labs[name]; redraw(); };
  });
  document.getElementById('add-lab').onclick = () => {
    const name = document.getElementById('new-lab').value.trim().toUpperCase();
    if (!name) return;
    if (q.labs[name]) { toast('That lab already exists', 'bad'); return; }
    q.labs[name] = { count: 1, subjects: [] }; redraw();
  };
  renderSavebar();
}

// --- teacher availability
function availSummary(list) {
  if (!list?.length) return '';
  const P = state.ws.calendar.periods_per_day;
  const byDay = {};
  for (const u of list) (byDay[u.day] ||= []).push(u.period);
  return state.ws.calendar.days.filter((d) => byDay[d]).map((d) => {
    const ps = byDay[d].sort((a, b) => a - b);
    if (ps.length === P) return `${d.slice(0, 3)} (all day)`;
    return `${d.slice(0, 3)} P${ps.join(',')}`;
  }).join(' · ');
}

function availEditor(name) {
  const { days, periods_per_day: P } = state.ws.calendar;
  const off = new Set((state.qDraft.teacher_unavailable?.[name] || []).map((u) => `${u.day}|${u.period}`));
  let h = `<div class="avail" style="grid-template-columns:56px repeat(${P}, minmax(30px,1fr))"><div></div>`;
  for (let p = 1; p <= P; p++) h += `<button type="button" class="ah" data-col="${p}" title="Toggle period ${p} on every day">P${p}</button>`;
  for (const d of days) {
    h += `<button type="button" class="ad" data-row="${esc(d)}" title="Toggle the whole day">${esc(d.slice(0, 3))}</button>`;
    for (let p = 1; p <= P; p++) {
      const isOff = off.has(`${d}|${p}`);
      h += `<button type="button" class="ac ${isOff ? 'off' : ''}" data-day="${esc(d)}" data-p="${p}" aria-pressed="${isOff}" aria-label="${esc(d)} period ${p}: ${isOff ? 'unavailable' : 'available'}">${isOff ? '✕' : ''}</button>`;
    }
  }
  return `${h}</div>
    <div class="row" style="margin-top:10px;justify-content:space-between">
      <span class="muted" style="font-size:13px">Click the periods this teacher <b>can't</b> teach, every week. Click a day or a period number to toggle the whole row or column.</span>
      <span class="row"><button type="button" class="btn ghost" data-clear>Clear</button><button type="button" class="btn" data-done>Done</button></span></div>`;
}

function setAvail(name, list) {
  const q = state.qDraft;
  q.teacher_unavailable = q.teacher_unavailable || {};
  if (list.length) q.teacher_unavailable[name] = list.sort(slotSort);
  else delete q.teacher_unavailable[name];
}

function wireAvail(container, name, redraw) {
  const { days, periods_per_day: P } = state.ws.calendar;
  const cur = () => new Set((state.qDraft.teacher_unavailable?.[name] || []).map((u) => `${u.day}|${u.period}`));
  const save = (set) => { setAvail(name, [...set].map((k) => { const [day, p] = k.split('|'); return { day, period: +p }; })); redraw(); };
  container.querySelectorAll('.ac').forEach((b) => { b.onclick = () => { const s = cur(), k = `${b.dataset.day}|${b.dataset.p}`; s.has(k) ? s.delete(k) : s.add(k); save(s); }; });
  container.querySelectorAll('.ad').forEach((b) => { b.onclick = () => {
    const s = cur(), keys = Array.from({ length: P }, (_, i) => `${b.dataset.row}|${i + 1}`);
    const all = keys.every((k) => s.has(k)); keys.forEach((k) => (all ? s.delete(k) : s.add(k))); save(s); }; });
  container.querySelectorAll('.ah').forEach((b) => { b.onclick = () => {
    const s = cur(), keys = days.map((d) => `${d}|${b.dataset.col}`);
    const all = keys.every((k) => s.has(k)); keys.forEach((k) => (all ? s.delete(k) : s.add(k))); save(s); }; });
  container.querySelector('[data-clear]').onclick = () => save(new Set());
  container.querySelector('[data-done]').onclick = () => { state.availOpen = null; redraw(); };
}

// --- per-class periods
function classEditor(c) {
  const slots = state.ws.calendar.slots;
  const overrides = state.qDraft.class_overrides?.[c.name] || {};
  const rows = c.subjects.filter((e) => e.subject !== 'CT ACTIVITY').map((e) => {
    const grade = quotaValue(state.qDraft, c.band, e.subject);
    const v = e.subject in overrides ? overrides[e.subject] : grade;
    return { ...e, grade, v, over: e.subject in overrides };
  });
  const total = rows.reduce((a, r) => a + (r.v || 0) * r.rows, 0);
  return `<div class="card pad class-editor">
    <div class="row" style="justify-content:space-between">
      <div><h3>Class ${esc(c.name)}</h3><div class="muted" style="font-size:13px">Change a number here to differ from the rest of grades ${esc(c.band)}. Other classes are not affected.</div></div>
      <div class="row"><span class="badge ${total > slots ? 'bad' : total === slots ? 'good' : 'warn'}">${total}/${slots} periods${total < slots && state.qDraft.fill_remaining_with_class_teacher !== false ? ` · ${slots - total} go to the class teacher` : ''}</span>
      <button class="btn" data-close-class>Done</button></div>
    </div>
    <table class="qtable" style="margin-top:10px"><thead><tr><th>Subject</th><th>Periods / week</th><th></th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="subj"><span class="subj-dot" style="background:${subjColor(r.subject).dot}"></span>${esc(r.subject)}${r.rows > 1 ? ` <span class="muted">×${r.rows} lessons</span>` : ''}</td>
        <td><span class="stepper-input ${r.over ? 'changed' : ''}" data-cs="${esc(r.subject)}">
          <button type="button" data-d="-" aria-label="Fewer periods of ${esc(r.subject)}">−</button>
          <input type="number" min="0" value="${r.v ?? ''}" placeholder="?" aria-label="Periods per week of ${esc(r.subject)} for ${esc(c.name)}">
          <button type="button" data-d="+" aria-label="More periods of ${esc(r.subject)}">+</button></span></td>
        <td>${r.over ? `<span class="badge accent">only ${esc(c.name)}</span> <button class="link" data-reset="${esc(r.subject)}">use grade value (${r.grade ?? '–'})</button>` : `<span class="muted" style="font-size:12.5px">grade value</span>`}</td></tr>`).join('')}
    </tbody></table></div>`;
}

function wireClassEditor(box, c, redraw) {
  const setOverride = (subject, val) => {
    const q = state.qDraft;
    q.class_overrides = q.class_overrides || {};
    const grade = quotaValue(q, c.band, subject);
    const o = (q.class_overrides[c.name] ||= {});
    if (val == null || val === grade) delete o[subject];
    else o[subject] = Math.max(0, Math.min(state.ws.calendar.slots, Math.round(val)));
    if (!Object.keys(o).length) delete q.class_overrides[c.name];
    redraw();
  };
  box.querySelectorAll('[data-cs]').forEach((el) => {
    const subject = el.dataset.cs, input = el.querySelector('input');
    el.querySelector('[data-d="-"]').onclick = () => setOverride(subject, (+input.value || 0) - 1);
    el.querySelector('[data-d="+"]').onclick = () => setOverride(subject, (+input.value || 0) + 1);
    input.onchange = () => setOverride(subject, input.value === '' ? null : +input.value);
  });
  box.querySelectorAll('[data-reset]').forEach((b) => { b.onclick = () => setOverride(b.dataset.reset, null); });
  box.querySelector('[data-close-class]').onclick = () => { state.classOpen = null; redraw(); };
}

// ---------------------------------------------------------------- edits: swap mode, assistant, undo
// The assistant and the swap button never change the timetable themselves: they ask the
// server for a *proposal* (smallest verified change), shown here with Apply / Discard.

const keyStore = {
  get() { try { return localStorage.getItem('tt.geminiKey') || ''; } catch { return ''; } },
  set(v) { try { v ? localStorage.setItem('tt.geminiKey', v) : localStorage.removeItem('tt.geminiKey'); } catch { /* private mode */ } },
};

function cellMark(mode, id, day, period) {
  const cls = [];
  if (state.swap && mode === 'class' && state.swapPick && state.swapPick.day === day && state.swapPick.period === period) cls.push('picked');
  const pr = state.proposal;
  if (pr && mode === 'class') {
    const cname = state.tt._idx.classes[id]?.name;
    for (const ch of pr.diff.changes) {
      if (!ch.classes.includes(cname)) continue;
      if (ch.to.day === day && ch.to.period === period) cls.push('moved-in');
      if (ch.from.day === day && ch.from.period === period) cls.push('moved-out');
    }
  } else if (pr && mode === 'teacher') {
    const tname = state.tt._idx.teachers[id]?.name;
    for (const ch of pr.diff.changes) {
      if (!ch.teachers.includes(tname)) continue;
      if (ch.to.day === day && ch.to.period === period) cls.push('moved-in');
      if (ch.from.day === day && ch.from.period === period) cls.push('moved-out');
    }
  }
  return [...new Set(cls)].join(' ');
}

async function pickSwap(day, period) {
  if (!state.swapPick) { state.swapPick = { day, period }; drawMain(); return; }
  const a = state.swapPick;
  if (a.day === day && a.period === period) { state.swapPick = null; drawMain(); return; }
  const cname = state.tt._idx.classes[state.ttEntity]?.name;
  state.swapPick = null; state.swap = false;
  drawMain();
  openChat(true);
  pushChat({ role: 'user', text: `Swap ${cname}: ${a.day} P${a.period} ⇄ ${day} P${period}` });
  await runEdit(() => postJSON(`/workspaces/${state.wsId}/edits/propose`, {
    changes: [{ type: 'swap', class: cname, day1: a.day, period1: a.period, day2: day, period2: period }] }));
}

async function runEdit(call) {
  state.chatBusy = true;
  pushChat({ role: 'busy' });
  try {
    const r = await call();
    dropBusy();
    if (r.reply !== undefined) {                     // assistant turn
      if (r.cover) pushChat({ role: 'cover', cover: r.cover });
      if (r.proposal) setProposal(r.proposal);
      if (r.setup) pushChat({ role: 'setup', p: r.setup });
      pushChat({ role: 'assistant', text: r.reply });
      if (r.view && !r.proposal) showEntity(r.view);
    } else if (r.ok) setProposal(r);                // direct swap
    else pushChat({ role: 'error', text: r.message, reasons: r.reasons });
  } catch (e) {
    dropBusy();
    pushChat({ role: 'error', text: e.message });
  } finally {
    state.chatBusy = false;
    drawChat();
  }
}

function setProposal(p) {
  if (state.proposal) {
    const old = state.chat.find((m) => m.role === 'proposal' && m.p.proposal_id === state.proposal.proposal_id);
    if (old && !old.done) old.done = 'replaced';
  }
  state.proposal = p;
  pushChat({ role: 'proposal', p });
  // jump to the first affected class so the highlight is visible
  const first = p.diff.changes[0];
  if (first && state.ttMode === 'class' && state.tt?._idx) {
    const cur = state.tt._idx.classes[state.ttEntity]?.name;
    if (!p.diff.changes.some((c) => c.classes.includes(cur))) showEntity({ kind: 'class', name: first.classes[0] });
  }
  if (parseRoute().page === 'timetable') drawMain();
}

function showEntity(v) {
  if (parseRoute().page !== 'timetable' || !state.tt?._idx) return;
  const I = state.tt._idx;
  const pool = v.kind === 'teacher' ? I.teachers : I.classes;
  const hit = Object.values(pool).find((x) => x.name === v.name);
  if (!hit) return;
  state.ttMode = v.kind === 'teacher' ? 'teacher' : 'class';
  state.ttEntity = hit.id; state.ttSearch = ''; state.focus = null;
  renderTimetable();
}

async function applyProposal(m) {
  try {
    await postJSON(`/workspaces/${state.wsId}/proposals/${m.p.proposal_id}/apply`);
    m.done = 'applied';
    state.proposal = null;
    await reloadTimetable();
    toast('Change applied. You can undo it from the toolbar.', 'good');
  } catch (e) { toast(e.message, 'bad'); m.done = 'expired'; state.proposal = null; drawMain(); }
  drawChat();
}

async function applySetup(m) {
  const dirty = state.qDraft && state.quotas && countChanges() > 0;
  if (dirty && !confirm('You have unsaved changes on the Review page. Applying this replaces them. Continue?')) return;
  try {
    const ws = await postJSON(`/workspaces/${state.wsId}/setup-proposals/${m.p.proposal_id}/apply`);
    m.done = 'applied';
    const quotas = await api(`/workspaces/${state.wsId}/quotas`);
    Object.assign(state, { quotas, qDraft: clone(quotas), tt: null, proposal: null });
    setSummary(ws);
    renderStepper(parseRoute().page);
    const page = parseRoute().page;
    if (page === 'review') renderReview();
    else if (page === 'timetable') await renderTimetable();
    toast(ws.errors.length ? 'Saved - some issues need fixing on the Review page' : 'Setup saved. Generate a new timetable when ready.', ws.errors.length ? 'bad' : 'good');
  } catch (e) { toast(e.message, 'bad'); m.done = 'expired'; }
  drawChat();
}

async function discardProposal(m) {
  m.done = 'discarded';
  if (state.proposal?.proposal_id === m.p.proposal_id) state.proposal = null;
  postJSON(`/workspaces/${state.wsId}/proposals/${m.p.proposal_id}/discard`).catch(() => {});
  drawChat(); drawMain();
}

async function undoEdit() {
  try {
    await postJSON(`/workspaces/${state.wsId}/undo`);
    state.proposal = null;
    state.chat.forEach((m) => { if (m.role === 'proposal' && !m.done) m.done = 'expired'; });
    const last = [...state.chat].reverse().find((m) => m.role === 'proposal' && m.done === 'applied');
    if (last) last.done = 'undone';
    await reloadTimetable();
    toast('Undone');
    drawChat();
  } catch (e) { toast(e.message, 'bad'); }
}

async function reloadTimetable() {
  state.tt = await api(`/workspaces/${state.wsId}/timetable`);
  if (parseRoute().page === 'timetable') await renderTimetable();
}

// --- chat drawer
function chatEl() {
  let el = document.getElementById('chat');
  if (!el) {
    el = document.createElement('aside');
    el.id = 'chat';
    el.className = 'chat no-print';
    el.setAttribute('aria-label', 'Timetable assistant');
    document.body.appendChild(el);
  }
  return el;
}

async function openChat(open) {
  state.chatOpen = open;
  const el = chatEl();
  el.classList.toggle('open', open);
  document.body.classList.toggle('chat-open', open);
  if (open && !state.ai) {
    try { state.ai = await api(`/workspaces/${state.wsId}/assistant`); } catch { state.ai = { ai_available: false }; }
  }
  drawChat();
  if (open) setTimeout(() => document.getElementById('chat-input')?.focus(), 50);
}

function pushChat(m) { state.chat.push(m); drawChat(); }
function dropBusy() { state.chat = state.chat.filter((m) => m.role !== 'busy'); }

function suggestions() {
  const I = state.tt?._idx;
  if (parseRoute().page === 'review' || !I) {
    const c = state.ws?.classes?.find((x) => !x.self_contained)?.name;
    const t = state.ws?.teachers?.[0]?.name;
    return [c && `How many periods of each subject does ${c} have?`, t && `${title(t)} doesn't come on Saturdays`,
      'We have 2 computer labs, not 3', 'Make PE 3 periods a week for grades 6 to 8'].filter(Boolean);
  }
  const cls = state.ttMode === 'class' ? I.classes[state.ttEntity]?.name : null;
  const t = state.ttMode === 'teacher' ? title(I.teachers[state.ttEntity]?.name || '') : null;
  const d = state.tt.days;
  const out = [];
  if (cls) out.push(`Swap ${cls}'s ${d[0]} period 2 with ${d[2]} period 2`, `No PE for ${cls} in the last period`);
  if (t) out.push(`${t} is absent on ${d[1]}. Who can cover?`, `Show ${t}'s ${d[0]}`);
  out.push(`Who is free on ${d[0]} period 3?`);
  return out;
}

function drawChat() {
  if (!state.chatOpen) return;
  const el = chatEl();
  const ai = state.ai || {};
  const hasKey = ai.ai_available || keyStore.get();
  const msgs = state.chat.map(renderMsg).join('');
  el.innerHTML = `
    <div class="chat-head">
      <div><strong>Assistant</strong><div class="muted" style="font-size:12px">Changes are previewed. Nothing is saved until you press Apply.</div></div>
      <button class="btn ghost icon-only" id="chat-close" aria-label="Close assistant">✕</button>
    </div>
    <div class="chat-body" id="chat-body">
      ${state.chat.length ? msgs : `<div class="chat-intro">${parseRoute().page === 'review' ? `
        <p>Ask about or change the setup, for example:</p>
        <ul><li>“Maths should be 7 periods a week in grades 9 and 10”</li><li>“Give 8 B one extra PE period”</li>
        <li>“Mrs Kapoor only comes Monday to Thursday”</li><li>“We have one physics lab for CHEM/PHY PR”</li></ul>
        <p class="muted">Each change is checked first; nothing is saved until you press Apply.</p>` : `
        <p>Ask for changes in plain words, for example:</p>
        <ul><li>“Swap 9 A's Monday 2nd period with Wednesday 4th”</li><li>“Move maths for 7 B to Friday period 3”</li>
        <li>“Mrs Sharma is absent tomorrow, who can cover?”</li><li>“Mr Rao can't come on Saturdays anymore”</li></ul>
        <p class="muted">Or use <b>Swap</b> in the toolbar and click two periods.</p>`}</div>`}
    </div>
    ${hasKey ? '' : `<div class="chat-key">
      <p>The assistant needs a Gemini API key on the server (<code>GEMINI_API_KEY</code>). You can also paste one here; it stays in this browser.</p>
      <div class="row"><input class="input" id="key-input" type="password" placeholder="Gemini API key" autocomplete="off"><button class="btn" id="key-save">Save</button></div></div>`}
    <div class="chat-suggest">${hasKey && !state.chat.length ? suggestions().map((s) => `<button class="chip" data-s="${esc(s)}">${esc(s)}</button>`).join('') : ''}</div>
    <form class="chat-form" id="chat-form">
      <textarea class="input" id="chat-input" rows="1" placeholder="${hasKey ? 'Ask for a change…' : 'Add a key to chat (Swap still works)'}" ${hasKey && !state.chatBusy ? '' : 'disabled'}></textarea>
      <button class="btn primary icon-only" aria-label="Send" ${hasKey && !state.chatBusy ? '' : 'disabled'}>${ICON.send}</button>
    </form>`;
  const body = document.getElementById('chat-body');
  body.scrollTop = body.scrollHeight;
  document.getElementById('chat-close').addEventListener('click', () => openChat(false));
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener('submit', (e) => { e.preventDefault(); sendChat(input.value); });
  el.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => sendChat(b.dataset.s)));
  document.getElementById('key-save')?.addEventListener('click', () => {
    keyStore.set(document.getElementById('key-input').value.trim()); drawChat();
  });
  el.querySelectorAll('[data-apply]').forEach((b) => b.addEventListener('click', () => applyProposal(state.chat[+b.dataset.apply])));
  el.querySelectorAll('[data-discard]').forEach((b) => b.addEventListener('click', () => discardProposal(state.chat[+b.dataset.discard])));
  el.querySelectorAll('[data-apply-setup]').forEach((b) => b.addEventListener('click', () => applySetup(state.chat[+b.dataset.applySetup])));
  el.querySelectorAll('[data-discard-setup]').forEach((b) => b.addEventListener('click', () => { const m = state.chat[+b.dataset.discardSetup]; m.done = 'discarded'; drawChat(); }));
  el.querySelectorAll('[data-goto]').forEach((b) => b.addEventListener('click', () => showEntity({ kind: b.dataset.kind, name: b.dataset.goto })));
}

function renderMsg(m, i) {
  if (m.role === 'user') return `<div class="msg user">${esc(m.text)}</div>`;
  if (m.role === 'assistant') return `<div class="msg bot">${esc(m.text).replace(/\n/g, '<br>')}</div>`;
  if (m.role === 'busy') return '<div class="msg bot busy"><span class="dots"><i></i><i></i><i></i></span> Working out the smallest change…</div>';
  if (m.role === 'error') return `<div class="msg err"><b>${esc(m.text)}</b>${(m.reasons || []).length ? `<ul>${m.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}</div>`;
  if (m.role === 'cover') {
    const c = m.cover;
    return `<div class="msg card-msg"><div class="cm-head">Cover for ${esc(title(c.teacher))} · ${esc(c.day)}</div>
      ${c.periods.length ? `<table class="cover"><tbody>${c.periods.map((p) => `<tr><td class="mono">P${p.period}</td>
        <td><button class="link" data-goto="${esc(p.classes[0])}" data-kind="class">${esc(p.classes.join(' + '))}</button> <span class="muted">${esc(p.subject)}</span></td>
        <td>${p.suggested ? `<b>${esc(title(p.suggested.teacher))}</b>${p.suggested.teaches_this_class ? ' <span class="badge good">knows class</span>' : ''}` : '<span class="badge bad">nobody free</span>'}
        ${p.alternatives.length ? `<div class="muted" style="font-size:12px">or ${esc(p.alternatives.slice(0, 3).map((a) => title(a.teacher)).join(', '))}</div>` : ''}</td></tr>`).join('')}</tbody></table>`
        : '<p class="muted">No lessons that day.</p>'}
      <p class="muted" style="font-size:12px;margin:6px 0 0">The weekly timetable is unchanged.</p></div>`;
  }
  if (m.role === 'setup') {
    const p = m.p;
    const status = { applied: '<span class="badge good">✓ saved</span>', discarded: '<span class="badge">discarded</span>', expired: '<span class="badge warn">expired</span>' }[m.done] || '';
    return `<div class="msg card-msg proposal ${m.done ? 'done' : ''}">
      <div class="cm-head">Setup change ${status}</div>
      <ul class="changes">${p.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
      ${p.new_errors.length ? `<div class="alert bad" style="margin:6px 0"><div><b>This would cause ${p.new_errors.length} problem${p.new_errors.length > 1 ? 's' : ''}:</b><ul>${p.new_errors.slice(0, 6).map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div></div>` : '<div class="badge good">✓ no new problems</div>'}
      ${p.fixed_errors.length ? `<div class="badge good" style="margin-top:6px">fixes ${p.fixed_errors.length} existing problem${p.fixed_errors.length > 1 ? 's' : ''}</div>` : ''}
      ${p.clears_timetable && !m.done ? '<div class="muted" style="font-size:12.5px;margin-top:6px">Saving this means the timetable has to be generated again.</div>' : ''}
      ${m.done ? '' : `<div class="row" style="margin-top:10px"><button class="btn primary" data-apply-setup="${i}">${p.new_errors.length ? 'Save anyway' : 'Apply'}</button><button class="btn" data-discard-setup="${i}">Discard</button></div>`}
    </div>`;
  }
  if (m.role === 'proposal') {
    const p = m.p, d = p.diff;
    const status = { applied: '<span class="badge good">✓ applied</span>', discarded: '<span class="badge">discarded</span>',
      replaced: '<span class="badge">replaced by a newer suggestion</span>', expired: '<span class="badge warn">expired</span>', undone: '<span class="badge warn">undone</span>' }[m.done] || '';
    return `<div class="msg card-msg proposal ${m.done ? 'done' : ''}">
      <div class="cm-head">${d.moved_periods ? `${d.moved_periods} period${d.moved_periods > 1 ? 's' : ''} move` : 'Nothing needs to move'}
        ${p.verified ? '<span class="badge good">✓ all rules checked</span>' : ''} ${status}</div>
      <div class="muted" style="font-size:12.5px">${esc(p.requests.join(' · '))}</div>
      <ul class="changes">${d.changes.slice(0, 14).map((c) => `<li><button class="link" data-goto="${esc(c.classes[0])}" data-kind="class">${esc(c.classes.join(' + '))}</button>
        <b>${esc(c.subject)}</b> <span class="mono">${esc(c.from.day.slice(0, 3))} P${c.from.period} → ${esc(c.to.day.slice(0, 3))} P${c.to.period}</span></li>`).join('')}
        ${d.changes.length > 14 ? `<li class="muted">…and ${d.changes.length - 14} more</li>` : ''}</ul>
      ${d.teachers.length ? `<div class="muted" style="font-size:12px">Teachers affected: ${esc(d.teachers.map(title).join(', '))}</div>` : ''}
      ${m.done ? '' : `<div class="row" style="margin-top:10px"><button class="btn primary" data-apply="${i}">Apply</button><button class="btn" data-discard="${i}">Discard</button></div>`}
    </div>`;
  }
  return '';
}

async function sendChat(text) {
  text = String(text || '').trim();
  if (!text || state.chatBusy) return;
  const history = state.chat.filter((m) => m.role === 'user' || m.role === 'assistant').slice(-12)
    .map((m) => ({ role: m.role, text: m.text }));
  pushChat({ role: 'user', text });
  await runEdit(() => postJSON(`/workspaces/${state.wsId}/chat`, { message: text, history, api_key: keyStore.get() || undefined }));
}

// ---------------------------------------------------------------- boot
window.addEventListener('hashchange', route);
matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => { if (parseRoute().page === 'timetable') drawMain(); });
route();
