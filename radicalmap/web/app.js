/* RadicalMap console.
 *
 * No framework and no build step on purpose: this is a local research tool
 * whose dependency surface should stay auditable, and every view here is a
 * table plus a detail pane. Rows are rendered with explicit DOM calls rather
 * than innerHTML templates — the corpus is untrusted text by definition, and
 * building it into HTML strings is how a research console grows an XSS hole
 * that fires on the exact content it was built to inspect.
 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const api = async (path, params) => {
  const u = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {}))
    if (v !== null && v !== undefined && v !== '') u.searchParams.set(k, v);
  const r = await fetch(u);
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail || d; } catch (_) {}
    throw new Error(`${r.status} — ${d}`);
  }
  return r.json();
};

let toastTimer;
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('err', !!isErr);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

/* ── formatting ─────────────────────────────────────────────────────────── */
const nf = new Intl.NumberFormat();
const num = v => (v === null || v === undefined || v === '') ? '' : nf.format(v);
function ts(v) {
  if (!v) return '';
  // Timestamps are unix seconds throughout the v2 schema.
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return String(v);
  return new Date(n * 1000).toISOString().replace('T', ' ').slice(0, 16);
}
const trunc = (s, n) => {
  s = (s === null || s === undefined) ? '' : String(s);
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
};

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of kids) if (c !== null && c !== undefined)
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
const chip = (txt, cls) => el('span', { class: 'chip ' + (cls || ''), text: txt });

function fillSelect(sel, values, { any = '(any)', selected = '' } = {}) {
  sel.replaceChildren();
  if (any !== null) sel.append(el('option', { value: '', text: any }));
  for (const v of values) {
    const val = (v && typeof v === 'object') ? v.value : v;
    const lab = (v && typeof v === 'object') ? v.label : v;
    sel.append(el('option', { value: val, text: lab, selected: String(val) === String(selected) || null }));
  }
}

/* ── generic table view ─────────────────────────────────────────────────── */
class TableView {
  constructor(opts) { Object.assign(this, opts); this.rows = []; this.idx = -1;
                      this.offset = 0; this.limit = 100; this.sort = null; this.total = 0; }

  async load(reset) {
    if (reset) this.offset = 0;
    let data;
    try { data = await this.fetch(this); }
    catch (e) { toast(e.message, true); $(this.status).textContent = e.message; return; }
    this.rows = data.rows || data;
    this.total = data.total !== undefined ? data.total : this.rows.length;
    if (data.limit) this.limit = data.limit;
    this.render();
    this.select(this.rows.length ? 0 : -1);
  }

  render() {
    const host = $(this.list);
    host.replaceChildren();
    if (!this.rows.length) {
      host.append(el('div', { class: 'empty', text: 'No rows.' }));
      this.updateStatus();
      return;
    }
    const thead = el('thead');
    const tr = el('tr');
    for (const c of this.columns) {
      const active = this.sort && this.sort.replace('-', '') === c.sort;
      const th = el('th', { class: c.num ? 'num' : null, title: c.sort ? 'click to sort' : null },
                    c.label);
      if (active) th.append(' ', el('span', { class: 'dir', text: this.sort[0] === '-' ? '▼' : '▲' }));
      if (c.sort) th.addEventListener('click', () => {
        this.sort = this.sort === c.sort ? '-' + c.sort : c.sort;
        this.load(true);
      });
      tr.append(th);
    }
    thead.append(tr);
    const tbody = el('tbody');
    this.rows.forEach((row, i) => {
      const r = el('tr', { onclick: () => this.select(i) });
      for (const c of this.columns) {
        const v = c.get(row);
        const td = el('td', { class: [c.num ? 'num' : '', c.dim ? 'dim' : ''].join(' ').trim() || null });
        if (v !== null && v !== undefined) td.append(v.nodeType ? v : String(v));
        td.title = td.textContent;
        r.append(td);
      }
      tbody.append(r);
    });
    host.append(el('table', {}, thead, tbody));
    this.updateStatus();
  }

