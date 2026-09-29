/* Mis Finanzas - frontend (JS puro, sin build) */
(() => {
  "use strict";

  // ------------------------------------------------------------------ estado
  const state = {
    month: currentMonth(),
    view: "dashboard",
    settings: { currency: "COP" },
    categories: [],
    cards: [],
    txFilters: { type: "", category_id: "", card_id: "", payment_method: "", q: "" },
  };
  const charts = [];

  const VIEWS = {
    dashboard: { title: "Dashboard", render: renderDashboard },
    movimientos: { title: "Movimientos", render: renderTransactions },
    tarjetas: { title: "Tarjetas de crédito", render: renderCards },
    presupuesto: { title: "Categorías y presupuesto", render: renderBudget },
    fijos: { title: "Gastos e ingresos fijos", render: renderRecurring },
    metas: { title: "Metas de ahorro", render: renderGoals },
    ajustes: { title: "Ajustes", render: renderSettings },
  };

  const TX_TYPES = { expense: "Gasto", income: "Ingreso", card_payment: "Pago tarjeta" };
  const METHODS = { cash: "Efectivo", debit: "Débito", transfer: "Transferencia", card: "Tarjeta de crédito" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const content = $("#content");

  // --------------------------------------------------------------- utilidades
  function currentMonth() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function shiftMonth(month, delta) {
    const [y, m] = month.split("-").map(Number);
    const total = y * 12 + (m - 1) + delta;
    return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
  }
  function monthName(month, short = false) {
    const [y, m] = month.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("es-CO",
      short ? { month: "short", year: "2-digit" } : { month: "long", year: "numeric" });
  }
  function defaultDateForMonth(month) {
    return month === currentMonth() ? today() : `${month}-01`;
  }
  function money(v, compact = false) {
    const opts = { style: "currency", currency: state.settings.currency || "COP", maximumFractionDigits: 0 };
    if (compact) Object.assign(opts, { notation: "compact", maximumFractionDigits: 1 });
    try { return new Intl.NumberFormat("es-CO", opts).format(v || 0); }
    catch { return `$${Math.round(v || 0).toLocaleString("es-CO")}`; }
  }
  function pct(v) { return `${(v || 0).toLocaleString("es-CO", { maximumFractionDigits: 1 })}%`; }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmtDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("es-CO", { day: "2-digit", month: "short" });
  }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
  function storageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } }

  async function api(method, path, data) {
    const res = await fetch(path, {
      method,
      headers: data ? { "Content-Type": "application/json" } : {},
      body: data ? JSON.stringify(data) : undefined,
    });
    if (res.status === 204) return null;
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || `Error ${res.status}`);
    return json;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = `toast show${isError ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.className = "toast"), 2600);
  }

  function destroyCharts() { while (charts.length) charts.pop().destroy(); }

  function meterClass(ratio) { return ratio >= 1 ? "bad" : ratio >= 0.85 ? "warn" : ""; }
  function utilClass(u) { return u >= 90 ? "bad" : u >= 70 ? "warn" : ""; }

  async function loadRefs() {
    const [cats, cards] = await Promise.all([
      api("GET", `/api/categories?month=${state.month}`),
      api("GET", `/api/cards?month=${state.month}`),
    ]);
    state.categories = cats;
    state.cards = cards;
  }

  // ---------------------------------------------------------------- navegación
  function setMonth(month) {
    state.month = month;
    render();
  }

  function updateMonthNav() {
    const label = monthName(state.month);
    $("#monthLabel").textContent = label.charAt(0).toUpperCase() + label.slice(1);
    $("#monthInput").value = state.month;
    $("#monthNav").style.visibility = state.view === "ajustes" || state.view === "metas" ? "hidden" : "visible";
  }

  async function render() {
    const view = VIEWS[state.view] ? state.view : "dashboard";
    state.view = view;
    document.querySelectorAll(".sidebar nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === view));
    $("#viewTitle").textContent = VIEWS[view].title;
    document.title = `${VIEWS[view].title} · Mis Finanzas`;
    updateMonthNav();
    destroyCharts();
    try {
      await loadRefs();
      await VIEWS[view].render();
    } catch (err) {
      content.innerHTML = `<div class="panel empty"><div class="big">⚠️</div><p>${esc(err.message)}</p></div>`;
    }
  }

  function routeFromHash() {
    state.view = (location.hash || "#dashboard").slice(1);
    $("#sidebar").classList.remove("open");
    render();
  }

  // ------------------------------------------------------------- formularios
  /**
   * fields: [{ name, label, type, options, full, hint, showIf(values), required, step, min }]
   * options puede ser un arreglo [{value,label}] o una función (values) => arreglo.
   */
  function openForm({ title, fields, values = {}, submitLabel = "Guardar", onSubmit }) {
    const dialog = $("#modal");
    const bodyEl = $("#modalBody");
    $("#modalTitle").textContent = title;
    $("#modalSubmit").textContent = submitLabel;
    $("#modalError").textContent = "";
    bodyEl.innerHTML = "";

    const current = { ...values };
    const inputs = {};

    fields.forEach((f) => {
      const wrap = document.createElement("div");
      wrap.className = `field${f.full ? " full" : ""}${f.type === "checkbox" ? " check" : ""}`;
      wrap.dataset.name = f.name;
      const id = `f_${f.name}`;
      let html = "";
      if (f.type === "checkbox") {
        html = `<input type="checkbox" id="${id}"><label for="${id}">${esc(f.label)}</label>`;
      } else {
        html = `<label for="${id}">${esc(f.label)}</label>`;
        if (f.type === "select") html += `<select id="${id}"></select>`;
        else if (f.type === "textarea") html += `<textarea id="${id}" rows="2"></textarea>`;
        else if (f.type === "seg") {
          html += `<div class="seg" id="${id}">${f.options.map((o) =>
            `<label><input type="radio" name="${f.name}" value="${esc(o.value)}">${esc(o.label)}</label>`).join("")}</div>`;
        } else {
          html += `<input id="${id}" type="${f.type || "text"}"${f.step ? ` step="${f.step}"` : ""}${f.min !== undefined ? ` min="${f.min}"` : ""}${f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : ""}>`;
        }
        if (f.hint) html += `<span class="hint">${esc(f.hint)}</span>`;
      }
      wrap.innerHTML = html;
      bodyEl.appendChild(wrap);
      inputs[f.name] = { field: f, wrap, el: wrap.querySelector(`#${id}`) };
    });

    function readValue(name) {
      const { field, el, wrap } = inputs[name];
      if (field.type === "checkbox") return el.checked;
      if (field.type === "seg") return (wrap.querySelector("input:checked") || {}).value || "";
      return el.value;
    }
    function writeValue(name, value) {
      const { field, el, wrap } = inputs[name];
      if (field.type === "checkbox") el.checked = !!value;
      else if (field.type === "seg") {
        wrap.querySelectorAll("input").forEach((i) => (i.checked = i.value === String(value ?? "")));
      } else el.value = value ?? "";
    }
    function fillOptions(name) {
      const { field, el } = inputs[name];
      if (field.type !== "select") return;
      const opts = typeof field.options === "function" ? field.options(current) : field.options;
      const prev = current[name] ?? "";
      el.innerHTML = opts.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
      const valid = opts.some((o) => String(o.value) === String(prev));
      el.value = valid ? String(prev) : (opts[0] ? String(opts[0].value) : "");
      current[name] = el.value;
    }
    function refresh() {
      fields.forEach((f) => {
        fillOptions(f.name);
        inputs[f.name].wrap.style.display = !f.showIf || f.showIf(current) ? "" : "none";
      });
    }

    fields.forEach((f) => {
      if (f.type !== "select") writeValue(f.name, current[f.name]);
    });
    refresh();
    fields.forEach((f) => {
      const handler = () => { current[f.name] = readValue(f.name); refresh(); };
      inputs[f.name].wrap.addEventListener("change", handler);
      inputs[f.name].wrap.addEventListener("input", () => { current[f.name] = readValue(f.name); });
    });

    const form = $("#modalForm");
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const data = {};
      fields.forEach((f) => {
        if (f.showIf && !f.showIf(current)) return;
        data[f.name] = readValue(f.name);
      });
      $("#modalSubmit").disabled = true;
      try {
        await onSubmit(data);
        dialog.close();
        render();
      } catch (err) {
        $("#modalError").textContent = err.message;
      } finally {
        $("#modalSubmit").disabled = false;
      }
    };
    dialog.showModal();
    const first = bodyEl.querySelector("input:not([type=radio]):not([type=checkbox]), select");
    if (first) first.focus();
  }

  function confirmAction(message) { return window.confirm(message); }

  // ------------------------------------------------------ formularios de negocio
  function categoryOptions(type, withEmpty = true) {
    const opts = state.categories
      .filter((c) => c.type === type && c.active)
      .map((c) => ({ value: c.id, label: `${c.icon} ${c.name}`.trim() }));
    return withEmpty ? [{ value: "", label: "— Sin categoría —" }, ...opts] : opts;
  }
  function cardOptions(includeId) {
    return state.cards
      .filter((c) => c.active || c.id === includeId)
      .map((c) => ({ value: c.id, label: `${c.name}${c.last4 ? ` •${c.last4}` : ""}` }));
  }

  function openTransactionForm(tx, preset = {}) {
    if (!tx && state.cards.length === 0 && preset.type === "card_payment") {
      toast("Primero registra una tarjeta", true);
      return;
    }
    const values = tx ? { ...tx } : {
      type: "expense", date: defaultDateForMonth(state.month), payment_method: "debit", installments: 1, ...preset,
    };
    openForm({
      title: tx ? "Editar movimiento" : "Nuevo movimiento",
      values,
      fields: [
        { name: "type", label: "Tipo", type: "seg", full: true,
          options: Object.entries(TX_TYPES).map(([value, label]) => ({ value, label })) },
        { name: "amount", label: "Monto", type: "number", step: "any", min: 0, placeholder: "0" },
        { name: "date", label: "Fecha", type: "date" },
        { name: "description", label: "Descripción", type: "text", full: true, placeholder: "Ej: Mercado del mes" },
        { name: "category_id", label: "Categoría", type: "select", full: true,
          options: (v) => categoryOptions(v.type === "income" ? "income" : "expense"),
          showIf: (v) => v.type !== "card_payment" },
        { name: "payment_method", label: "Medio de pago", type: "select",
          options: (v) => Object.entries(METHODS)
            .filter(([k]) => v.type === "expense" || k !== "card")
            .map(([value, label]) => ({ value, label })) },
        { name: "card_id", label: "Tarjeta", type: "select",
          options: () => cardOptions(tx && tx.card_id),
          showIf: (v) => v.type === "card_payment" || (v.type === "expense" && v.payment_method === "card") },
        { name: "installments", label: "Número de cuotas", type: "number", min: 1, step: 1,
          showIf: (v) => v.type === "expense" && v.payment_method === "card" },
        { name: "notes", label: "Notas", type: "textarea", full: true },
      ],
      onSubmit: async (data) => {
        if (tx) await api("PUT", `/api/transactions/${tx.id}`, data);
        else await api("POST", "/api/transactions", data);
        toast(tx ? "Movimiento actualizado" : "Movimiento registrado");
      },
    });
  }

  function openCardForm(card) {
    openForm({
      title: card ? "Editar tarjeta" : "Nueva tarjeta de crédito",
      values: card ? { ...card, active: !!card.active } : { color: "#2a78d6", active: true, initial_balance: 0 },
      fields: [
        { name: "name", label: "Nombre", type: "text", placeholder: "Ej: Visa Oro" },
        { name: "bank", label: "Banco / franquicia", type: "text", placeholder: "Ej: Bancolombia" },
        { name: "credit_limit", label: "Cupo total", type: "number", step: "any", min: 0 },
        { name: "initial_balance", label: "Deuda actual al registrarla", type: "number", step: "any", min: 0,
          hint: "Lo que ya debías antes de empezar a usar la app" },
        { name: "cut_day", label: "Día de corte", type: "number", min: 1, step: 1 },
        { name: "due_day", label: "Día límite de pago", type: "number", min: 1, step: 1 },
        { name: "last4", label: "Últimos 4 dígitos", type: "text", placeholder: "1234" },
        { name: "interest_rate", label: "Tasa de interés mensual (%)", type: "number", step: "any", min: 0 },
        { name: "color", label: "Color", type: "color" },
        { name: "active", label: "Tarjeta activa", type: "checkbox" },
      ],
      onSubmit: async (data) => {
        if (card) await api("PUT", `/api/cards/${card.id}`, data);
        else await api("POST", "/api/cards", data);
        toast(card ? "Tarjeta actualizada" : "Tarjeta creada");
      },
    });
  }

  function openCategoryForm(cat, type = "expense") {
    openForm({
      title: cat ? "Editar categoría" : "Nueva categoría",
      values: cat ? { ...cat, active: !!cat.active } : { type, color: "#2a78d6", icon: "", active: true, default_budget: 0 },
      fields: [
        { name: "type", label: "Tipo", type: "seg", full: true,
          options: [{ value: "expense", label: "Gasto" }, { value: "income", label: "Ingreso" }] },
        { name: "name", label: "Nombre", type: "text" },
        { name: "icon", label: "Ícono (emoji)", type: "text", placeholder: "🛒" },
        { name: "default_budget", label: "Presupuesto mensual por defecto", type: "number", step: "any", min: 0,
          full: true, hint: "Se usa en todos los meses salvo que definas uno específico",
          showIf: (v) => v.type === "expense" },
        { name: "color", label: "Color", type: "color" },
        { name: "active", label: "Activa", type: "checkbox" },
      ],
      onSubmit: async (data) => {
        if (cat) await api("PUT", `/api/categories/${cat.id}`, data);
        else await api("POST", "/api/categories", data);
        toast("Categoría guardada");
      },
    });
  }

  function openRecurringForm(rec) {
    openForm({
      title: rec ? "Editar fijo" : "Nuevo gasto/ingreso fijo",
      values: rec ? { ...rec, active: !!rec.active } : { type: "expense", day: 1, payment_method: "debit", active: true },
      fields: [
        { name: "type", label: "Tipo", type: "seg", full: true,
          options: [{ value: "expense", label: "Gasto" }, { value: "income", label: "Ingreso" }] },
        { name: "description", label: "Descripción", type: "text", full: true, placeholder: "Ej: Arriendo" },
        { name: "amount", label: "Monto", type: "number", step: "any", min: 0 },
        { name: "day", label: "Día del mes", type: "number", min: 1, step: 1 },
        { name: "category_id", label: "Categoría", type: "select", full: true,
          options: (v) => categoryOptions(v.type) },
        { name: "payment_method", label: "Medio de pago", type: "select",
          options: (v) => Object.entries(METHODS)
            .filter(([k]) => v.type === "expense" || k !== "card")
            .map(([value, label]) => ({ value, label })) },
        { name: "card_id", label: "Tarjeta", type: "select", options: () => cardOptions(rec && rec.card_id),
          showIf: (v) => v.type === "expense" && v.payment_method === "card" },
        { name: "active", label: "Activo", type: "checkbox" },
      ],
      onSubmit: async (data) => {
        if (rec) await api("PUT", `/api/recurring/${rec.id}`, data);
        else await api("POST", "/api/recurring", data);
        toast("Guardado");
      },
    });
  }

  function openGoalForm(goal) {
    openForm({
      title: goal ? "Editar meta" : "Nueva meta de ahorro",
      values: goal ? { ...goal } : { color: "#1baf7a", saved: 0 },
      fields: [
        { name: "name", label: "Nombre", type: "text", full: true, placeholder: "Ej: Fondo de emergencia" },
        { name: "target", label: "Monto objetivo", type: "number", step: "any", min: 0 },
        { name: "saved", label: "Ahorrado hasta hoy", type: "number", step: "any", min: 0 },
        { name: "deadline", label: "Fecha objetivo (opcional)", type: "date" },
        { name: "color", label: "Color", type: "color" },
      ],
      onSubmit: async (data) => {
        if (goal) await api("PUT", `/api/goals/${goal.id}`, data);
        else await api("POST", "/api/goals", data);
        toast("Meta guardada");
      },
    });
  }

  // ---------------------------------------------------------------- dashboard
  function deltaHtml(cur, prev, goodWhenUp) {
    if (!prev) return `<span class="muted">Sin datos del mes anterior</span>`;
    const change = ((cur - prev) / Math.abs(prev)) * 100;
    if (!isFinite(change) || Math.abs(change) < 0.05) return `<span class="muted">Igual que el mes anterior</span>`;
    const up = change > 0;
    const good = up === goodWhenUp;
    return `<span class="${good ? "pos" : "neg"}">${up ? "▲" : "▼"} ${pct(Math.abs(change))}</span> vs mes anterior`;
  }

  async function renderDashboard() {
    const d = await api("GET", `/api/dashboard?month=${state.month}`);
    const t = d.totals;
    const ct = d.card_totals;
    const hasData = t.count > 0;

    const kpis = [
      { label: "Ingresos", value: money(t.income), delta: deltaHtml(t.income, d.previous.income, true) },
      { label: "Gastos", value: money(t.expense), delta: deltaHtml(t.expense, d.previous.expense, false) },
      { label: "Balance del mes", value: `<span class="${t.balance < 0 ? "neg" : ""}">${money(t.balance)}</span>`,
        delta: t.income ? `Tasa de ahorro: <b>${pct(t.savings_rate)}</b>` : `<span class="muted">Registra tus ingresos</span>` },
      { label: "Presupuesto usado", value: d.budget.total ? pct(d.budget.spent / d.budget.total * 100) : "—",
        delta: d.budget.total ? `${money(d.budget.spent)} de ${money(d.budget.total)}` : `<a href="#presupuesto">Define tu presupuesto</a>` },
      { label: "Deuda en tarjetas", value: money(ct.balance),
        delta: ct.limit ? `Uso del cupo: <b class="${utilClass(ct.utilization) === "bad" ? "neg" : ""}">${pct(ct.utilization)}</b>` : `<a href="#tarjetas">Agrega una tarjeta</a>` },
      { label: "Cupo disponible", value: money(ct.available),
        delta: ct.limit ? `De un cupo total de ${money(ct.limit)}` : "" },
    ];

    const alerts = d.alerts.map((a) => `<div class="alert ${a.level}">${esc(a.text)}</div>`).join("");

    const maxCat = Math.max(1, ...d.by_category.map((c) => Math.max(c.spent, c.budget)));
    const catRows = d.by_category.length ? d.by_category.map((c) => {
      const ratio = c.budget ? c.spent / c.budget : 0;
      return `<div class="bar-row">
        <div class="bar-top"><span class="bar-name"><span class="dot" style="background:${esc(c.color)}"></span>${esc(c.icon)} ${esc(c.name)}</span>
        <span class="num"><b>${money(c.spent)}</b>${c.budget ? ` <span class="muted">/ ${money(c.budget)}</span>` : ""}</span></div>
        <div class="meter ${c.budget ? meterClass(ratio) : ""}" title="${c.budget ? `${pct(ratio * 100)} del presupuesto` : "Sin presupuesto"}">
          <span style="width:${(c.spent / maxCat) * 100}%"></span>
          ${c.budget ? `<i class="mark" style="left:calc(${Math.min(c.budget / maxCat, 1) * 100}% - 1px)" title="Presupuesto"></i>` : ""}
        </div></div>`;
    }).join("") : `<div class="empty">Aún no hay gastos este mes</div>`;

    const cardRows = d.cards.length ? d.cards.map((c) => `<div class="bar-row">
        <div class="bar-top"><span class="bar-name"><span class="dot" style="background:${esc(c.color)}"></span>${esc(c.name)}</span>
        <span class="num"><b>${money(c.available)}</b> <span class="muted">disp.</span></span></div>
        <div class="meter ${utilClass(c.utilization)}" title="Uso ${pct(c.utilization)}"><span style="width:${Math.min(c.utilization, 100)}%"></span></div>
        <div class="bar-top muted" style="margin-top:4px;font-size:12px"><span>Usado ${money(c.balance)} (${pct(c.utilization)})</span>
        <span>${c.due_date ? `Pago: ${fmtDate(c.due_date)}` : ""}</span></div></div>`).join("")
      : `<div class="empty">No tienes tarjetas registradas.<br><br><button class="btn btn-sm" data-act="new-card">+ Agregar tarjeta</button></div>`;

    const methodTotal = d.by_method.reduce((s, m) => s + m.total, 0) || 1;
    const methodRows = d.by_method.length ? d.by_method.map((m) => `<div class="bar-row">
        <div class="bar-top"><span>${esc(METHODS[m.payment_method] || m.payment_method)}</span>
        <span class="num"><b>${money(m.total)}</b> <span class="muted">${pct(m.total / methodTotal * 100)}</span></span></div>
        <div class="meter"><span style="width:${m.total / methodTotal * 100}%"></span></div></div>`).join("")
      : `<div class="empty">Sin gastos este mes</div>`;

    const topRows = d.top_expenses.length ? `<table><tbody>${d.top_expenses.map((x) => `<tr>
        <td><div>${esc(x.description || x.category_name || "Gasto")}</div>
        <div class="muted" style="font-size:12px">${fmtDate(x.date)} · ${esc(x.category_icon || "")} ${esc(x.category_name || "Sin categoría")}${x.card_name ? ` · 💳 ${esc(x.card_name)}` : ""}</div></td>
        <td class="right num"><b>${money(x.amount)}</b></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">Sin gastos este mes</div>`;

    content.innerHTML = `
      ${!hasData ? `<div class="alert info mb">👋 No hay movimientos en ${esc(monthName(state.month))}.
        ${d.pending_recurring ? `<a href="#" data-act="gen-recurring">Registrar tus ${d.pending_recurring} fijos</a> o ` : ""}
        <a href="#" data-act="new-tx">agrega tu primer movimiento</a>.</div>` : ""}
      <div class="kpis">${kpis.map((k) => `<div class="panel kpi"><div class="label">${k.label}</div>
        <div class="value num">${k.value}</div><div class="delta">${k.delta}</div></div>`).join("")}</div>
      ${alerts ? `<div class="alerts">${alerts}</div>` : ""}
      <div class="grid grid-2 mb">
        <div class="panel"><h3>Ingresos vs gastos</h3><p class="sub">Últimos 12 meses</p>
          <div class="legend"><span><i class="box" style="background:var(--series-1)"></i>Ingresos</span><span><i class="box" style="background:var(--series-2)"></i>Gastos</span></div>
          <div class="chart-box"><canvas id="trendChart" role="img" aria-label="Ingresos y gastos de los últimos 12 meses"></canvas></div></div>
        <div class="panel"><h3>Ritmo de gasto del mes</h3><p class="sub">Gasto acumulado día a día comparado con el mes anterior</p>
          <div class="legend"><span><i style="background:var(--series-1)"></i>${esc(monthName(state.month))}</span><span><i style="background:var(--muted)"></i>${esc(monthName(shiftMonth(state.month, -1)))}</span>${d.budget.total ? `<span><i style="background:var(--bad);height:2px"></i>Presupuesto</span>` : ""}</div>
          <div class="chart-box"><canvas id="dailyChart" role="img" aria-label="Gasto acumulado por día"></canvas></div></div>
      </div>
      <div class="grid grid-2 mb">
        <div class="panel"><div class="panel-head"><div><h3>Gasto por categoría</h3><p class="sub">La línea vertical marca el presupuesto de cada categoría</p></div>
          <a class="btn btn-sm btn-ghost" href="#presupuesto">Editar</a></div><div class="bar-list">${catRows}</div></div>
        <div class="stack">
          <div class="panel"><div class="panel-head"><div><h3>Tarjetas de crédito</h3><p class="sub">Cupo disponible al cierre del mes</p></div>
            <a class="btn btn-sm btn-ghost" href="#tarjetas">Ver</a></div><div class="bar-list">${cardRows}</div></div>
          <div class="panel"><h3>Medios de pago</h3><p class="sub">Cómo pagaste tus gastos este mes</p><div class="bar-list">${methodRows}</div></div>
        </div>
      </div>
      <div class="panel"><div class="panel-head"><div><h3>Gastos más grandes del mes</h3><p class="sub">Top 5</p></div>
        <a class="btn btn-sm btn-ghost" href="#movimientos">Ver todos</a></div><div class="table-wrap">${topRows}</div></div>
    `;

    drawTrendChart(d.trend);
    drawDailyChart(d.daily, d.budget.total);
  }

  function chartBase() {
    const muted = cssVar("--muted");
    const grid = cssVar("--grid");
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${money(ctx.parsed.y)}` },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkip: true }, border: { color: grid } },
        y: { beginAtZero: true, grid: { color: grid }, border: { display: false },
             ticks: { color: muted, callback: (v) => money(v, true), maxTicksLimit: 6 } },
      },
    };
  }

  function drawTrendChart(trend) {
    const el = $("#trendChart");
    if (!el || !window.Chart) return;
    charts.push(new Chart(el, {
      type: "bar",
      data: {
        labels: trend.map((t) => monthName(t.month, true)),
        datasets: [
          { label: "Ingresos", data: trend.map((t) => t.income), backgroundColor: cssVar("--series-1"),
            borderRadius: 4, borderSkipped: "bottom", maxBarThickness: 18 },
          { label: "Gastos", data: trend.map((t) => t.expense), backgroundColor: cssVar("--series-2"),
            borderRadius: 4, borderSkipped: "bottom", maxBarThickness: 18 },
        ],
      },
      options: { ...chartBase(),
        datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.9 } },
        onClick: (_e, els) => { if (els.length) setMonth(trend[els[0].index].month); },
      },
    }));
  }

  function drawDailyChart(daily, budget) {
    const el = $("#dailyChart");
    if (!el || !window.Chart) return;
    const days = Math.max(daily.current.length, daily.previous.length);
    const isCurrent = state.month === currentMonth();
    const todayDay = new Date().getDate();
    const current = daily.current.map((v, i) => (isCurrent && i + 1 > todayDay ? null : v));
    const datasets = [
      { label: monthName(state.month), data: current, borderColor: cssVar("--series-1"), backgroundColor: cssVar("--series-1"),
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
      { label: monthName(shiftMonth(state.month, -1)), data: daily.previous, borderColor: cssVar("--muted"),
        backgroundColor: cssVar("--muted"), borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
    ];
    if (budget) {
      datasets.push({ label: "Presupuesto", data: Array(days).fill(budget), borderColor: cssVar("--bad"),
        backgroundColor: cssVar("--bad"), borderWidth: 1.5, borderDash: [2, 3], pointRadius: 0, pointHoverRadius: 0 });
    }
    charts.push(new Chart(el, {
      type: "line",
      data: { labels: Array.from({ length: days }, (_, i) => i + 1), datasets },
      options: { ...chartBase(),
        plugins: { ...chartBase().plugins,
          tooltip: { callbacks: {
            title: (items) => `Día ${items[0].label}`,
            label: (ctx) => ` ${ctx.dataset.label}: ${money(ctx.parsed.y)}` } } },
      },
    }));
  }

  // ------------------------------------------------------------- movimientos
  async function renderTransactions() {
    const f = state.txFilters;
    const qs = new URLSearchParams({ month: state.month });
    Object.entries(f).forEach(([k, v]) => { if (v) qs.set(k, v); });
    const list = await api("GET", `/api/transactions?${qs}`);

    const sum = (type) => list.filter((t) => t.type === type).reduce((s, t) => s + t.amount, 0);
    const catOpts = [{ value: "", label: "Todas las categorías" }, { value: "none", label: "Sin categoría" },
      ...state.categories.map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))];
    const opt = (arr, sel) => arr.map((o) => `<option value="${esc(o.value)}"${String(o.value) === String(sel) ? " selected" : ""}>${esc(o.label)}</option>`).join("");

    content.innerHTML = `
      <div class="toolbar">
        <input type="search" id="fq" placeholder="Buscar..." value="${esc(f.q)}">
        <select id="ftype">${opt([{ value: "", label: "Todos los tipos" }, ...Object.entries(TX_TYPES).map(([value, label]) => ({ value, label }))], f.type)}</select>
        <select id="fcat">${opt(catOpts, f.category_id)}</select>
        <select id="fcard">${opt([{ value: "", label: "Todas las tarjetas" }, ...state.cards.map((c) => ({ value: c.id, label: c.name }))], f.card_id)}</select>
        <select id="fmethod">${opt([{ value: "", label: "Todos los medios" }, ...Object.entries(METHODS).map(([value, label]) => ({ value, label }))], f.payment_method)}</select>
        <span class="grow"></span>
        <a class="btn" href="/api/transactions/export.csv?${qs}" download>⬇ Exportar CSV</a>
        <button class="btn btn-primary" data-act="new-tx">+ Movimiento</button>
      </div>
      <div class="summary-strip">
        <span>${list.length} movimiento(s)</span>
        <span>Ingresos: <b class="num">${money(sum("income"))}</b></span>
        <span>Gastos: <b class="num">${money(sum("expense"))}</b></span>
        <span>Pagos a tarjetas: <b class="num">${money(sum("card_payment"))}</b></span>
      </div>
      <div class="panel" style="padding:0">
        ${list.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Fecha</th><th>Descripción</th><th>Categoría</th><th>Tipo</th><th>Medio</th><th class="right">Monto</th><th></th></tr></thead>
          <tbody>${list.map((t) => `<tr>
            <td class="num">${fmtDate(t.date)}</td>
            <td>${esc(t.description) || '<span class="muted">—</span>'}${t.notes ? `<div class="muted" style="font-size:12px">${esc(t.notes)}</div>` : ""}</td>
            <td>${t.category_name ? `<span class="tag"><span class="dot" style="background:${esc(t.category_color)}"></span>${esc(t.category_icon)} ${esc(t.category_name)}</span>` : '<span class="muted">—</span>'}</td>
            <td><span class="pill ${t.type}">${TX_TYPES[t.type]}</span></td>
            <td>${esc(METHODS[t.payment_method] || t.payment_method)}${t.card_name ? `<div class="muted" style="font-size:12px">💳 ${esc(t.card_name)}${t.installments > 1 ? ` · ${t.installments} cuotas` : ""}</div>` : ""}</td>
            <td class="right num"><b class="${t.type === "income" ? "pos" : ""}">${t.type === "income" ? "+" : t.type === "expense" ? "−" : ""}${money(t.amount)}</b></td>
            <td class="row-actions">
              <button class="icon-btn small" data-act="edit-tx" data-id="${t.id}" title="Editar">✏️</button>
              <button class="icon-btn small" data-act="del-tx" data-id="${t.id}" title="Eliminar">🗑️</button>
            </td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty"><div class="big">🧾</div><p>No hay movimientos para ${esc(monthName(state.month))} con estos filtros.</p>
             <button class="btn btn-primary" data-act="new-tx">+ Registrar movimiento</button></div>`}
      </div>`;

    state._txList = list;
    const bind = (id, key) => $(id).addEventListener("change", (e) => { f[key] = e.target.value; render(); });
    bind("#ftype", "type"); bind("#fcat", "category_id"); bind("#fcard", "card_id"); bind("#fmethod", "payment_method");
    let timer;
    $("#fq").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        f.q = e.target.value;
        await render();
        const q = $("#fq");
        if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
      }, 300);
    });
  }

  // ---------------------------------------------------------------- tarjetas
  async function renderCards() {
    const cards = state.cards;
    content.innerHTML = `
      <div class="toolbar"><span class="grow muted">Saldos calculados al cierre de ${esc(monthName(state.month))}. Las compras con tarjeta suman a la deuda y los pagos la reducen.</span>
        <button class="btn btn-primary" data-act="new-card">+ Nueva tarjeta</button></div>
      ${cards.length ? `<div class="cards-grid">${cards.map((c) => `
        <div class="panel cc${c.active ? "" : " inactive"}">
          <div class="cc-face" style="--cc:${esc(c.color)}">
            <div class="cc-top"><div><div class="cc-name">${esc(c.name)}</div><div class="cc-bank">${esc(c.bank)}</div></div>
              <div>${c.active ? "" : "Inactiva"}</div></div>
            <div class="cc-num">•••• •••• •••• ${esc(c.last4 || "••••")}</div>
          </div>
          <div class="cc-body">
            <div class="bar-top" style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px">
              <span>Uso del cupo</span><b class="${utilClass(c.utilization) === "bad" ? "neg" : ""}">${pct(c.utilization)}</b></div>
            <div class="meter ${utilClass(c.utilization)}"><span style="width:${Math.min(Math.max(c.utilization, 0), 100)}%"></span></div>
            <div class="cc-stats">
              <div><span>Cupo total</span><b class="num">${money(c.credit_limit)}</b></div>
              <div><span>Deuda</span><b class="num">${money(c.balance)}</b></div>
              <div><span>Disponible</span><b class="num ${c.available < 0 ? "neg" : ""}">${money(c.available)}</b></div>
              <div><span>Compras del mes</span><b class="num">${money(c.month_spent)}</b></div>
              <div><span>Pagos del mes</span><b class="num">${money(c.month_paid)}</b></div>
              <div><span>Interés mensual</span><b>${c.interest_rate ? pct(c.interest_rate) : "—"}</b></div>
            </div>
            <div class="cc-meta"><span>✂️ Corte: ${c.cut_date ? fmtDate(c.cut_date) : "—"}</span><span>📅 Pago: ${c.due_date ? fmtDate(c.due_date) : "—"}</span></div>
            <div class="cc-actions">
              <button class="btn btn-sm btn-primary" data-act="pay-card" data-id="${c.id}">Registrar pago</button>
              <button class="btn btn-sm" data-act="buy-card" data-id="${c.id}">Registrar compra</button>
              <button class="btn btn-sm btn-ghost" data-act="card-tx" data-id="${c.id}">Movimientos</button>
              <span class="grow" style="flex:1"></span>
              <button class="icon-btn small" data-act="edit-card" data-id="${c.id}" title="Editar">✏️</button>
              <button class="icon-btn small" data-act="del-card" data-id="${c.id}" title="Eliminar">🗑️</button>
            </div>
          </div>
        </div>`).join("")}</div>`
      : `<div class="panel empty"><div class="big">💳</div><p>Registra tus tarjetas de crédito para controlar su cupo, deuda y fechas de pago.</p>
         <button class="btn btn-primary" data-act="new-card">+ Agregar tarjeta</button></div>`}`;
  }

  // ------------------------------------------------------ categorías y presupuesto
  async function renderBudget() {
    const expense = state.categories.filter((c) => c.type === "expense");
    const income = state.categories.filter((c) => c.type === "income");
    const activeExp = expense.filter((c) => c.active);
    const totalBudget = activeExp.reduce((s, c) => s + c.budget, 0);
    const totalSpent = activeExp.reduce((s, c) => s + c.spent, 0);
    const totalIncome = income.reduce((s, c) => s + c.spent, 0);

    const rowsExp = expense.map((c) => {
      const ratio = c.budget ? c.spent / c.budget : 0;
      return `<tr class="${c.active ? "" : "muted"}">
        <td><span class="tag"><span class="dot" style="background:${esc(c.color)}"></span>${esc(c.icon)} ${esc(c.name)}</span>${c.active ? "" : ' <span class="pill off">Inactiva</span>'}</td>
        <td class="right"><input class="budget-input" type="number" min="0" step="any" data-budget="${c.id}" value="${c.budget || ""}" placeholder="0">
          <div class="muted" style="font-size:11.5px">${c.budget_custom ? "Solo este mes" : "Por defecto"}</div></td>
        <td class="right num">${money(c.spent)}</td>
        <td class="right num ${c.budget && c.budget - c.spent < 0 ? "neg" : ""}">${c.budget ? money(c.budget - c.spent) : "—"}</td>
        <td style="min-width:140px">${c.budget ? `<div class="meter ${meterClass(ratio)}"><span style="width:${Math.min(ratio, 1) * 100}%"></span></div><div class="muted" style="font-size:12px">${pct(ratio * 100)}</div>` : '<span class="muted">Sin presupuesto</span>'}</td>
        <td class="row-actions">
          <button class="icon-btn small" data-act="edit-cat" data-id="${c.id}" title="Editar">✏️</button>
          <button class="icon-btn small" data-act="del-cat" data-id="${c.id}" title="Eliminar">🗑️</button></td></tr>`;
    }).join("");

    content.innerHTML = `
      <div class="kpis">
        <div class="panel kpi"><div class="label">Presupuesto de ${esc(monthName(state.month))}</div><div class="value num">${money(totalBudget)}</div></div>
        <div class="panel kpi"><div class="label">Gastado</div><div class="value num">${money(totalSpent)}</div>
          <div class="delta">${totalBudget ? pct(totalSpent / totalBudget * 100) + " del presupuesto" : ""}</div></div>
        <div class="panel kpi"><div class="label">Disponible</div><div class="value num ${totalBudget - totalSpent < 0 ? "neg" : ""}">${money(totalBudget - totalSpent)}</div></div>
        <div class="panel kpi"><div class="label">Ingresos del mes</div><div class="value num">${money(totalIncome)}</div>
          <div class="delta">${totalIncome ? `Presupuesto = ${pct(totalBudget / totalIncome * 100)} de tus ingresos` : ""}</div></div>
      </div>
      <div class="panel mb">
        <div class="panel-head"><div><h3>Categorías de gasto</h3>
          <p class="sub">Escribe el presupuesto de ${esc(monthName(state.month))}. Se guarda solo para este mes; deja vacío para usar el valor por defecto.</p></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-sm" data-act="copy-budget">Copiar presupuesto de ${esc(monthName(shiftMonth(state.month, -1)))}</button>
            <button class="btn btn-sm btn-primary" data-act="new-cat" data-type="expense">+ Categoría</button></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Categoría</th><th class="right">Presupuesto</th><th class="right">Gastado</th><th class="right">Restante</th><th>Progreso</th><th></th></tr></thead>
          <tbody>${rowsExp || '<tr><td colspan="6" class="empty">Sin categorías</td></tr>'}</tbody></table></div>
      </div>
      <div class="panel">
        <div class="panel-head"><div><h3>Categorías de ingreso</h3><p class="sub">Fuentes de ingreso</p></div>
          <button class="btn btn-sm btn-primary" data-act="new-cat" data-type="income">+ Categoría</button></div>
        <div class="table-wrap"><table><thead><tr><th>Categoría</th><th class="right">Recibido este mes</th><th></th></tr></thead>
        <tbody>${income.map((c) => `<tr class="${c.active ? "" : "muted"}">
          <td><span class="tag"><span class="dot" style="background:${esc(c.color)}"></span>${esc(c.icon)} ${esc(c.name)}</span></td>
          <td class="right num">${money(c.spent)}</td>
          <td class="row-actions"><button class="icon-btn small" data-act="edit-cat" data-id="${c.id}">✏️</button>
          <button class="icon-btn small" data-act="del-cat" data-id="${c.id}">🗑️</button></td></tr>`).join("")}</tbody></table></div>
      </div>`;

    content.querySelectorAll("[data-budget]").forEach((input) => {
      input.addEventListener("change", async () => {
        try {
          await api("PUT", "/api/budgets", { category_id: input.dataset.budget, month: state.month, amount: input.value });
          toast("Presupuesto actualizado");
          render();
        } catch (err) { toast(err.message, true); }
      });
    });
  }

  // ------------------------------------------------------------- gastos fijos
  async function renderRecurring() {
    const list = await api("GET", `/api/recurring?month=${state.month}`);
    const pending = list.filter((r) => r.active && !r.registered);
    const totalExp = list.filter((r) => r.active && r.type === "expense").reduce((s, r) => s + r.amount, 0);
    const totalInc = list.filter((r) => r.active && r.type === "income").reduce((s, r) => s + r.amount, 0);
    content.innerHTML = `
      <div class="kpis">
        <div class="panel kpi"><div class="label">Gastos fijos mensuales</div><div class="value num">${money(totalExp)}</div></div>
        <div class="panel kpi"><div class="label">Ingresos fijos mensuales</div><div class="value num">${money(totalInc)}</div></div>
        <div class="panel kpi"><div class="label">Pendientes en ${esc(monthName(state.month))}</div><div class="value num">${pending.length}</div></div>
      </div>
      <div class="toolbar"><span class="grow muted">Define tus gastos e ingresos que se repiten cada mes (arriendo, servicios, salario...) y regístralos con un clic.</span>
        ${pending.length ? `<button class="btn" data-act="gen-recurring">✅ Registrar ${pending.length} pendiente(s) del mes</button>` : ""}
        <button class="btn btn-primary" data-act="new-rec">+ Nuevo fijo</button></div>
      <div class="panel" style="padding:0">
        ${list.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Día</th><th>Descripción</th><th>Categoría</th><th>Medio</th><th class="right">Monto</th><th>Este mes</th><th></th></tr></thead>
          <tbody>${list.map((r) => `<tr>
            <td class="num">${r.day}</td>
            <td>${esc(r.description)} <span class="pill ${r.type}">${TX_TYPES[r.type]}</span></td>
            <td>${r.category_name ? `${esc(r.category_icon)} ${esc(r.category_name)}` : '<span class="muted">—</span>'}</td>
            <td>${esc(METHODS[r.payment_method])}${r.card_name ? `<div class="muted" style="font-size:12px">💳 ${esc(r.card_name)}</div>` : ""}</td>
            <td class="right num"><b>${money(r.amount)}</b></td>
            <td>${!r.active ? '<span class="pill off">Inactivo</span>' : r.registered ? '<span class="pill ok">Registrado</span>' : '<span class="pill pending">Pendiente</span>'}</td>
            <td class="row-actions"><button class="icon-btn small" data-act="edit-rec" data-id="${r.id}">✏️</button>
              <button class="icon-btn small" data-act="del-rec" data-id="${r.id}">🗑️</button></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty"><div class="big">🔁</div><p>Aún no tienes gastos o ingresos fijos.</p>
             <button class="btn btn-primary" data-act="new-rec">+ Agregar</button></div>`}
      </div>`;
    state._recList = list;
  }

  // -------------------------------------------------------------------- metas
  async function renderGoals() {
    const goals = await api("GET", "/api/goals");
    state._goals = goals;
    content.innerHTML = `
      <div class="toolbar"><span class="grow muted">Define metas y registra tus abonos para ver cuánto te falta.</span>
        <button class="btn btn-primary" data-act="new-goal">+ Nueva meta</button></div>
      ${goals.length ? `<div class="grid grid-3">${goals.map((g) => {
        const ratio = g.target ? g.saved / g.target : 0;
        let monthly = "";
        if (g.deadline && g.saved < g.target) {
          const [y, m] = g.deadline.split("-").map(Number);
          const now = new Date();
          const months = (y - now.getFullYear()) * 12 + (m - 1 - now.getMonth());
          monthly = months > 0 ? `Ahorra ${money((g.target - g.saved) / months)} al mes para lograrlo`
            : `<span class="neg">La fecha objetivo ya llegó</span>`;
        }
        return `<div class="panel goal">
          <div class="panel-head"><h3><span class="dot" style="background:${esc(g.color)}"></span> ${esc(g.name)}</h3>
            <div><button class="icon-btn small" data-act="edit-goal" data-id="${g.id}">✏️</button>
            <button class="icon-btn small" data-act="del-goal" data-id="${g.id}">🗑️</button></div></div>
          <div class="goal-amount num">${money(g.saved)} <span class="muted" style="font-size:14px;font-weight:500">de ${money(g.target)}</span></div>
          <div class="meter"><span style="width:${Math.min(ratio, 1) * 100}%;background:${esc(g.color)}"></span></div>
          <div class="cc-meta" style="margin-top:8px"><span>${pct(ratio * 100)} completado${ratio >= 1 ? " 🎉" : ""}</span>
            <span>${g.deadline ? `Meta: ${esc(g.deadline)}` : ""}</span></div>
          ${monthly ? `<p class="muted" style="margin:8px 0 0;font-size:12.5px">${monthly}</p>` : ""}
          <div class="cc-actions"><button class="btn btn-sm btn-primary" data-act="add-goal" data-id="${g.id}">+ Abonar</button>
            <button class="btn btn-sm" data-act="sub-goal" data-id="${g.id}">− Retirar</button></div>
        </div>`;
      }).join("")}</div>`
      : `<div class="panel empty"><div class="big">🎯</div><p>Crea tu primera meta: fondo de emergencia, viaje, carro...</p>
         <button class="btn btn-primary" data-act="new-goal">+ Nueva meta</button></div>`}`;
  }

  // ------------------------------------------------------------------ ajustes
  async function renderSettings() {
    content.innerHTML = `
      <div class="grid grid-2">
        <div class="panel"><h3>Moneda</h3><p class="sub">Formato en el que se muestran los montos</p>
          <div class="toolbar"><select id="currency">
            ${["COP", "USD", "EUR", "MXN", "ARS", "CLP", "PEN"].map((c) => `<option${c === state.settings.currency ? " selected" : ""}>${c}</option>`).join("")}
          </select></div></div>
        <div class="panel"><h3>Respaldo</h3><p class="sub">Todos tus datos viven en el archivo <code>data/finanzas.db</code> (SQLite) dentro de la carpeta de la app.
          Descarga una copia de seguridad de vez en cuando.</p>
          <a class="btn" href="/api/backup">⬇ Descargar respaldo</a>
          <p class="sub" style="margin-top:12px">Para restaurar: cierra la app y reemplaza <code>data/finanzas.db</code> por el archivo de respaldo.</p></div>
        <div class="panel"><h3>Datos de ejemplo</h3><p class="sub">¿Quieres probar la app con datos ficticios? Ejecuta <code>python seed_demo.py</code> con la app cerrada.</p></div>
        <div class="panel"><h3>Atajos de teclado</h3><p class="sub" style="margin:0">
          <b>N</b>: nuevo movimiento · <b>←</b>/<b>→</b>: mes anterior/siguiente · <b>T</b>: volver al mes actual</p></div>
      </div>`;
    $("#currency").addEventListener("change", async (e) => {
      state.settings = await api("PUT", "/api/settings", { currency: e.target.value });
      toast("Moneda actualizada");
    });
  }

  // ----------------------------------------------------------- acciones (clics)
  const findCard = (id) => state.cards.find((c) => c.id === Number(id));
  const findCat = (id) => state.categories.find((c) => c.id === Number(id));

  async function removeItem(msg, path, okMsg) {
    if (!confirmAction(msg)) return;
    try { await api("DELETE", path); toast(okMsg); render(); } catch (err) { toast(err.message, true); }
  }

  const ACTIONS = {
    "new-tx": () => openTransactionForm(null),
    "edit-tx": (id) => openTransactionForm(state._txList.find((t) => t.id === Number(id))),
    "del-tx": (id) => removeItem("¿Eliminar este movimiento?", `/api/transactions/${id}`, "Movimiento eliminado"),
    "new-card": () => openCardForm(null),
    "edit-card": (id) => openCardForm(findCard(id)),
    "del-card": (id) => removeItem("¿Eliminar esta tarjeta? Sus movimientos se conservarán pero quedarán sin tarjeta asociada.",
      `/api/cards/${id}`, "Tarjeta eliminada"),
    "pay-card": (id) => {
      const c = findCard(id);
      openTransactionForm(null, { type: "card_payment", card_id: c.id, payment_method: "transfer",
        amount: c.balance > 0 ? c.balance : "", description: `Pago ${c.name}` });
    },
    "buy-card": (id) => openTransactionForm(null, { type: "expense", payment_method: "card", card_id: Number(id) }),
    "card-tx": (id) => {
      Object.assign(state.txFilters, { type: "", category_id: "", card_id: String(id), payment_method: "", q: "" });
      location.hash = "#movimientos";
    },
    "new-cat": (_id, el) => openCategoryForm(null, el.dataset.type),
    "edit-cat": (id) => openCategoryForm(findCat(id)),
    "del-cat": (id) => removeItem("¿Eliminar esta categoría? Los movimientos quedarán sin categoría. (Puedes desactivarla en lugar de eliminarla.)",
      `/api/categories/${id}`, "Categoría eliminada"),
    "copy-budget": async () => {
      const from = shiftMonth(state.month, -1);
      if (!confirmAction(`¿Copiar el presupuesto de ${monthName(from)} a ${monthName(state.month)}?`)) return;
      try { await api("POST", "/api/budgets/copy", { from, to: state.month }); toast("Presupuesto copiado"); render(); }
      catch (err) { toast(err.message, true); }
    },
    "new-rec": () => openRecurringForm(null),
    "edit-rec": (id) => openRecurringForm(state._recList.find((r) => r.id === Number(id))),
    "del-rec": (id) => removeItem("¿Eliminar este fijo? Los movimientos ya registrados se conservan.", `/api/recurring/${id}`, "Eliminado"),
    "gen-recurring": async () => {
      try {
        const r = await api("POST", "/api/recurring/generate", { month: state.month });
        toast(`${r.created} movimiento(s) registrado(s)`);
        render();
      } catch (err) { toast(err.message, true); }
    },
    "new-goal": () => openGoalForm(null),
    "edit-goal": (id) => openGoalForm(state._goals.find((g) => g.id === Number(id))),
    "del-goal": (id) => removeItem("¿Eliminar esta meta?", `/api/goals/${id}`, "Meta eliminada"),
    "add-goal": (id) => goalMove(id, 1),
    "sub-goal": (id) => goalMove(id, -1),
  };

  function goalMove(id, sign) {
    const g = state._goals.find((x) => x.id === Number(id));
    openForm({
      title: `${sign > 0 ? "Abonar a" : "Retirar de"} ${g.name}`,
      submitLabel: sign > 0 ? "Abonar" : "Retirar",
      fields: [{ name: "amount", label: "Monto", type: "number", step: "any", min: 0, full: true }],
      onSubmit: async (data) => {
        const amount = Number(data.amount);
        if (!(amount > 0)) throw new Error("Ingresa un monto mayor a 0");
        await api("POST", `/api/goals/${id}/contribute`, { amount: sign * amount });
        toast(sign > 0 ? "Abono registrado" : "Retiro registrado");
      },
    });
  }

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (!el || !ACTIONS[el.dataset.act]) return;
    e.preventDefault();
    ACTIONS[el.dataset.act](el.dataset.id, el);
  });

  // ------------------------------------------------------------------- tema
  function applyTheme(theme) {
    if (theme) document.documentElement.dataset.theme = theme;
  }
  $("#themeBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    applyTheme(next);
    storageSet("finanzas.theme", next);
    render();
  });

  // ----------------------------------------------------------------- arranque
  $("#prevMonth").addEventListener("click", () => setMonth(shiftMonth(state.month, -1)));
  $("#nextMonth").addEventListener("click", () => setMonth(shiftMonth(state.month, 1)));
  $("#todayBtn").addEventListener("click", () => setMonth(currentMonth()));
  $("#monthInput").addEventListener("change", (e) => { if (e.target.value) setMonth(e.target.value); });
  $("#quickAdd").addEventListener("click", () => openTransactionForm(null));
  $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#modalClose").addEventListener("click", () => $("#modal").close());
  $("#modalCancel").addEventListener("click", () => $("#modal").close());
  window.addEventListener("hashchange", routeFromHash);

  document.addEventListener("keydown", (e) => {
    if ($("#modal").open || e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    if (e.key === "n" || e.key === "N") { e.preventDefault(); openTransactionForm(null); }
    else if (e.key === "ArrowLeft") setMonth(shiftMonth(state.month, -1));
    else if (e.key === "ArrowRight") setMonth(shiftMonth(state.month, 1));
    else if (e.key === "t" || e.key === "T") setMonth(currentMonth());
  });

  applyTheme(storageGet("finanzas.theme"));
  api("GET", "/api/settings")
    .then((s) => { state.settings = s; })
    .catch(() => {})
    .finally(routeFromHash);
})();
