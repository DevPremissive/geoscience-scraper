/* C4.2 targeting viewer.
 *
 * Three things this file is careful about:
 *
 *   1. It never contacts anything but this server. There is no basemap style
 *      URL, no glyph URL and no sprite URL — a MapLibre style with a `glyphs`
 *      entry fetches from that origin the moment any text is drawn, so this
 *      viewer draws no text on the map at all and puts labels in the DOM.
 *
 *   2. Every layer refetches on `moveend` with the current bbox. The server
 *      decides the H3 resolution from the feature count and reports it back;
 *      the rail shows what you are actually looking at, so an aggregate is
 *      never mistaken for the underlying cells.
 *
 *   3. Clicks resolve finest-first (r9 land / criticality, then claims, then
 *      the r7 surface). Clicking a hot hex and getting the r7 summary when a
 *      claim is under the cursor is the wrong answer to the question asked.
 */
'use strict';

const RAMP = ['#0d1b2a', '#1b3c5c', '#20687a', '#44946a', '#a8b54a', '#eebf3e', '#f7812e', '#de3e2e'];

const state = {
  catalog: null,
  metric: 'heat',
  quarter: null,
  system: null,
  version: null,
  block: null,
  aoi: 'abitibi',
  eventEnd: null,
  eventWindowMonths: 12,
  selected: null,
  inflight: {},
};

const LAYERS = {
  context:     { chk: 'l_context',     url: () => `/api/context?bbox=${bbox()}` },
  fabric:      { chk: 'l_fabric',      url: fabricUrl },
  land:        { chk: 'l_land',        url: () => `/api/land?aoi=${state.aoi}&bbox=${bbox()}` },
  criticality: { chk: 'l_criticality', url: () => `/api/criticality?bbox=${bbox()}${state.block ? '&block=' + state.block : ''}` },
  claims:      { chk: 'l_claims',      url: () => `/api/claims?bbox=${bbox()}` },
  blocks:      { chk: 'l_blocks',      url: () => `/api/blocks?bbox=${bbox()}` },
  events:      { chk: 'l_events',      url: eventsUrl },
};

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {},
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#eef1f4' } }],
  },
  center: [-80.75, 48.35],
  zoom: 8,
  maxZoom: 15,
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

function bbox() {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(5)).join(',');
}

function fabricUrl() {
  const p = new URLSearchParams({ metric: state.metric, bbox: bbox() });
  if (state.metric === 'heat' && state.quarter) p.set('quarter', state.quarter);
  if (state.metric === 'prospectivity') {
    if (state.system) p.set('system', state.system);
    if (state.version) p.set('version', state.version);
  }
  if (state.metric === 'criticality' && state.block) p.set('block', state.block);
  return '/api/fabric?' + p;
}

function eventsUrl() {
  const p = new URLSearchParams({ bbox: bbox() });
  if (state.eventEnd) {
    const end = new Date(state.eventEnd);
    const start = new Date(end);
    start.setMonth(start.getMonth() - state.eventWindowMonths);
    p.set('start', start.toISOString().slice(0, 10));
    p.set('end', end.toISOString().slice(0, 10));
  }
  return '/api/events?' + p;
}

/* ---- layer plumbing ------------------------------------------------------ */

function ensureSource(name, data) {
  const src = map.getSource(name);
  if (src) { src.setData(data); return false; }
  map.addSource(name, { type: 'geojson', data });
  return true;
}

function rampExpr(lo, hi) {
  if (lo === null || hi === null || lo === hi) return RAMP[RAMP.length - 2];
  const stops = [];
  RAMP.forEach((c, i) => { stops.push(lo + (hi - lo) * (i / (RAMP.length - 1)), c); });
  return ['interpolate', ['linear'], ['coalesce', ['get', 'value'], lo], ...stops];
}

/* Bottom to top. Layers arrive in whatever order their fetches resolve, and
   `context-fill` is an opaque white province polygon — if it lands last it
   paints over every surface beneath it and the map looks empty with the rail
   cheerfully reporting a few hundred cells loaded. Order is therefore enforced
   after every add rather than guessed with a `beforeId`. */
