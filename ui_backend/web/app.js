/* ════════════════════════════════════════════════════════════
   Tradingm5 Dashboard – single-file client
   ════════════════════════════════════════════════════════════ */

// ── API layer ──────────────────────────────────────────────
const API = {
  _json(r) {
    if (!r.ok) return r.json().then(d => { throw d; });
    return r.json();
  },
  get(url)       { return fetch(url).then(this._json); },
  post(url, body){ return fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) }).then(this._json); },
  del(url)       { return fetch(url, { method:'DELETE' }).then(this._json); },

  getAccounts()           { return this.get('/api/accounts'); },
  upsertAccount(p)        { return this.post('/api/accounts', p); },
  deleteAccount(name)     { return this.del(`/api/accounts/${encodeURIComponent(name)}`); },
  healthcheckAll()        { return this.post('/api/healthcheck', {}); },
  healthcheckOne(name)    { return this.get(`/api/healthcheck/${encodeURIComponent(name)}`); },
  submitPlan(rows)        { return this.post('/api/trade/submit-plan', { plan_rows: rows, timeout_seconds: 3600, poll_seconds: 1.0 }); },
  quickMulti(body)        { return this.post('/api/trade/quick-multi', body); },
  getBook()               { return this.get('/api/orders/active'); },
  closeOrder(account, symbol, side) { return this.post('/api/orders/close', { account, symbol, side }); },
  licenseStatus()         { return this.get('/api/license/status'); },
  activateLicense(path)   { return this.post('/api/license/activate', { license_key_path: path }); },
};

// ── State ──────────────────────────────────────────────────
const state = {
  accounts: [],
  healthMap: {},
  orderRowId: 0,
};

// ── Helpers ────────────────────────────────────────────────
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];
function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function setResult(id, v) { const el = document.getElementById(id); if (el) el.textContent = typeof v === 'string' ? v : JSON.stringify(v, null, 2); }

function profitClass(val) {
  const n = Number(val);
  if (n > 0) return 'profit-pos';
  if (n < 0) return 'profit-neg';
  return '';
}

function showSpinner(btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = orig + '<span class="spinner"></span>';
  return () => { btn.disabled = false; btn.innerHTML = orig; };
}

// ── Tab switching ──────────────────────────────────────────
$('#tabNav').addEventListener('click', e => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  const target = btn.dataset.tab;
  $$('.tab').forEach(t => t.classList.toggle('active', t === btn));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === `panel-${target}`));
});

// ── Accounts ───────────────────────────────────────────────
async function loadAccounts() {
  try {
    state.accounts = await API.getAccounts();
  } catch { state.accounts = []; }
  renderAccounts();
}

