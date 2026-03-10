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
  searchSymbols(account, query = '', limit = 20) {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    return this.get(`/api/symbols/${encodeURIComponent(account)}?${params.toString()}`);
  },
  validateSymbol(account, symbol) {
    const params = new URLSearchParams({ symbol });
    return this.get(`/api/symbols/validate/${encodeURIComponent(account)}?${params.toString()}`);
  },
  submitPlan(rows)        { return this.post('/api/trade/submit-plan', { plan_rows: rows, timeout_seconds: 30, poll_seconds: 0.5 }); },
  quickMulti(body)        { return this.post('/api/trade/quick-multi', body); },
  getBook()               { return this.get('/api/orders/active'); },
  closeOrder(account, symbol, side, ticket = null) {
    const body = { account, symbol, side };
    if (ticket !== null && ticket !== undefined) {
      body.ticket = Number(ticket);
    }
    return this.post('/api/orders/close', body);
  },
  cancelPendingOrder(account, ticket) {
    return this.post('/api/orders/cancel-pending', { account, ticket: Number(ticket) });
  },
  licenseStatus()         { return this.get('/api/license/status'); },
  activateLicense(path)   { return this.post('/api/license/activate', { license_key_path: path }); },
  closedHistory(accountName = '', days = 7, limit = 300) {
    const params = new URLSearchParams({ days: String(days), limit: String(limit) });
    if (accountName) params.set('account_name', accountName);
    return this.get(`/api/history/closed?${params.toString()}`);
  },
  systemLogs(limit = 20) {
    return this.get(`/api/system/logs?limit=${encodeURIComponent(String(limit))}`);
  },
};

// ── State ──────────────────────────────────────────────────
const state = {
  accounts: [],
  healthMap: {},
  orderRowId: 0,
  closingSet: new Set(),
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

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

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
  renderHistoryAccountFilter();
  populateAccountSelects();
}

function renderHistoryAccountFilter() {
  const sel = $('#historyAccount');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">All Accounts</option>' +
    state.accounts.map(a => `<option value="${esc(a.name)}"${a.name === current ? ' selected' : ''}>${esc(a.name)}</option>`).join('');
}

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
    <input data-field="symbol" placeholder="symbol" value="EURUSD" class="field-wide" list="sym-list-${id}">
    <datalist id="sym-list-${id}"></datalist>
    <button class="btn-sm btn-muted sym-find-btn" title="Search symbols for selected account">Find</button>
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
  if (e.target.closest('.remove-row-btn')) {
    e.target.closest('.order-row').remove();
    return;
  }
  const findBtn = e.target.closest('.sym-find-btn');
  if (findBtn) {
    const row = findBtn.closest('.order-row');
    if (!row) return;
    const account = row.querySelector('[data-field="account"]')?.value?.trim();
    const symbolInput = row.querySelector('[data-field="symbol"]');
    if (!account) {
      setResult('tradingResult', { ok: false, error: 'Select account first, then use Find for symbols.' });
      return;
    }
    const q = symbolInput?.value?.trim() || '';
    const done = showSpinner(findBtn);
    API.searchSymbols(account, q, 40)
      .then((res) => {
        const dl = row.querySelector(`datalist#sym-list-${row.dataset.rowId}`);
        if (!dl) return;
        dl.innerHTML = '';
        for (const item of (res.items || [])) {
          const opt = document.createElement('option');
          opt.value = item.name;
          opt.label = item.description ? `${item.name} - ${item.description}` : item.name;
          dl.appendChild(opt);
        }
        const count = (res.items || []).length;
        if (count === 0) {
          setResult('tradingResult', {
            ok: false,
            account,
            query: q,
            error: 'No symbols found. Use exact broker symbol name (e.g. EURUSD / EURUSDm / XAUUSD).',
          });
          return;
        }
        setResult('tradingResult', { ok: true, account, symbols_found: count, query: q });
      })
      .catch((err) => {
        setResult('tradingResult', { ok: false, error: err.detail || err.message || String(err) });
      })
      .finally(done);
  }
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
  setResult('tradingResult', 'Validating symbols...');
  try {
    const validations = await Promise.all(
      rows.map(r =>
        API.validateSymbol(r.account, r.symbol).catch(err => ({
          ok: false,
          account: r.account,
          symbol: r.symbol,
          error: err.detail || err.message || String(err),
        }))
      )
    );
    const invalid = validations.filter(v => !v.ok);
    if (invalid.length > 0) {
      setResult('tradingResult', { ok: false, error: 'Invalid symbols found', invalid });
      done();
      return;
    }

    setResult('tradingResult', 'Submitting orders in parallel...');
    const res = await API.submitPlan(rows);
    setResult('tradingResult', res);
  } catch (err) {
    setResult('tradingResult', { error: err.detail || err.message || JSON.stringify(err) });
  }
  done();
});