  select(i) {
    this.idx = i;
    const body = $(this.list).querySelector('tbody');
    if (!body) return;
    [...body.rows].forEach((tr, n) => tr.setAttribute('aria-selected', n === i));
    if (i >= 0) {
      body.rows[i].scrollIntoView({ block: 'nearest' });
      if (this.detail) this.detail(this.rows[i]);
    }
    this.updateStatus();
  }

  move(d) {
    if (!this.rows.length) return;
    this.select(Math.max(0, Math.min(this.rows.length - 1, this.idx + d)));
  }

  async page(d) {
    const next = this.offset + d * this.limit;
    if (next < 0 || next >= this.total) return;
    this.offset = next;
    await this.load(false);
  }

  updateStatus() {
    const from = this.total ? this.offset + 1 : 0;
    const to = Math.min(this.offset + this.rows.length, this.total);
    $(this.status).textContent =
      `${nf.format(from)}–${nf.format(to)} of ${nf.format(this.total)}` +
      (this.idx >= 0 ? `   ·   row ${this.idx + 1}` : '');
    if (this.pager) $(this.pager).textContent =
      `${nf.format(from)}–${nf.format(to)} / ${nf.format(this.total)}`;
  }
}

/* ── detail rendering helpers ───────────────────────────────────────────── */
function kv(pairs) {
  const dl = el('dl', { class: 'kv' });
  for (const [k, v] of pairs) {
    if (v === null || v === undefined || v === '') continue;
    dl.append(el('dt', { text: k }), el('dd', {}, v.nodeType ? v : String(v)));
  }
  return dl;
}
const sec = t => el('h3', { class: 'sec', text: t });

/** Highlight `span` inside `text` without building HTML from either. */
function highlighted(text, span) {
  const wrap = el('pre', { class: 'text' });
  if (!text) return wrap;
  const i = span ? String(text).toLowerCase().indexOf(String(span).toLowerCase()) : -1;
  if (i < 0) { wrap.textContent = text; return wrap; }
  wrap.append(String(text).slice(0, i),
              el('mark', { text: String(text).slice(i, i + span.length) }),
              String(text).slice(i + span.length));
  return wrap;
}

const VERDICT_CLS = { confirmed: 'bad', rejected: 'ok', candidate: 'warn', uncertain: 'info' };

/* ── views ──────────────────────────────────────────────────────────────── */
const views = {};

views.items = new TableView({
  list: '#items-list', status: '#items-status', pager: '#p-items',
  fetch: v => api('/api/items', {
    q: $('#f-item-q').value.trim(), findings: $('#f-item-findings').checked || '',
    sort: v.sort, limit: v.limit, offset: v.offset }),
  columns: [
    { label: 'id', num: true, sort: 'id', get: r => r.id },
    { label: 'posted', sort: 'posted', dim: true, get: r => ts(r.posted_at) },
    { label: 'account', get: r => r.account_handle || '' },
    { label: 'caption', get: r => trunc(r.caption || r.title, 90) },
    { label: 'views', num: true, sort: 'views', get: r => num(r.view_count) },
    { label: 'likes', num: true, sort: 'likes', get: r => num(r.like_count) },
    { label: 'cmts', num: true, sort: 'comments', get: r => num(r.comment_count) },
    { label: 'find', num: true, get: r => r.findings ? chip(r.findings, 'bad') : '' },
  ],
  async detail(row) {
    const host = $('#items-detail');
    host.replaceChildren(el('div', { class: 'empty', text: 'Loading…' }));
    let d;
    try { d = await api(`/api/items/${row.id}`); }
    catch (e) { host.replaceChildren(el('div', { class: 'empty', text: e.message })); return; }
    const it = d.item;
    host.replaceChildren();
    host.append(sec(`item ${it.id}`), kv([
      ['platform', it.platform], ['kind', it.kind], ['account', it.account_handle],
      ['posted', ts(it.posted_at)], ['collected', ts(it.collected_at)],
      ['views', num(it.view_count)], ['likes', num(it.like_count)],
      ['duration', it.duration_s ? it.duration_s + 's' : ''], ['url', it.url],
    ]));
    if (it.caption) { host.append(sec('caption'), el('pre', { class: 'text', text: it.caption })); }
    if (d.tags.length) {
      const box = el('div');
      d.tags.forEach(t => { box.append(chip('#' + t.tag, 'info'), ' '); });
      host.append(sec(`tags (${d.tags.length})`), box);
    }
    if (d.sounds.length) {
      host.append(sec('sounds'), kv(d.sounds.map(s =>
        [s.title || '(untitled)', `${s.author_name || '?'} · used ${num(s.use_count)}×`])));
    }
    if (d.classifications.length) {
      host.append(sec(`classifications (${d.classifications.length})`));
      d.classifications.forEach(c => host.append(kv([
        [c.stage, el('span', {}, chip(c.verdict || '—', VERDICT_CLS[c.verdict] || ''), ' ',
                     c.domain || '', ' ', c.harm_type || '', ' ', c.rationale || '')],
      ])));
    }
    if (d.comments.length) {
      host.append(sec(`comments (${d.comments.length})`));
      d.comments.slice(0, 60).forEach(c => host.append(
        el('div', { style: 'margin-bottom:6px' },
          el('span', { class: 'chip mute', text: (c.author_hash || '').slice(0, 8) }), ' ',
          el('span', { class: 'dim', text: `♥${num(c.like_count)}` }), ' ',
          document.createTextNode(c.text || ''))));
    }
  },
});

