const api = {
  getAccounts: () => fetch("/api/accounts").then(r => r.json()),
  upsertAccount: (body) => fetch("/api/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(r => r.json()),
  deleteAccount: (name) => fetch(`/api/accounts/${encodeURIComponent(name)}`, { method: "DELETE" }).then(r => r.json()),
  healthcheckAll: () => fetch("/api/healthcheck", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  }).then(r => r.json()),
  healthcheckOne: (name) => fetch(`/api/healthcheck/${encodeURIComponent(name)}`).then(r => r.json()),
  submitPlan: (planRows) => fetch("/api/trade/submit-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_rows: planRows, timeout_seconds: 3600, poll_seconds: 1.0 })
  }).then(r => r.json()),
  quickMulti: (body) => fetch("/api/trade/quick-multi", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(r => r.json()),
  getBook: () => fetch("/api/orders/active").then(r => r.json()),
  closeOrder: (account, symbol, side) => fetch("/api/orders/close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account, symbol, side })
  }).then(r => r.json()),
  licenseStatus: () => fetch("/api/license/status").then(r => r.json()),
  activateLicense: (path) => fetch("/api/license/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ license_key_path: path })
  }).then(r => r.json())
};

const state = {
  healthByAccount: {}
};

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function setJson(id, value) {
  document.getElementById(id).textContent = JSON.stringify(value, null, 2);
}

async function refreshAccounts() {
  const data = await api.getAccounts();
  const body = document.querySelector("#accountsTable tbody");
  body.innerHTML = "";
  for (const acc of data) {
    const tr = document.createElement("tr");
    const health = state.healthByAccount[acc.name] || "-";
    tr.innerHTML = `
      <td><input type="checkbox" class="acc-check" data-name="${acc.name}"></td>
      <td>${acc.name}</td>
      <td>${acc.mt5_login}</td>
      <td>${acc.mt5_server}</td>
      <td>${health}</td>
      <td>
        <button data-name="${acc.name}" class="hc-btn">Health</button>
        <button data-name="${acc.name}" class="del-btn">Delete</button>
      </td>
    `;
    body.appendChild(tr);
  }
  body.querySelectorAll(".hc-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const res = await api.healthcheckOne(btn.dataset.name);
      state.healthByAccount[btn.dataset.name] = res.ok ? "OK" : `FAIL: ${res.error || "-"}`;
      await refreshAccounts();
    });
  });
  body.querySelectorAll(".del-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api.deleteAccount(btn.dataset.name);
      await refreshAccounts();
    });
  });
}

function renderBook(book) {
  setText("totalProfit", Number(book.total_profit || 0).toFixed(2));
  const posBody = document.querySelector("#positionsTable tbody");
  const ordBody = document.querySelector("#ordersTable tbody");
  posBody.innerHTML = "";
  ordBody.innerHTML = "";

  for (const p of book.positions || []) {
    const tr = document.createElement("tr");
    const key = `${p.account}||${p.symbol}`;
    tr.innerHTML = `
      <td><input type="checkbox" class="pos-check" data-key="${key}" data-account="${p.account}" data-symbol="${p.symbol}"></td>
      <td>${p.account}</td>
      <td>${p.ticket}</td>
      <td>${p.symbol}</td>
      <td>${p.side}</td>
      <td>${p.volume}</td>
      <td>${Number(p.profit).toFixed(2)}</td>
      <td><button class="close-btn" data-account="${p.account}" data-symbol="${p.symbol}" data-side="${p.side}">Close</button></td>
    `;
    posBody.appendChild(tr);
  }

  for (const o of book.pending_orders || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${o.account}</td>
      <td>${o.ticket}</td>
      <td>${o.symbol}</td>
      <td>${o.order_type}</td>
      <td>${o.volume}</td>
      <td>${o.price_open}</td>
    `;
    ordBody.appendChild(tr);
  }

  posBody.querySelectorAll(".close-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const res = await api.closeOrder(btn.dataset.account, btn.dataset.symbol, btn.dataset.side);
      alert(`Closed: ${res.closed_count}`);
    });
  });
}

function selectedAccounts() {
  return Array.from(document.querySelectorAll(".acc-check:checked")).map(i => i.dataset.name);
}

async function refreshBook() {
  const book = await api.getBook();
  renderBook(book);
}

function connectRealtime() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${window.location.host}/ws/realtime`);
  ws.onmessage = (evt) => {
    const payload = JSON.parse(evt.data);
    if (payload.type === "snapshot") {
      renderBook(payload.data);
    }
  };
  ws.onclose = () => setTimeout(connectRealtime, 1000);
}