// ── Live Book ──────────────────────────────────────────────
function updateCloseSelectedBtn() {
  const checked = $$('#positionsTable tbody .pos-check:checked');
  const btn = $('#closeSelectedBtn');
  btn.disabled = checked.length === 0;
  btn.textContent = checked.length > 0 ? `Close Selected (${checked.length})` : 'Close Selected';
}

function updateCancelPendingSelectedBtn() {
  const checked = $$('#ordersTable tbody .pending-check:checked');
  const btn = $('#cancelSelectedPendingBtn');
  btn.disabled = checked.length === 0;
  btn.textContent = checked.length > 0 ? `Cancel Selected Pending (${checked.length})` : 'Cancel Selected Pending';
}

function renderBook(data) {
  const profit = Number(data.total_profit || 0);
  const profStr = profit.toFixed(2);
  setText('totalProfit', profStr);
  const headerPnl = document.getElementById('headerPnl');
  headerPnl.textContent = `P/L: ${profStr}`;
  headerPnl.style.color = profit >= 0 ? 'var(--green)' : 'var(--red)';

  const positions = data.positions || [];
  const posTbody = $('#positionsTable tbody');
  const previouslyChecked = new Set(
    $$('#positionsTable tbody .pos-check:checked').map(cb => cb.dataset.key)
  );

  posTbody.innerHTML = '';
  for (const p of positions) {
    const pVal = Number(p.profit);
    const cls = profitClass(pVal);
    const key = `${p.account}|${p.ticket}`;
    const checked = previouslyChecked.has(key) ? ' checked' : '';
    const closing = state.closingSet.has(key);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="checkbox" class="pos-check" data-key="${key}" data-account="${esc(p.account)}" data-symbol="${esc(p.symbol)}" data-side="${p.side}" data-ticket="${p.ticket}"${checked}></td>
      <td>${esc(p.account)}</td>
      <td>${p.ticket}</td>
      <td>${esc(p.symbol)}</td>
      <td>${p.side}</td>
      <td>${p.volume}</td>
      <td>${p.price_open}</td>
      <td class="${cls}">${pVal.toFixed(2)}</td>
      <td>${p.sl || 0}</td>
      <td>${p.tp || 0}</td>
      <td>${closing
        ? '<span class="spinner"></span>'
        : `<button class="btn-sm btn-red close-pos" data-account="${esc(p.account)}" data-symbol="${esc(p.symbol)}" data-side="${p.side}" data-ticket="${p.ticket}">Close</button>`
      }</td>`;
    posTbody.appendChild(tr);
  }

  const selectAll = document.getElementById('selectAllPos');
  if (selectAll) selectAll.checked = positions.length > 0 && previouslyChecked.size === positions.length;

  updateCloseSelectedBtn();

  const ordTbody = $('#ordersTable tbody');
  const previouslyPendingChecked = new Set(
    $$('#ordersTable tbody .pending-check:checked').map(cb => cb.dataset.key)
  );
  ordTbody.innerHTML = '';
  for (const o of (data.pending_orders || [])) {
    const key = `${o.account}|${o.ticket}`;
    const checked = previouslyPendingChecked.has(key) ? ' checked' : '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="checkbox" class="pending-check" data-key="${key}" data-account="${esc(o.account)}" data-ticket="${o.ticket}"${checked}></td>
      <td>${esc(o.account)}</td>
      <td>${o.ticket}</td>
      <td>${esc(o.symbol)}</td>
      <td>${o.order_type}</td>
      <td>${o.volume}</td>
      <td>${o.price_open}</td>
      <td>${o.sl || 0}</td>
      <td>${o.tp || 0}</td>
      <td><button class="btn-sm btn-red cancel-pending-btn" data-account="${esc(o.account)}" data-ticket="${o.ticket}">Cancel</button></td>`;
    ordTbody.appendChild(tr);
  }
  const selectAllPending = document.getElementById('selectAllPending');
  if (selectAllPending) {
    selectAllPending.checked =
      (data.pending_orders || []).length > 0 &&
      previouslyPendingChecked.size === (data.pending_orders || []).length;
  }
  updateCancelPendingSelectedBtn();
}

// select-all checkbox
document.getElementById('selectAllPos').addEventListener('change', function () {
  $$('#positionsTable tbody .pos-check').forEach(cb => { cb.checked = this.checked; });
  updateCloseSelectedBtn();
});

// individual checkbox changes
$('#positionsTable').addEventListener('change', e => {
  if (e.target.classList.contains('pos-check')) updateCloseSelectedBtn();
});