views.cls = new TableView({
  list: '#cls-list', status: '#cls-status', pager: '#p-cls',
  fetch: v => api('/api/classifications', {
    stage: $('#f-cls-stage').value, verdict: $('#f-cls-verdict').value,
    target_type: $('#f-cls-target').value, domain: $('#f-cls-domain').value,
    findings: $('#f-cls-findings').checked || '',
    sort: v.sort, limit: v.limit, offset: v.offset }),
  columns: [
    { label: 'id', num: true, sort: 'id', get: r => r.id },
    { label: 'target', get: r => `${r.target_type}:${r.target_id}` },
    { label: 'stage', get: r => chip(r.stage || '', r.stage === 'llm' ? 'mute' : '') },
    { label: 'verdict', get: r => chip(r.verdict || '—', VERDICT_CLS[r.verdict] || '') },
    { label: 'domain', dim: true, get: r => r.domain || '' },
    { label: 'harm', dim: true, get: r => r.harm_type || '' },
    { label: 'stance', dim: true, get: r => r.stance || '' },
    { label: 'pattern', dim: true, get: r => trunc(r.lexicon_pattern, 34) },
    { label: 'tier', get: r => r.tier ? chip(r.tier, r.tier === 'A' ? 'bad' : r.tier === 'B' ? 'warn' : '') : '' },
    { label: 'evidence', get: r => trunc(r.evidence_span, 46) },
    { label: 'find', num: true, get: r => r.is_finding ? chip('YES', 'bad') : '' },
  ],
  async detail(row) {
    const host = $('#cls-detail');
    host.replaceChildren();
    host.append(sec(`classification ${row.id}`), kv([
      ['target', `${row.target_type}:${row.target_id}`], ['stage', row.stage],
      ['verdict', row.verdict], ['run', `${row.run_id} (${row.run_version || '?'})`],
      ['domain', row.domain], ['harm_type', row.harm_type], ['stance', row.stance],
      ['severity', row.severity], ['confidence', row.confidence],
      ['is_finding', row.is_finding ? 'yes' : 'no'],
      ['lexicon', row.lexicon_pattern], ['tier', row.tier],
    ]));
    if (row.rationale) host.append(sec('rationale'), el('pre', { class: 'text', text: row.rationale }));
    host.append(sec('source text'));
    try {
      const t = await api(`/api/target/${row.target_type}/${row.target_id}`);
      host.append(highlighted(t.text, row.evidence_span));
    } catch (e) {
      host.append(el('div', { class: 'empty', text: e.message }));
    }
  },
});