const Z_ORDER = ['context-fill', 'fabric-fill', 'land-fill', 'events-fill',
                 'criticality-fill', 'claims-fill', 'claims-line', 'blocks-line'];

function enforceOrder() {
  Z_ORDER.forEach((id) => { if (map.getLayer(id)) map.moveLayer(id); });
}

function addLayerStyles(name, meta) {
  const lo = meta.value_min, hi = meta.value_max;
  switch (name) {
    case 'context':
      map.addLayer({ id: 'context-fill', type: 'fill', source: 'context',
        paint: {
          'fill-color': ['match', ['get', 'context'], 'lakes', '#dae5ef', '#ffffff'],
          'fill-outline-color': '#c3ccd6',
        } });
      break;
    case 'fabric':
      map.addLayer({ id: 'fabric-fill', type: 'fill', source: 'fabric',
        paint: { 'fill-color': rampExpr(lo, hi), 'fill-opacity': 0.72 } });
      break;
    case 'land':
      map.addLayer({ id: 'land-fill', type: 'fill', source: 'land',
        paint: {
          'fill-color': ['case', ['has', 'colour'], ['get', 'colour'], rampExpr(0, 1)],
          'fill-opacity': 0.62,
        } });
      break;
    case 'criticality':
      map.addLayer({ id: 'criticality-fill', type: 'fill', source: 'criticality',
        paint: { 'fill-color': rampExpr(lo, hi), 'fill-opacity': 0.8 } });
      break;
    case 'claims':
      map.addLayer({ id: 'claims-fill', type: 'fill', source: 'claims',
        paint: { 'fill-color': '#20303f', 'fill-opacity': 0.05 } });
      map.addLayer({ id: 'claims-line', type: 'line', source: 'claims',
        paint: { 'line-color': '#2f3f52', 'line-width': 0.7 } });
      break;
    case 'blocks':
      map.addLayer({ id: 'blocks-line', type: 'line', source: 'blocks',
        paint: { 'line-color': '#b4402f', 'line-width': 1.4 } });
      break;
    case 'events':
      map.addLayer({ id: 'events-fill', type: 'fill', source: 'events',
        paint: { 'fill-color': '#e8a33c', 'fill-opacity': 0.55,
                 'fill-outline-color': '#8a5c12' } });
      break;
  }
}

function removeLayer(name) {
  ['-fill', '-line'].forEach((sfx) => {
    if (map.getLayer(name + sfx)) map.removeLayer(name + sfx);
  });
  if (map.getSource(name)) map.removeSource(name);
}

async function refreshLayer(name) {
  const def = LAYERS[name];
  const on = document.getElementById(def.chk).checked;
  if (!on) { removeLayer(name); return; }
  const url = def.url();
  const token = (state.inflight[name] = (state.inflight[name] || 0) + 1);
  let gj;
  try {
    const r = await fetch(url);
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      setStatus(`${name}: ${body.detail || r.statusText}`, true);
      removeLayer(name);
      return;
    }
    gj = await r.json();
  } catch (e) {
    setStatus(`${name}: ${e.message}`, true);
    return;
  }
  // A slow response for a viewport the user has already left must not repaint.
  if (token !== state.inflight[name]) return;

  const fresh = ensureSource(name, gj);
  if (fresh) { addLayerStyles(name, gj.meta || {}); enforceOrder(); }
  else if (name === 'fabric' || name === 'criticality') {
    const id = name + '-fill';
    if (map.getLayer(id)) {
      map.setPaintProperty(id, 'fill-color',
        rampExpr((gj.meta || {}).value_min, (gj.meta || {}).value_max));
    }
  }
  if (name === 'fabric') renderMetricMeta(gj.meta || {});
  if (name === 'land') renderLandLegend(gj.meta || {});
}

let refreshTimer = null;
function refreshAll() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    setStatus('loading…');
    await Promise.all(Object.keys(LAYERS).map(refreshLayer));
    setStatus(`z${map.getZoom().toFixed(1)} · ${bbox()}`);
  }, 180);
}