document.getElementById('selectAllPending').addEventListener('change', function () {
  $$('#ordersTable tbody .pending-check').forEach(cb => { cb.checked = this.checked; });
  updateCancelPendingSelectedBtn();
});

$('#ordersTable').addEventListener('change', e => {
  if (e.target.classList.contains('pending-check')) updateCancelPendingSelectedBtn();
});

// single position close
$('#positionsTable').addEventListener('click', async e => {
  const btn = e.target.closest('.close-pos');
  if (!btn) return;
  const { account, symbol, side, ticket } = btn.dataset;
  const done = showSpinner(btn);
  $('#closeStatus').textContent = `Closing #${ticket} ${side} ${symbol} on ${account}...`;
  try {
    const res = await API.closeOrder(account, symbol, side, ticket);
    $('#closeStatus').textContent = `Closed ${res.closed_count} position(s)`;
  } catch (err) {
    $('#closeStatus').textContent = 'Close failed: ' + (err.detail || err.message || '');
  }
  done();
});

// close selected positions (parallel)
$('#closeSelectedBtn').addEventListener('click', async function () {
  const checked = $$('#positionsTable tbody .pos-check:checked');
  if (!checked.length) return;

  const done = showSpinner(this);
  $('#closeStatus').textContent = `Closing ${checked.length} position(s)...`;

  const jobs = new Map();
  for (const cb of checked) {
    const key = `${cb.dataset.account}|${cb.dataset.ticket}`;
    if (!jobs.has(key)) {
      jobs.set(key, {
        account: cb.dataset.account,
        symbol: cb.dataset.symbol,
        side: cb.dataset.side,
        ticket: cb.dataset.ticket,
      });
    }
    state.closingSet.add(cb.dataset.key);
  }

  const promises = [...jobs.values()].map(j =>
    API.closeOrder(j.account, j.symbol, j.side, j.ticket)
      .catch(err => ({ error: err.detail || err.message || String(err) }))
  );
  const results = await Promise.all(promises);

  let totalClosed = 0;
  let errors = 0;
  for (const r of results) {
    if (r.error) errors++;
    else totalClosed += (r.closed_count || 0);
  }
  state.closingSet.clear();
  $('#closeStatus').textContent = `Closed ${totalClosed} position(s)` + (errors ? `, ${errors} failed` : '');
  done();
});

// close ALL positions
$('#closeAllBtn').addEventListener('click', async function () {
  const rows = $$('#positionsTable tbody tr');
  if (!rows.length) { $('#closeStatus').textContent = 'No open positions.'; return; }

  const done = showSpinner(this);
  $('#closeStatus').textContent = `Closing all ${rows.length} position(s)...`;

  const jobs = new Map();
  for (const row of rows) {
    const cb = row.querySelector('.pos-check');
    if (!cb) continue;
    const key = `${cb.dataset.account}|${cb.dataset.ticket}`;
    if (!jobs.has(key)) {
      jobs.set(key, {
        account: cb.dataset.account,
        symbol: cb.dataset.symbol,
        side: cb.dataset.side,
        ticket: cb.dataset.ticket,
      });
    }
    state.closingSet.add(cb.dataset.key);
  }

  const promises = [...jobs.values()].map(j =>
    API.closeOrder(j.account, j.symbol, j.side, j.ticket)
      .catch(err => ({ error: err.detail || err.message || String(err) }))
  );
  const results = await Promise.all(promises);

  let totalClosed = 0;
  let errors = 0;
  for (const r of results) {
    if (r.error) errors++;
    else totalClosed += (r.closed_count || 0);
  }
  state.closingSet.clear();
  $('#closeStatus').textContent = `Closed ${totalClosed} position(s)` + (errors ? `, ${errors} failed` : '');
  done();
});

$('#ordersTable').addEventListener('click', async e => {
  const btn = e.target.closest('.cancel-pending-btn');
  if (!btn) return;
  const { account, ticket } = btn.dataset;
  const done = showSpinner(btn);
  $('#pendingStatus').textContent = `Cancelling pending #${ticket} on ${account}...`;
  try {
    await API.cancelPendingOrder(account, ticket);
    $('#pendingStatus').textContent = `Cancelled pending #${ticket}`;
  } catch (err) {
    $('#pendingStatus').textContent = 'Cancel failed: ' + (err.detail || err.message || '');
  }
  done();
});