views.net = new TableView({
  list: '#net-list', status: '#net-status', pager: '#p-net',
  fetch: v => api('/api/nodes', {
    node_type: $('#f-net-type').value, community: $('#f-net-comm').value,
    sort: v.sort, limit: v.limit, offset: v.offset }),
  columns: [
    { label: 'type', get: r => chip(r.node_type, 'info') },
    { label: 'id', num: true, get: r => r.node_id },
    { label: 'label', get: r => trunc(r.label, 60) },
    { label: 'pagerank', num: true, sort: 'pagerank', get: r => (r.pagerank ?? 0).toFixed(5) },
    { label: 'degree', num: true, sort: 'degree', get: r => num(r.degree) },
    { label: 'wdeg', num: true, sort: 'weighted', get: r => (r.weighted_degree ?? 0).toFixed(2) },
    { label: 'k-core', num: true, sort: 'core', get: r => num(r.core_number) },
    { label: 'comm', num: true, sort: 'community', get: r => r.community },
    { label: 'bridge', num: true, sort: 'bridge', get: r => (r.bridge_score ?? 0).toFixed(3) },
  ],
  async detail(row) {
    const host = $('#net-detail');
    host.replaceChildren();
    host.append(sec(`${row.node_type} ${row.node_id}`), kv([
      ['label', row.label], ['pagerank', row.pagerank], ['degree', row.degree],
      ['weighted degree', row.weighted_degree], ['k-core', row.core_number],
      ['community', row.community], ['bridge score', row.bridge_score],
    ]));
    host.append(sec('edges'));
    try {
      const edges = await api(`/api/nodes/${row.node_type}/${row.node_id}/edges`, { limit: 100 });
      if (!edges.length) { host.append(el('div', { class: 'empty', text: 'No edges.' })); return; }
      const tb = el('tbody');
      edges.forEach(e => tb.append(el('tr', {},
        el('td', {}, chip(e.direction, e.direction === 'out' ? 'acc' : 'mute')),
        el('td', {}, e.edge_type),
        el('td', {}, e.direction === 'out' ? `${e.dst_type}:${e.dst_id}` : `${e.src_type}:${e.src_id}`),
        el('td', { class: 'num' }, (e.weight ?? 0).toFixed(4)),
        el('td', { class: 'num dim' }, num(e.observed_count)))));
      host.append(el('table', {}, el('thead', {}, el('tr', {},
        el('th', {}, 'dir'), el('th', {}, 'type'), el('th', {}, 'other'),
        el('th', { class: 'num' }, 'weight'), el('th', { class: 'num' }, 'obs'))), tb));
    } catch (e) { host.append(el('div', { class: 'empty', text: e.message })); }
  },
});

views.lex = new TableView({
  list: '#lex-list', status: '#lex-status', pager: '#p-lex',
  fetch: v => api('/api/lexicon', {
    tier: $('#f-lex-tier').value, q: $('#f-lex-q').value.trim(),
    limit: v.limit, offset: v.offset }),
  columns: [
    { label: 'tier', get: r => chip(r.tier || '?', r.tier === 'A' ? 'bad' : r.tier === 'B' ? 'warn' : '') },
    { label: 'slug', get: r => r.slug || '' },
    { label: 'pattern', get: r => trunc(r.pattern, 52) },
    { label: 'type', dim: true, get: r => r.pattern_type },
    { label: 'domain', dim: true, get: r => r.domain || '' },
    { label: 'harm', dim: true, get: r => r.harm_type || '' },
    { label: 'stance', dim: true, get: r => r.stance_hint || '' },
    { label: 'hits', num: true, get: r => num(r.hits) },
    { label: 'find', num: true, get: r => r.findings ? chip(r.findings, 'bad') : '' },
  ],
  detail(row) {
    const host = $('#lex-detail');
    host.replaceChildren();
    host.append(sec(row.slug || `entry ${row.id}`), kv([
      ['pattern', row.pattern], ['pattern_type', row.pattern_type], ['tier', row.tier],
      ['domain', row.domain], ['harm_type', row.harm_type], ['stance hint', row.stance_hint],
      ['language', row.lang], ['origin', row.origin],
      ['hits', num(row.hits)], ['findings', num(row.findings)],
    ]));
    if (row.definition) host.append(sec('definition'), el('pre', { class: 'text', text: row.definition }));
    if (row.notes) host.append(sec('notes'), el('pre', { class: 'text', text: row.notes }));
    host.append(sec('tier means'), el('div', { class: 'dim', text:
      row.tier === 'A' ? 'A — unambiguous in this context. May start an adjudication alone.'
      : row.tier === 'B' ? 'B — strongly indicative but context-dependent.'
      : 'C — weak on its own. A tier-C hit alone can never start an adjudication; that gate is what removed v1’s largest false-positive class.' }));
  },
});