async function refreshLicense() {
  const status = await api.licenseStatus();
  setJson("licenseResult", status);
  setText("licenseBadge", `License: ${status.status}`);
}

document.getElementById("accountForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const payload = {
    name: String(form.get("name") || ""),
    mt5_login: Number(form.get("mt5_login")),
    mt5_password: String(form.get("mt5_password") || ""),
    mt5_server: String(form.get("mt5_server") || ""),
    mt5_path: String(form.get("mt5_path") || ""),
    mt5_portable: form.get("mt5_portable") === "on"
  };
  await api.upsertAccount(payload);
  e.target.reset();
  await refreshAccounts();
});

document.getElementById("refreshAccountsBtn").addEventListener("click", refreshAccounts);
document.getElementById("refreshBookBtn").addEventListener("click", refreshBook);
document.getElementById("refreshLicenseBtn").addEventListener("click", refreshLicense);
document.getElementById("healthcheckAllBtn").addEventListener("click", async () => {
  const res = await api.healthcheckAll();
  state.healthByAccount = {};
  for (const row of res.results || []) {
    state.healthByAccount[row.name] = row.ok ? "OK" : `FAIL: ${row.error || "-"}`;
  }
  await refreshAccounts();
});

document.getElementById("submitPlanBtn").addEventListener("click", async () => {
  try {
    const raw = document.getElementById("planJson").value;
    const planRows = JSON.parse(raw);
    const res = await api.submitPlan(planRows);
    setJson("planResult", res);
  } catch (err) {
    setJson("planResult", { ok: false, error: String(err) });
  }
});

document.getElementById("quickOrderForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const accounts = selectedAccounts();
  if (!accounts.length) {
    setJson("quickOrderResult", { ok: false, error: "Select at least one account" });
    return;
  }
  const form = new FormData(e.target);
  const body = {
    accounts,
    symbol: String(form.get("symbol") || ""),
    side: String(form.get("side") || "buy"),
    volume: Number(form.get("volume")),
    comment: String(form.get("comment") || "")
  };
  const trigger = String(form.get("trigger_price") || "").trim();
  const sl = String(form.get("sl_price") || "").trim();
  const tp = String(form.get("tp_price") || "").trim();
  if (trigger) body.trigger_price = Number(trigger);
  if (sl) body.sl_price = Number(sl);
  if (tp) body.tp_price = Number(tp);
  const res = await api.quickMulti(body);
  setJson("quickOrderResult", res);
});

document.getElementById("closeSelectedBtn").addEventListener("click", async () => {
  const selected = Array.from(document.querySelectorAll(".pos-check:checked"));
  if (!selected.length) {
    alert("Select at least one position row");
    return;
  }
  const uniq = new Map();
  for (const s of selected) {
    const key = `${s.dataset.account}||${s.dataset.symbol}`;
    if (!uniq.has(key)) {
      uniq.set(key, { account: s.dataset.account, symbol: s.dataset.symbol });
    }
  }
  const results = [];
  for (const row of uniq.values()) {
    const res = await api.closeOrder(row.account, row.symbol, "all");
    results.push(res);
  }
  alert(`Close requests sent: ${results.length}`);
});

document.getElementById("activateLicenseBtn").addEventListener("click", async () => {
  const path = document.getElementById("licensePath").value;
  const res = await api.activateLicense(path);
  setJson("licenseResult", res);
  await refreshLicense();
});

refreshAccounts();
refreshBook();
refreshLicense();
connectRealtime();