function renderAccounts() {
  const tbody = $('#accountsTable tbody');
  tbody.innerHTML = '';
  for (const acc of state.accounts) {
    const h = state.healthMap[acc.name];
    let healthHtml = '<span style="color:var(--text-dim)">—</span>';
    if (h === true) healthHtml = '<span class="health-ok">OK</span>';
    else if (typeof h === 'string') healthHtml = `<span class="health-fail">${h}</span>`;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(acc.name)}</td>
      <td>${acc.mt5_login}</td>
      <td>${esc(acc.mt5_server)}</td>
      <td>${healthHtml}</td>
      <td>
        <button class="btn-sm btn-muted hc-one" data-name="${esc(acc.name)}">Health</button>
        <button class="btn-sm btn-red del-acc" data-name="${esc(acc.name)}">Del</button>
      </td>`;
    tbody.appendChild(tr);
  }
  populateAccountSelects();
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

$('#accountsTable').addEventListener('click', async e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const name = btn.dataset.name;
  if (btn.classList.contains('hc-one')) {
    const done = showSpinner(btn);
    try {
      const r = await API.healthcheckOne(name);
      state.healthMap[name] = r.ok ? true : (r.error || 'FAIL');
    } catch (err) { state.healthMap[name] = String(err.detail || err.message || 'Error'); }
    done();
    renderAccounts();
  } else if (btn.classList.contains('del-acc')) {
    if (!confirm(`Delete account "${name}"?`)) return;
    const done = showSpinner(btn);
    try { await API.deleteAccount(name); } catch {}
    done();
    await loadAccounts();
  }
});

$('#accountForm').addEventListener('submit', async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const payload = {
    name: String(f.get('name')).trim(),
    mt5_login: Number(f.get('mt5_login')),
    mt5_password: String(f.get('mt5_password')),
    mt5_server: String(f.get('mt5_server')).trim(),
    mt5_path: String(f.get('mt5_path') || '').trim() || null,
    mt5_portable: f.get('mt5_portable') === 'on',
  };
  try {
    await API.upsertAccount(payload);
    e.target.reset();
    await loadAccounts();
  } catch (err) { alert('Save failed: ' + (err.detail || err.message || JSON.stringify(err))); }
});

$('#healthcheckAllBtn').addEventListener('click', async function () {
  const done = showSpinner(this);
  $('#healthStatus').textContent = 'Checking...';
  try {
    const res = await API.healthcheckAll();
    state.healthMap = {};
    for (const r of (res.results || [])) {
      state.healthMap[r.name] = r.ok ? true : (r.error || 'FAIL');
    }
    renderAccounts();
    $('#healthStatus').textContent = 'Done';
  } catch (err) {
    $('#healthStatus').textContent = 'Error: ' + (err.detail || err.message || '');
  }
  done();
});

// ── Order Builder ──────────────────────────────────────────
function populateAccountSelects() {
  $$('.order-row select[data-field="account"]').forEach(sel => {
    const current = sel.value;
    sel.innerHTML = '<option value="">-- account --</option>' +
      state.accounts.map(a => `<option value="${esc(a.name)}"${a.name === current ? ' selected' : ''}>${esc(a.name)}</option>`).join('');
  });
}

function createOrderRow() {
  const id = ++state.orderRowId;
  const div = document.createElement('div');
  div.className = 'order-row';
  div.dataset.rowId = id;
  div.innerHTML = `
    <select data-field="account"><option value="">-- account --</option></select>
    <select data-field="side"><option value="buy">BUY</option><option value="sell">SELL</option></select>
    <input data-field="symbol" placeholder="symbol" value="EURUSD" class="field-wide">
    <input data-field="volume" type="number" step="0.01" min="0.01" placeholder="vol" value="0.1" style="width:70px">
    <input data-field="trigger_price" type="number" step="0.00001" placeholder="trigger (opt)" style="width:100px">
    <input data-field="sl_price" type="number" step="0.00001" placeholder="SL (opt)" style="width:90px">
    <input data-field="tp_price" type="number" step="0.00001" placeholder="TP (opt)" style="width:90px">
    <input data-field="comment" placeholder="comment" style="width:100px">
    <button class="remove-row-btn" title="Remove row">&times;</button>`;
  $('#orderRows').appendChild(div);
  populateAccountSelects();
}

$('#addOrderRowBtn').addEventListener('click', () => createOrderRow());
$('#clearOrderRowsBtn').addEventListener('click', () => { $('#orderRows').innerHTML = ''; setResult('tradingResult', ''); });
$('#orderRows').addEventListener('click', e => {
  if (e.target.closest('.remove-row-btn')) e.target.closest('.order-row').remove();
});

function collectOrderRows() {
  const rows = [];
  for (const div of $$('.order-row')) {
    const get = field => {
      const el = div.querySelector(`[data-field="${field}"]`);
      return el ? el.value.trim() : '';
    };
    const account = get('account');
    const symbol = get('symbol');
    const side = get('side');
    const volume = parseFloat(get('volume'));
    if (!account || !symbol || !side || !volume) continue;
    const row = { account, symbol, side, volume };
    const trigger = parseFloat(get('trigger_price'));
    const sl = parseFloat(get('sl_price'));
    const tp = parseFloat(get('tp_price'));
    const comment = get('comment');
    if (!isNaN(trigger) && trigger > 0) row.trigger_price = trigger;
    if (!isNaN(sl) && sl > 0) row.sl_price = sl;
    if (!isNaN(tp) && tp > 0) row.tp_price = tp;
    if (comment) row.comment = comment;
    rows.push(row);
  }
  return rows;
}

$('#submitAllOrdersBtn').addEventListener('click', async function () {
  const rows = collectOrderRows();
  if (!rows.length) { setResult('tradingResult', 'Add at least one valid order row.'); return; }
  const done = showSpinner(this);
  setResult('tradingResult', 'Submitting orders in parallel...');
  try {
    const res = await API.submitPlan(rows);
    setResult('tradingResult', res);
  } catch (err) {
    setResult('tradingResult', { error: err.detail || err.message || JSON.stringify(err) });
  }
  done();
});

$('#submitJsonPlanBtn').addEventListener('click', async function () {
  const raw = $('#planJson').value.trim();
  if (!raw) { setResult('jsonPlanResult', 'Enter JSON plan.'); return; }
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { setResult('jsonPlanResult', 'Invalid JSON: ' + e.message); return; }
  const done = showSpinner(this);
  try {
    const res = await API.submitPlan(parsed);
    setResult('jsonPlanResult', res);
  } catch (err) {
    setResult('jsonPlanResult', { error: err.detail || err.message || JSON.stringify(err) });
  }
  done();
});

// ── Live Book ──────────────────────────────────────────────
function renderBook(data) {
  const profit = Number(data.total_profit || 0);
  const profStr = profit.toFixed(2);
  setText('totalProfit', profStr);
  const headerPnl = document.getElementById('headerPnl');
  headerPnl.textContent = `P/L: ${profStr}`;
  headerPnl.style.color = profit >= 0 ? 'var(--green)' : 'var(--red)';

  const posTbody = $('#positionsTable tbody');
  posTbody.innerHTML = '';
  for (const p of (data.positions || [])) {
    const pVal = Number(p.profit);
    const cls = profitClass(pVal);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(p.account)}</td>
      <td>${p.ticket}</td>
      <td>${esc(p.symbol)}</td>
      <td>${p.side}</td>
      <td>${p.volume}</td>
      <td>${p.price_open}</td>
      <td class="${cls}">${pVal.toFixed(2)}</td>
      <td>${p.sl || 0}</td>
      <td>${p.tp || 0}</td>
      <td><button class="btn-sm btn-red close-pos" data-account="${esc(p.account)}" data-symbol="${esc(p.symbol)}" data-side="${p.side}">Close</button></td>`;
    posTbody.appendChild(tr);
  }

  const ordTbody = $('#ordersTable tbody');
  ordTbody.innerHTML = '';
  for (const o of (data.pending_orders || [])) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(o.account)}</td>
      <td>${o.ticket}</td>
      <td>${esc(o.symbol)}</td>
      <td>${o.order_type}</td>
      <td>${o.volume}</td>
      <td>${o.price_open}</td>
      <td>${o.sl || 0}</td>
      <td>${o.tp || 0}</td>`;
    ordTbody.appendChild(tr);
  }
}

$('#positionsTable').addEventListener('click', async e => {
  const btn = e.target.closest('.close-pos');
  if (!btn) return;
  if (!confirm(`Close ${btn.dataset.side} ${btn.dataset.symbol} on ${btn.dataset.account}?`)) return;
  const done = showSpinner(btn);
  try {
    const res = await API.closeOrder(btn.dataset.account, btn.dataset.symbol, btn.dataset.side);
    setResult('tradingResult', { closed: res.closed_count, details: res.details });
  } catch (err) {
    alert('Close failed: ' + (err.detail || err.message || JSON.stringify(err)));
  }
  done();
});

// ── WebSocket realtime ─────────────────────────────────────
let ws = null;
let wsRetry = 1000;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/realtime`);
  ws.onopen = () => {
    document.getElementById('wsDot').classList.add('connected');
    wsRetry = 1000;
  };
  ws.onclose = () => {
    document.getElementById('wsDot').classList.remove('connected');
    setTimeout(connectWS, wsRetry);
    wsRetry = Math.min(wsRetry * 1.5, 10000);
  };
  ws.onmessage = evt => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'snapshot') renderBook(msg.data);
    } catch {}
  };
}

// ── License ────────────────────────────────────────────────
async function refreshLicense() {
  try {
    const s = await API.licenseStatus();
    setResult('licenseResult', s);
    const badge = document.getElementById('headerLic');
    badge.textContent = `License: ${s.status}`;
    badge.style.color = s.ok ? 'var(--green)' : 'var(--red)';
  } catch (err) { setResult('licenseResult', { error: String(err) }); }
}

$('#activateLicenseBtn').addEventListener('click', async function () {
  const p = $('#licensePath').value.trim();
  if (!p) { setResult('licenseResult', 'Enter license file path.'); return; }
  const done = showSpinner(this);
  try {
    const res = await API.activateLicense(p);
    setResult('licenseResult', res);
  } catch (err) { setResult('licenseResult', { error: err.detail || err.message || JSON.stringify(err) }); }
  done();
  await refreshLicense();
});

$('#refreshLicenseBtn').addEventListener('click', refreshLicense);

// ── Boot ───────────────────────────────────────────────────
(async function boot() {
  await loadAccounts();
  refreshLicense();
  connectWS();
  createOrderRow();
})();