views.queue = new TableView({
  list: '#queue-list', status: '#queue-status', pager: '#p-queue',
  fetch(v) {
    return $('#f-queue-view').value === 'prop'
      ? api('/api/propagation', { limit: v.limit, offset: v.offset })
      : api('/api/jobs', { status: $('#f-queue-status').value, limit: v.limit, offset: v.offset });
  },
  get columns() {
    return $('#f-queue-view').value === 'prop' ? [
      { label: 'id', num: true, get: r => r.id },
      { label: 'run', num: true, get: r => r.run_id },
      { label: 'src', get: r => `${r.src_type}:${r.src_id}` },
      { label: 'edge', get: r => r.edge_type },
      { label: 'target', get: r => trunc(r.target, 52) },
      { label: 'depth', num: true, get: r => r.depth },
      { label: 'score', num: true, get: r => r.score },
      { label: 'drift', num: true, get: r => r.drift },
      { label: 'decision', get: r => chip(r.decision || '', r.decision === 'accepted' ? 'ok' : 'mute') },
    ] : [
      { label: 'id', num: true, get: r => r.id },
      { label: 'type', get: r => r.job_type },
      { label: 'target', get: r => trunc(r.target, 58) },
      { label: 'status', get: r => chip(r.status, r.status === 'done' ? 'ok'
          : r.status === 'failed' ? 'bad' : r.status === 'pending' ? 'warn' : 'mute') },
      { label: 'try', num: true, get: r => r.attempts },
      { label: 'depth', num: true, get: r => r.depth },
      { label: 'edge', dim: true, get: r => r.edge_type || '' },
      { label: 'error', dim: true, get: r => trunc(r.last_error, 44) },
      { label: 'created', dim: true, get: r => ts(r.created_at) },
    ];
  },
});