function setStatus(msg, bad) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.style.color = bad ? 'var(--bad)' : '';
}

/* ---- rail ---------------------------------------------------------------- */

function renderMetricMeta(meta) {
  const bits = [];
  if (meta.aggregated) {
    bits.push(`<b>Aggregated to r${meta.resolution}</b> by ${meta.agg} — ` +
      `${fmt(meta.n_source_cells)} r7 cells in view. Zoom in for per-cell values.`);
  } else if (meta.resolution) {
    bits.push(`r${meta.resolution}, ${fmt(meta.features)} cells.`);
  }
  if (meta.truncated) bits.push('<b>Truncated</b> to the feature budget — highest values kept.');
  if (meta.quarter) bits.push(`Quarter <code>${meta.quarter}</code>.`);
  if (meta.version) bits.push(`Model <code>${meta.version}</code>, blocked ROC AUC ${meta.blocked_roc_auc_mean}.`);
  if (meta.value_min !== null && meta.value_min !== undefined) {
    bits.push(`Range ${sig(meta.value_min)} → ${sig(meta.value_max)}.`);
  }
  if (meta.survivorship_biased) bits.push('<b>Survivorship-biased</b> — pre-archive quarters are descriptive only.');
  document.getElementById('metricMeta').innerHTML = bits.join(' ');
}

function renderLandLegend(meta) {
  const el = document.getElementById('landLegend');
  if (!document.getElementById('l_land').checked) { el.innerHTML = ''; return; }
  if (meta.mode === 'r7_open_fraction') {
    el.innerHTML = `<span>Showing <b>open fraction</b> per r7 hex — ` +
      `${fmt(meta.n_source_cells)} r9 cells in view is over budget. Zoom in for per-cell state.</span>`;
    return;
  }
  const colours = meta.colours || {};
  const counts = meta.state_counts || {};
  el.innerHTML = Object.keys(colours).map((k) =>
    `<span><i style="background:${colours[k]}"></i>${k}${counts[k] ? ' ' + fmt(counts[k]) : ''}</span>`
  ).join('');
}

function fmt(n) { return (n === null || n === undefined) ? '–' : Number(n).toLocaleString(); }
function sig(n) { return (n === null || n === undefined) ? '–' : Number(n).toPrecision(3); }
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function loadCatalog() {
  const r = await fetch('/api/catalog');
  const cat = await r.json();
  state.catalog = cat;

  const msel = document.getElementById('metric');
  msel.innerHTML = cat.fabric_metrics.map((m) =>
    `<option value="${m.metric}" ${m.available ? '' : 'disabled'}>` +
    `${esc(m.label)}${m.available ? '' : ' — unavailable'}</option>`).join('');
  msel.value = state.metric;

  const heat = cat.fabric_metrics.find((m) => m.metric === 'heat');
  const quarters = ((heat || {}).meta || {}).quarters_available || [];
  document.getElementById('quarter').innerHTML =
    ['<option value="">latest</option>', '<option value="peak">peak (any quarter)</option>']
      .concat(quarters.slice().reverse().map((q) => `<option value="${q}">${q}</option>`)).join('');

  const models = [];
  (cat.models || []).forEach((m) => m.versions.forEach((v) =>
    models.push({ system: m.system, version: v })));
  document.getElementById('model').innerHTML = models.map((m) =>
    `<option value="${m.system}|${m.version}">${esc(m.system)} · ${esc(m.version)}</option>`).join('');

  document.getElementById('aoi').innerHTML = (cat.aois || []).map((a) =>
    `<option value="${a.aoi_id}">${esc(a.aoi_id)} · ${fmt(a.cells)} r9 · ${a.snapshot}</option>`).join('');
  if (cat.aois && cat.aois.length) state.aoi = cat.aois[0].aoi_id;

  const blocks = cat.watched_blocks || [];
  document.getElementById('block').innerHTML =
    ['<option value="">all blocks</option>'].concat(blocks.map((b) =>
      `<option value="${b.block_id}">${esc(b.block_id)} · ${fmt(b.cells)} cells</option>`)).join('');
  document.getElementById('blockList').innerHTML = blocks.slice(0, 30).map((b) =>
    `<div class="item" data-block="${b.block_id}"><b>${esc(b.block_id)}</b> ` +
    `<em>${fmt(b.cells)} critical cells · max ${b.max_score}</em></div>`).join('')
    || '<div class="meta">No criticality scored yet.</div>';

  const ev = cat.events || {};
  if (ev.max) {
    state.eventEnd = ev.max;
    document.getElementById('timeLabel').innerHTML =
      `${fmt(ev.events)} events, ${ev.min} → ${ev.max}.`;
  }

  document.getElementById('dossierList').innerHTML = (cat.dossiers || []).map((d) =>
    `<div class="item" data-dossier="${esc(d)}"><b>${esc(d)}</b></div>`).join('')
    || '<div class="meta">No dossiers on disk yet.</div>';

  syncMetricControls();
}