$('#cancelSelectedPendingBtn').addEventListener('click', async function () {
  const checked = $$('#ordersTable tbody .pending-check:checked');
  if (!checked.length) return;

  const done = showSpinner(this);
  $('#pendingStatus').textContent = `Cancelling ${checked.length} pending order(s)...`;
  const jobs = new Map();
  for (const cb of checked) {
    const key = `${cb.dataset.account}|${cb.dataset.ticket}`;
    if (!jobs.has(key)) {
      jobs.set(key, { account: cb.dataset.account, ticket: cb.dataset.ticket });
    }
  }
  const results = await Promise.all(
    [...jobs.values()].map(j =>
      API.cancelPendingOrder(j.account, j.ticket).catch(err => ({ error: err.detail || err.message || String(err) }))
    )
  );
  const errors = results.filter(r => r.error).length;
  $('#pendingStatus').textContent = `Cancelled ${results.length - errors} pending order(s)` + (errors ? `, ${errors} failed` : '');
  done();
});

$('#cancelAllPendingBtn').addEventListener('click', async function () {
  const checks = $$('#ordersTable tbody .pending-check');
  if (!checks.length) {
    $('#pendingStatus').textContent = 'No pending orders.';
    return;
  }
  const done = showSpinner(this);
  $('#pendingStatus').textContent = `Cancelling all ${checks.length} pending order(s)...`;

  const jobs = new Map();
  for (const cb of checks) {
    const key = `${cb.dataset.account}|${cb.dataset.ticket}`;
    if (!jobs.has(key)) {
      jobs.set(key, { account: cb.dataset.account, ticket: cb.dataset.ticket });
    }
  }
  const results = await Promise.all(
    [...jobs.values()].map(j =>
      API.cancelPendingOrder(j.account, j.ticket).catch(err => ({ error: err.detail || err.message || String(err) }))
    )
  );
  const errors = results.filter(r => r.error).length;
  $('#pendingStatus').textContent = `Cancelled ${results.length - errors} pending order(s)` + (errors ? `, ${errors} failed` : '');
  done();
});

async function refreshHistory() {
  const account = $('#historyAccount')?.value || '';
  const daysRaw = Number($('#historyDays')?.value || 7);
  const days = Number.isFinite(daysRaw) ? Math.max(1, Math.min(daysRaw, 365)) : 7;
  $('#historyStatus').textContent = 'Loading...';
  try {
    const res = await API.closedHistory(account, days, 500);
    const tbody = $('#historyTable tbody');
    tbody.innerHTML = '';
    for (const item of (res.items || [])) {
      const tr = document.createElement('tr');
      const profit = Number(item.profit || 0);
      tr.innerHTML = `
        <td>${item.executed_at_utc || ''}</td>
        <td>${esc(item.account)}</td>
        <td>${item.deal_ticket}</td>
        <td>${item.order_ticket}</td>
        <td>${item.position_id}</td>
        <td>${esc(item.symbol)}</td>
        <td>${item.side}</td>
        <td>${item.volume}</td>
        <td>${item.price}</td>
        <td class="${profitClass(profit)}">${profit.toFixed(2)}</td>
        <td>${esc(item.comment || '')}</td>
      `;
      tbody.appendChild(tr);
    }
    $('#historyStatus').textContent = `Loaded ${(res.items || []).length} rows`;
    renderHistoryMini((res.items || []).slice(0, 50));
  } catch (err) {
    $('#historyStatus').textContent = `Error: ${err.detail || err.message || String(err)}`;
  }
}

function renderHistoryMini(items) {
  const tbody = $('#historyMiniTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  let total = 0;
  for (const item of items) {
    const profit = Number(item.profit || 0);
    total += profit;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${item.executed_at_utc || ''}</td>
      <td>${esc(item.account)}</td>
      <td>${esc(item.symbol)}</td>
      <td>${item.side}</td>
      <td>${item.volume}</td>
      <td>${item.price}</td>
      <td class="${profitClass(profit)}">${profit.toFixed(2)}</td>
    `;
    tbody.appendChild(tr);
  }
  const status = $('#historyMiniStatus');
  if (status) {
    status.textContent = `Rows: ${items.length}, Total P/L: ${total.toFixed(2)}`;
  }
}

async function refreshLogs() {
  $('#logsStatus').textContent = 'Loading...';
  try {
    const res = await API.systemLogs(20);
    setResult('logsResult', {
      folder: 'logs',
      files: res.items || [],
    });
    $('#logsStatus').textContent = `${(res.items || []).length} file(s)`;
  } catch (err) {
    $('#logsStatus').textContent = `Error: ${err.detail || err.message || String(err)}`;
  }
}

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
$('#refreshHistoryBtn').addEventListener('click', refreshHistory);
$('#refreshLogsBtn').addEventListener('click', refreshLogs);
const historyMiniBtn = $('#refreshHistoryMiniBtn');
if (historyMiniBtn) historyMiniBtn.addEventListener('click', refreshHistory);

// ── Boot ───────────────────────────────────────────────────
(async function boot() {
  await loadAccounts();
  refreshLicense();
  refreshLogs();
  connectWS();
  createOrderRow();
  refreshHistory();
})();