/* ── dashboard ──────────────────────────────────────────────────────────── */
async function loadDash() {
  let d;
  try { d = await api('/api/overview'); }
  catch (e) { toast(e.message, true); return; }
  $('#dbpath').textContent = d.db.split('/').slice(-2).join('/');

  const cards = $('#dash-cards');
  cards.replaceChildren();
  const order = ['items', 'accounts', 'comments', 'tags', 'sounds', 'edges',
                 'classifications', 'gold_annotations', 'transcripts', 'links'];
  for (const k of order) {
    cards.append(el('div', { class: 'card' + (k === 'gold_annotations' ? ' hot' : '') },
      el('div', { class: 'n', text: num(d.counts[k] ?? 0) }),
      el('div', { class: 'l', text: k.replace('_', ' ') })));
  }

  const cols = $('#dash-cols');
  cols.replaceChildren();

  const verdicts = el('div', { class: 'panel' }, sec('classification verdicts'));
  verdicts.append(kv(Object.entries(d.verdicts || {}).map(([k, v]) =>
    [k, el('span', {}, chip(num(v), VERDICT_CLS[k] || '')) ])));
  verdicts.append(el('div', { class: 'dim', style: 'margin-top:8px' },
    `${num(d.findings)} findings from ${num(d.counts.classifications)} classifications.`));
  cols.append(verdicts);

  if (d.counts.gold_annotations === 0) {
    cols.append(el('div', { class: 'panel' }, sec('gold set'),
      el('div', { class: 'blind-note' },
        'No gold annotations yet. Every precision/recall figure depends on this set, ' +
        'and it cannot be produced by the model being evaluated. Open the Annotate tab.')));
  }

  const runs = el('div', { class: 'panel' }, sec('classifier runs'));
  runs.append(kv(d.runs.map(r => [`#${r.id} ${r.stage}`,
    `${r.version || ''} ${r.model || ''} · ${num(r.target_count)} targets · ${ts(r.started_at)}`])));
  cols.append(runs);

  const jobs = el('div', { class: 'panel' }, sec('fetch queue'));
  jobs.append(kv(d.jobs.map(j => [j.status, num(j.n)])));
  cols.append(jobs);

  if (d.lexicon) {
    cols.append(el('div', { class: 'panel' }, sec('lexicon'), kv([
      ['version', d.lexicon.version], ['entries', num(d.lexicon.entries)],
      ['created', ts(d.lexicon.created_at)]])));
  }
  if (d.saturation && d.saturation.length) {
    const p = el('div', { class: 'panel' }, sec('crawl saturation'));
    p.append(kv(d.saturation.map(s =>
      [`${num(s.fetches)} fetches`,
       `${num(s.known_accounts)} accounts (+${num(s.new_accounts)} new) · ${num(s.known_items)} items`])));
    cols.append(p);
  }
  if (d.platforms.length) {
    cols.append(el('div', { class: 'panel' }, sec('platforms'),
      kv(d.platforms.map(p => [p.name, num(p.items) + ' items']))));
  }
  const span = d.span || {};
  $('#dash-status').textContent = span.first_post
    ? `corpus spans ${ts(span.first_post)} → ${ts(span.last_post)}   ·   ${d.db}`
    : d.db;
}

/* ── annotate ───────────────────────────────────────────────────────────── */
const ann = { queue: [], i: 0, opts: null, pick: { domain: null, harm_type: null, stance: null },
              axis: 0, positive: null };

async function annDraw() {
  const who = $('#f-ann-who').value.trim();
  if (!who) { toast('enter an annotator name first', true); $('#f-ann-who').focus(); return; }
  if (who.startsWith('model:')) { toast('annotator cannot start with "model:"', true); return; }
  try {
    if (!ann.opts) ann.opts = await api('/api/annotate/options');
    const d = await api('/api/annotate/sample', {
      n: $('#f-ann-n').value, seed: $('#f-ann-seed').value, annotator: who });
    ann.queue = d.rows; ann.i = 0;
    toast(`${d.remaining} to annotate (${d.skipped} already done)`);
    annRender();
  } catch (e) { toast(e.message, true); }
}

function annRender() {
  const host = $('#ann-wrap');
  host.replaceChildren();
  $('#ann-progress').textContent = ann.queue.length
    ? `${ann.i + 1} / ${ann.queue.length}` : '—';
  if (ann.i >= ann.queue.length) {
    host.append(el('div', { class: 'empty', text: 'Sample complete. Draw another to continue.' }));
    return;
  }
  const row = ann.queue[ann.i];
  ann.pick = { domain: null, harm_type: null, stance: null };
  ann.positive = null; ann.axis = 0;

  const card = el('div', { class: 'ann-card' });
  card.append(el('div', { class: 'blind-note' },
    'Blinded: the classifier’s verdict is not shown — and is not sent to this page. ' +
    'Matched lexicon terms are shown because they say what drew the sample, not what it concluded.'));
  card.append(kv([
    ['target', `${row.target_type}:${row.target_id}`],
    ['stratum', row.stratum],
    ['matched terms', (row.terms || []).join(', ') || '(none — drawn as a clean control)'],
  ]));
  card.append(el('div', { class: 'ann-text', text: row.text || '(no text)' }));

  const axes = [
    ['is this a positive?', 'positive',
      [{ slug: 'yes', name: 'yes — a real finding' }, { slug: 'no', name: 'no' }]],
    ['domain', 'domain', ann.opts.domains],
    ['harm type', 'harm_type', ann.opts.harm_types],
    ['stance', 'stance', ann.opts.stances],
  ];
  axes.forEach(([label, key, opts], ai) => {
    const box = el('div', { class: 'axis' });
    box.append(el('label', { class: 'dim', text: label + (ai === ann.axis ? '  ◀' : '') }));
    const o = el('div', { class: 'opts' });
    opts.forEach((opt, n) => {
      const b = el('button', {
        'aria-pressed': 'false', 'data-axis': key, 'data-slug': opt.slug,
        onclick: () => annPick(key, opt.slug),
      }, n < 9 ? el('kbd', { text: n + 1 }) : null, opt.name || opt.slug);
      o.append(b);
    });
    box.append(o);
    card.append(box);
  });

  const acts = el('div', { class: 'ann-actions' },
    el('button', { class: 'primary', onclick: annSave }, 'save  ⏎'),
    el('button', { onclick: () => { ann.i++; annRender(); } }, 'skip  s'),
    el('span', { class: 'dim', id: 'ann-hint', text: 'pick "is this a positive?" to enable save' }));
  card.append(acts);
  host.append(card);
  annSyncButtons();
}