function syncMetricControls() {
  document.getElementById('quarterRow').hidden = state.metric !== 'heat';
  document.getElementById('modelRow').hidden = state.metric !== 'prospectivity';
  document.getElementById('blockRow').hidden = state.metric !== 'criticality';
}

/* ---- evidence panel ------------------------------------------------------ */

function openPanel(html) {
  document.getElementById('panelBody').innerHTML = html;
  document.getElementById('panel').hidden = false;
}

function rows(pairs) {
  return '<table>' + pairs.filter(Boolean).map(
    ([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('') + '</table>';
}

function sparkline(values, w = 380, h = 34) {
  const vals = values.map((v) => (v === null || v === undefined ? 0 : v));
  if (!vals.length) return '';
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  const pts = vals.map((v, i) =>
    `${(i / Math.max(1, vals.length - 1)) * w},${h - ((v - lo) / span) * (h - 4) - 2}`).join(' ');
  const zeroY = h - ((0 - lo) / span) * (h - 4) - 2;
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    (lo < 0 && hi > 0 ? `<line x1="0" y1="${zeroY}" x2="${w}" y2="${zeroY}" stroke="#3a4756"/>` : '') +
    `<polyline fill="none" stroke="#4aa3df" stroke-width="1.5" points="${pts}"/></svg>`;
}

function pill(text, cls) { return `<span class="pill ${cls || 'dim'}">${esc(text)}</span>`; }

async function showCell(cellId) {
  state.selected = cellId;
  openPanel('<div class="meta">loading evidence…</div>');
  let ev;
  try {
    const r = await fetch(`/api/cell/${cellId}`);
    ev = await r.json();
    if (!r.ok) throw new Error(ev.detail || r.statusText);
  } catch (e) {
    openPanel(`<div class="missing">Could not load ${esc(cellId)}: ${esc(e.message)}</div>`);
    return;
  }

  const out = [];
  out.push(`<h3>${ev.resolution === 9 ? 'Cell' : 'Region'} evidence</h3>
            <div class="cellid">${esc(ev.cell_id)} · r${ev.resolution} · ${ev.area_km2} km²</div>`);

  out.push(`<img class="figure" alt="land context"
      src="/render?cell=${encodeURIComponent(ev.cell_id)}&layers=context,land,claims&width=390&height=250&aoi=${state.aoi}">`);

  // Land
  const ls = ev.land_state || {};
  out.push('<h4>Land state (C1.1)</h4>');
  if (ls.available && ls.state) {
    out.push(rows([
      ['State', pill(ls.state, ls.state === 'open' ? 'open' : 'claimed')],
      ls.blocking_layer ? ['Blocked by', esc(ls.blocking_layer)] : null,
      ['As of', esc(ls.as_of)], ['AOI', esc(ls.aoi)],
    ]));
  } else if (ls.available) {
    out.push(rows([
      ['Open cells', `${fmt(ls.open_cells)} of ${fmt(Object.values(ls.state_counts || {}).reduce((a, b) => a + b, 0))}`],
      ['Open fraction', (ls.open_fraction * 100).toFixed(1) + '%'],
      ['As of', esc(ls.as_of)],
    ]));
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc(ls.reason)}</div>`);
  }

  // Heat
  out.push('<h4>Staking heat (C1.3)</h4>');
  if (ev.heat && ev.heat.quarters) {
    out.push(sparkline(ev.heat.heat_cross_smoothed));
    out.push(rows([
      ['Quarters observed', ev.heat.quarters.length],
      ['Latest heat', sig(ev.heat.heat_cross_smoothed[ev.heat.heat_cross_smoothed.length - 1])],
      ['Ever staked', fmt(ev.heat.ever_staked_count) + ' times'],
      ['Cells staked (sum)', fmt(ev.heat.cells_staked.reduce((a, b) => a + b, 0))],
      ['Expiries (sum)', fmt(ev.heat.expiry_count.reduce((a, b) => a + b, 0))],
    ]));
    out.push('<div class="note">Prior interest is a weak positive prior, never a label (Master §2).</div>');
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc((ev.heat || {}).reason || 'no heat record')}</div>`);
  }

  // Prospectivity
  out.push('<h4>Prospectivity (C2.1)</h4>');
  if (ev.prospectivity && ev.prospectivity.score !== undefined) {
    const p = ev.prospectivity;
    out.push(rows([
      ['Score', `${p.score} <em>(${p.percentile}th pct)</em>`],
      ['System', esc(p.system)], ['Version', esc(p.version)],
      ['Blocked ROC AUC', p.blocked_roc_auc_mean],
      ['Scores', esc(p.scores)],
    ]));
    out.push('<div class="note">One term in the deal score, not the ranking.</div>');
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc((ev.prospectivity || {}).reason || 'no score for this cell')}</div>`);
  }

  // Criticality
  out.push('<h4>Criticality (C1.5)</h4>');
  if (ev.criticality && ev.criticality.top) {
    out.push(rows(ev.criticality.top.map((t) => [
      `${t.block_id} @ ${t.distance_m ?? '–'} m`,
      `<b>${t.score}</b> <em>${esc((t.reason_codes || []).join(', '))}</em>`,
    ])));
    out.push(`<div class="note">${esc(ev.criticality.note)}</div>`);
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc((ev.criticality || {}).reason || 'not scored')}</div>`);
  }

  // Lapse watch
  if (ev.lapse_watch) {
    out.push('<h4>Lapse watch (C1.6)</h4>');
    out.push(rows(ev.lapse_watch.claims.map((c) => [
      `${c.claim_id} · ${c.owner}`,
      `${c.expiry_date} <em>(${c.days_to_expiry} d)</em> ${pill(c.status, 'warn')}`,
    ])));
    out.push(`<div class="note">${esc(ev.lapse_watch.note)}</div>`);
  }

  // Neighbours & buyers
  out.push('<h4>Neighbours &amp; buyers (C1.4 / C6.2)</h4>');
  const nb = ev.neighbours || {};
  if (nb.available && (nb.blocks || []).length) {
    out.push(rows(nb.blocks.slice(0, 8).map((b) => {
      const by = b.buyer;
      let tag = '';
      if (by) {
        if (by.profile_thin) tag = ' ' + pill('profile thin', 'warn');
        else if (by.resolution_status && by.resolution_status !== 'resolved') {
          tag = ' ' + pill(by.resolution_status.replace(/_/g, ' '), 'dim');
        }
        if (by.consolidator_flag) tag += ' ' + pill('consolidator', 'open');
      }
      return [esc(b.owner_norm),
        `${fmt(b.n_claims)} claims · ${fmt(Math.round(b.area_ha))} ha${tag}`];
    })));
    const thin = (nb.blocks || []).filter((b) => b.buyer && b.buyer.profile_thin);
    if (thin.length) {
      out.push('<div class="note">A thin SEDAR+ profile means the captured filing ' +
        'index is incomplete for that issuer, not that the issuer is silent (audit L4).</div>');
    }
    out.push(`<div class="note">${esc(nb.note)}</div>`);
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc(nb.reason || 'no blocks within 5 km')}</div>`);
  }

  // Evidence layers
  const ft = ev.features || {};
  out.push('<h4>Evidence layers vs Ontario (C2)</h4>');
  if (ft.available) {
    out.push(rows(ft.features.map((f) => [
      esc(f.feature),
      `${sig(f.value)} <em>(${f.percentile}th pct)</em>`,
    ])));
    out.push(`<div class="note">${fmt(ft.n_features_on_cell)} features on this cell, ` +
      `snapshot ${esc(ft.snapshot)}. Percentiles are against every cell carrying that feature.</div>`);
  } else {
    out.push(`<div class="missing">NOT AVAILABLE — ${esc(ft.reason)}</div>`);
  }

  // Dossier
  out.push('<h4>Dossier (C4.1)</h4>');
  const d = ev.dossier || {};
  if (d.exists) {
    out.push(rows([['Versions', esc((d.versions || []).join(', '))],
                   ['Status', pill(d.status || 'draft', 'warn')]]));
    out.push(`<a href="${d.html}" target="_blank"><button class="ghost">Open dossier</button></a>`);
  } else {
    out.push('<div class="meta">No dossier for this target yet.</div>');
  }
  if (ev.resolution === 9) {
    out.push(`<button id="genDossier" data-cell="${esc(ev.cell_id)}">Generate dossier (draft)</button>`);
    out.push('<div class="note">Assembly takes up to a few minutes and produces a ' +
      '<b>draft</b>. Nothing auto-advances past draft.</div>');
  } else {
    out.push('<div class="meta">Dossiers are written for r9 cells. Zoom in and click a claim-scale cell.</div>');
  }

  openPanel(out.join(''));

  const btn = document.getElementById('genDossier');
  if (btn) btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = 'assembling…';
    try {
      const r = await fetch(`/api/dossier?cell_id=${encodeURIComponent(btn.dataset.cell)}`,
                            { method: 'POST' });
      const res = await r.json();
      btn.textContent = res.ok ? 'done — reopening' : 'failed (see below)';
      if (!res.ok) {
        openPanel(`<h3>Dossier assembly failed</h3><pre class="meta" style="white-space:pre-wrap">` +
          `${esc(res.stderr || res.stdout)}</pre>`);
      } else {
        showCell(btn.dataset.cell);
      }
    } catch (e) {
      btn.textContent = 'failed: ' + e.message;
    }
  };
}

/* ---- interactions -------------------------------------------------------- */

map.on('click', (e) => {
  const order = ['land-fill', 'criticality-fill', 'claims-fill', 'events-fill',
                 'fabric-fill', 'blocks-line'];
  const present = order.filter((id) => map.getLayer(id));
  const hits = map.queryRenderedFeatures(e.point, { layers: present });
  if (!hits.length) return;
  const byLayer = {};
  hits.forEach((h) => { if (!byLayer[h.layer.id]) byLayer[h.layer.id] = h; });
  const first = present.map((id) => byLayer[id]).find(Boolean);
  if (!first) return;

  if (first.layer.id === 'claims-fill') {
    const p = first.properties;
    new maplibregl.Popup({ closeButton: true })
      .setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.TENURE_NUM)}</b><br>${esc(p.HOLDER)}<br>` +
               `status ${esc(p.TENURE_STA)}<br>due ${esc((p.CLAIM_DUE_ || '').slice(0, 10))}`)
      .addTo(map);
    return;
  }
  if (first.layer.id === 'blocks-line') {
    const p = first.properties;
    new maplibregl.Popup({ closeButton: true })
      .setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.owner_norm)}</b><br>${esc(p.block_id)}<br>` +
               `${fmt(p.n_claims)} claims`)
      .addTo(map);
    return;
  }
  const cellId = first.properties.cell_id;
  if (cellId) showCell(cellId);
});

map.on('mousemove', (e) => {
  const ids = ['land-fill', 'criticality-fill', 'fabric-fill', 'claims-fill']
    .filter((id) => map.getLayer(id));
  map.getCanvas().style.cursor =
    ids.length && map.queryRenderedFeatures(e.point, { layers: ids }).length ? 'pointer' : '';
});

map.on('moveend', refreshAll);

document.getElementById('panelClose').onclick = () => {
  document.getElementById('panel').hidden = true;
};

document.getElementById('metric').onchange = (e) => {
  state.metric = e.target.value;
  syncMetricControls();
  refreshLayer('fabric');
};
document.getElementById('quarter').onchange = (e) => {
  state.quarter = e.target.value || null;
  refreshLayer('fabric');
};
document.getElementById('model').onchange = (e) => {
  const [sys, ver] = e.target.value.split('|');
  state.system = sys; state.version = ver;
  refreshLayer('fabric');
};
document.getElementById('block').onchange = (e) => {
  state.block = e.target.value || null;
  refreshLayer('fabric');
  refreshLayer('criticality');
};
document.getElementById('aoi').onchange = (e) => {
  state.aoi = e.target.value;
  refreshLayer('land');
};

Object.entries(LAYERS).forEach(([name, def]) => {
  document.getElementById(def.chk).onchange = () => {
    refreshLayer(name);
    if (name === 'land') renderLandLegend({});
  };
});

document.getElementById('timeScrub').oninput = (e) => {
  const ev = (state.catalog || {}).events || {};
  if (!ev.min || !ev.max) return;
  const t0 = new Date(ev.min).getTime(), t1 = new Date(ev.max).getTime();
  const at = new Date(t0 + (t1 - t0) * (e.target.value / 100));
  state.eventEnd = at.toISOString().slice(0, 10);
  const from = new Date(at); from.setMonth(from.getMonth() - state.eventWindowMonths);
  document.getElementById('timeLabel').innerHTML =
    `Window <code>${from.toISOString().slice(0, 10)}</code> → <code>${state.eventEnd}</code>` +
    ` (${state.eventWindowMonths} months)`;
  if (document.getElementById('l_events').checked) refreshLayer('events');
};

document.getElementById('blockList').onclick = (e) => {
  const item = e.target.closest('[data-block]');
  if (!item) return;
  state.block = item.dataset.block;
  document.getElementById('block').value = state.block;
  document.getElementById('l_criticality').checked = true;
  fetch(`/api/criticality?block=${state.block}&bbox=-95.5,41.5,-74,57`)
    .then((r) => r.json())
    .then((gj) => {
      if (!gj.features.length) return;
      const b = new maplibregl.LngLatBounds();
      gj.features.forEach((f) => f.geometry.coordinates[0].forEach((c) => b.extend(c)));
      map.fitBounds(b, { padding: 80, maxZoom: 12 });
    });
};

document.getElementById('dossierList').onclick = (e) => {
  const item = e.target.closest('[data-dossier]');
  if (!item) return;
  const cell = item.dataset.dossier.replace(/^[A-Z]{2}-/, '');
  gotoCell(cell);
};

document.getElementById('gotoBtn').onclick = () => {
  const v = document.getElementById('goto').value.trim();
  if (!v) return;
  if (v.includes(',')) {
    const [lon, lat] = v.split(',').map(Number);
    if (isFinite(lon) && isFinite(lat)) map.flyTo({ center: [lon, lat], zoom: 12 });
    return;
  }
  gotoCell(v);
};

async function gotoCell(cellId) {
  try {
    const r = await fetch(`/api/cell/${encodeURIComponent(cellId)}`);
    const ev = await r.json();
    if (!r.ok) { setStatus(ev.detail || 'not found', true); return; }
    map.flyTo({ center: ev.centroid, zoom: ev.resolution >= 9 ? 13 : 10 });
    showCell(cellId);
  } catch (e) {
    setStatus(e.message, true);
  }
}

map.on('load', async () => {
  await loadCatalog();
  refreshAll();
});