function annPick(key, slug) {
  if (key === 'positive') ann.positive = (slug === 'yes');
  else ann.pick[key] = ann.pick[key] === slug ? null : slug;
  annSyncButtons();
}

function annSyncButtons() {
  document.querySelectorAll('#ann-wrap .opts button').forEach(b => {
    const k = b.dataset.axis, s = b.dataset.slug;
    const on = k === 'positive'
      ? (ann.positive !== null && ((s === 'yes') === ann.positive))
      : ann.pick[k] === s;
    b.setAttribute('aria-pressed', String(on));
  });
  const hint = $('#ann-hint');
  if (hint) hint.textContent = ann.positive === null
    ? 'pick "is this a positive?" to enable save'
    : `will save: positive=${ann.positive} domain=${ann.pick.domain || '—'} ` +
      `harm=${ann.pick.harm_type || '—'} stance=${ann.pick.stance || '—'}`;
}

async function annSave() {
  const row = ann.queue[ann.i];
  if (!row) return;
  if (ann.positive === null) { toast('decide positive / not first', true); return; }
  const body = {
    target_type: row.target_type, target_id: row.target_id,
    label: ann.pick.domain || (ann.positive ? 'positive' : 'clean'),
    is_positive: ann.positive, annotator: $('#f-ann-who').value.trim(),
    domain: ann.pick.domain, harm_type: ann.pick.harm_type, stance: ann.pick.stance,
  };
  try {
    const r = await fetch('/api/annotate', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) { toast(j.detail || r.statusText, true); return; }
    $('#ann-status').textContent =
      `${j.coverage.annotated_targets} annotated targets`;
    ann.i++; annRender();
  } catch (e) { toast(e.message, true); }
}

/* ── tabs + keyboard ────────────────────────────────────────────────────── */
const TABS = [
  ['dash', 'Dashboard', loadDash],
  ['items', 'Explorer', () => views.items.load(true)],
  ['cls', 'Findings', () => views.cls.load(true)],
  ['net', 'Network', () => views.net.load(true)],
  ['lex', 'Lexicon', () => views.lex.load(true)],
  ['queue', 'Queue', () => views.queue.load(true)],
  ['ann', 'Annotate', () => {}],
];
let current = 'dash';

function show(key) {
  current = key;
  TABS.forEach(([k]) => {
    $('#view-' + k).classList.toggle('active', k === key);
    $(`#tab-${k}`).setAttribute('aria-selected', String(k === key));
  });
  const t = TABS.find(t => t[0] === key);
  if (t) t[2]();
}

function buildTabs() {
  const nav = $('#tabs');
  TABS.forEach(([k, label], i) => nav.append(el('button', {
    id: 'tab-' + k, 'aria-selected': 'false', onclick: () => show(k),
  }, el('kbd', { text: i + 1 }), label)));
}

const isTyping = e => /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  if (e.key === 'Escape') {
    $('#help').classList.remove('show');
    if (isTyping(e)) e.target.blur();
    return;
  }
  if (isTyping(e)) {
    if (e.key === 'Enter' && current === 'ann' && e.target.id === 'f-ann-who') annDraw();
    else if (e.key === 'Enter') { const v = views[current]; if (v) v.load(true); }
    return;
  }
  if (e.key === '?') { $('#help').classList.toggle('show'); return; }

  if (current === 'ann' && ann.queue.length && ann.i < ann.queue.length) {
    if (e.key === 'Enter') { e.preventDefault(); annSave(); return; }
    if (e.key === 's') { ann.i++; annRender(); return; }
    if (e.key === 'Tab') {
      e.preventDefault(); ann.axis = (ann.axis + 1) % 4; annRender(); return;
    }
    if (/^[1-9]$/.test(e.key)) {
      const keys = ['positive', 'domain', 'harm_type', 'stance'];
      const key = keys[ann.axis];
      const opts = key === 'positive'
        ? [{ slug: 'yes' }, { slug: 'no' }]
        : ann.opts[key === 'harm_type' ? 'harm_types' : key === 'domain' ? 'domains' : 'stances'];
      const opt = opts[Number(e.key) - 1];
      if (opt) annPick(key, opt.slug);
      return;
    }
    return;
  }

  if (/^[1-7]$/.test(e.key)) { show(TABS[Number(e.key) - 1][0]); return; }

  const v = views[current];
  if (!v) return;
  if (e.key === 'j') v.move(1);
  else if (e.key === 'k') v.move(-1);
  else if (e.key === 'g') v.select(0);
  else if (e.key === 'G') v.select(v.rows.length - 1);
  else if (e.key === ']') v.page(1);
  else if (e.key === '[') v.page(-1);
  else if (e.key === 'r') v.load(true);
  else if (e.key === '/') {
    e.preventDefault();
    const box = $('#view-' + current).querySelector('.filters input[type=text],.filters input:not([type])');
    if (box) box.focus();
  }
});

/* ── wiring ─────────────────────────────────────────────────────────────── */
async function initFilters() {
  try {
    const f = await api('/api/classifications/facets');
    fillSelect($('#f-cls-stage'), f.stage);
    fillSelect($('#f-cls-verdict'), f.verdict);
    fillSelect($('#f-cls-target'), f.target_type);
    fillSelect($('#f-cls-domain'), f.domain);
  } catch (_) {}
  try {
    const comms = await api('/api/communities');
    fillSelect($('#f-net-comm'), comms.map(c => ({ value: c.community, label: `#${c.community} (${c.members})` })));
  } catch (_) {}
  fillSelect($('#f-net-type'), ['item', 'account', 'tag', 'sound']);
  fillSelect($('#f-lex-tier'), ['A', 'B', 'C']);
  fillSelect($('#f-queue-status'), ['pending', 'done', 'failed', 'skipped', 'leased']);

  const reload = key => () => views[key].load(true);
  ['f-cls-stage', 'f-cls-verdict', 'f-cls-target', 'f-cls-domain', 'f-cls-findings']
    .forEach(id => $('#' + id).addEventListener('change', reload('cls')));
  ['f-net-type', 'f-net-comm'].forEach(id => $('#' + id).addEventListener('change', reload('net')));
  $('#f-lex-tier').addEventListener('change', reload('lex'));
  $('#f-item-findings').addEventListener('change', reload('items'));
  $('#f-queue-status').addEventListener('change', reload('queue'));
  $('#f-queue-view').addEventListener('change', reload('queue'));

  let t;
  const debounced = key => () => { clearTimeout(t); t = setTimeout(() => views[key].load(true), 260); };
  $('#f-item-q').addEventListener('input', debounced('items'));
  $('#f-lex-q').addEventListener('input', debounced('lex'));

  document.querySelectorAll('[data-page]').forEach(b =>
    b.addEventListener('click', () => views[b.dataset.page].page(Number(b.dataset.dir))));
  $('#ann-draw').addEventListener('click', annDraw);
  $('#help').addEventListener('click', e => {
    if (e.target.id === 'help') $('#help').classList.remove('show');
  });
}

buildTabs();
initFilters().then(() => show('dash'));
