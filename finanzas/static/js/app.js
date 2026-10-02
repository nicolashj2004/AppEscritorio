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
    invDisplay: "COP",
    flow: "all", // all | expense | income (selector fijo de la barra superior)
  };
  const charts = [];

  const VIEWS = {
    dashboard: { title: "Dashboard", render: renderDashboard },
    movimientos: { title: "Movimientos", render: renderTransactions },
    tarjetas: { title: "Tarjetas", render: renderCards },
    presupuesto: { title: "Categorías y presupuesto", render: renderBudget },
    fijos: { title: "Gastos e ingresos fijos", render: renderRecurring },
    metas: { title: "Metas de ahorro", render: renderGoals },
    inversiones: { title: "Inversiones", render: renderInvestments },
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
  function dayInMonth(month, day) {
    const [y, m] = month.split("-").map(Number);
    const last = new Date(y, m, 0).getDate();
    return `${month}-${String(Math.min(Math.max(day, 1), last)).padStart(2, "0")}`;
  }
  // Mes al que corresponde un movimiento por defecto (ver Ajustes → ingresos)
  function autoPeriod(type, date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "")) return state.month;
    const month = date.slice(0, 7);
    const shift = Number(state.settings.income_shift_day) || 0;
    return type === "income" && shift && Number(date.slice(8, 10)) >= shift ? shiftMonth(month, 1) : month;
  }
  function defaultDateForMonth(month) {
    return month === currentMonth() ? today() : `${month}-01`;
  }
  function money(v, compact = false, currency = null) {
    const cur = currency || state.settings.currency || "COP";
    const opts = { style: "currency", currency: cur, maximumFractionDigits: cur === "USD" ? 2 : 0 };
    if (compact) Object.assign(opts, { notation: "compact", maximumFractionDigits: 1 });
    try { return new Intl.NumberFormat("es-CO", opts).format(v || 0); }
    catch { return `$${Math.round(v || 0).toLocaleString("es-CO")}`; }
  }
  function fmtRate(v) { return `$ ${Number(v).toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
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

  const FLOW_VIEWS = ["movimientos", "presupuesto", "fijos"];
  function updateFlowToggle() {
    const el = $("#flowToggle");
    el.style.display = FLOW_VIEWS.includes(state.view) ? "" : "none";
    el.querySelectorAll("input").forEach((i) => (i.checked = i.value === state.flow));
  }
  const showExpense = () => state.flow !== "income";
  const showIncome = () => state.flow !== "expense";

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
    updateFlowToggle();
    destroyCharts();
    try {
      await loadRefs();
      await VIEWS[view].render();
    } catch (err) {
      content.innerHTML = `<div class="panel empty"><div class="big">⚠️</div><p>${esc(err.message)}</p></div>`;
    }
  }

  function routeFromHash() {
    if ($("#modal").open) $("#modal").close();
    state.view = (location.hash || "#dashboard").slice(1);
    $("#sidebar").classList.remove("open");
    render();
  }

  // ------------------------------------------------------------- formularios
  /**
   * fields: [{ name, label, type, options, full, hint, showIf(values), required, step, min }]
   * options puede ser un arreglo [{value,label}] o una función (values) => arreglo.
   */
  function openForm({ title, fields, values = {}, submitLabel = "Guardar", onSubmit, onChange }) {
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
          html += `<input id="${id}" type="${f.type || "text"}"${f.step ? ` step="${f.step}"` : ""}${f.min !== undefined ? ` min="${f.min}"` : ""}${f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : ""}${f.datalist ? ` list="${id}_list" autocomplete="off"` : ""}>`;
          if (f.datalist) html += `<datalist id="${id}_list">${f.datalist.map((o) => `<option value="${esc(o)}">`).join("")}</datalist>`;
        }
        if (f.hint) html += `<span class="hint"></span>`;
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
        const hintEl = inputs[f.name].wrap.querySelector(".hint");
        if (hintEl) {
          const text = typeof f.hint === "function" ? f.hint(current) : f.hint;
          hintEl.textContent = text || "";
          hintEl.style.display = text ? "" : "none";
        }
      });
    }

    fields.forEach((f) => {
      if (f.type !== "select") writeValue(f.name, current[f.name]);
    });
    refresh();
    fields.forEach((f) => {
      const handler = () => {
        current[f.name] = readValue(f.name);
        if (onChange) onChange(f.name, current, (name, value) => { current[name] = value; writeValue(name, value); });
        refresh();
      };
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
  function cardOptions(includeId, kind = "credit") {
    return state.cards
      .filter((c) => c.kind === kind && (c.active || c.id === includeId))
      .map((c) => ({ value: c.id, label: `${c.name}${c.last4 ? ` •${c.last4}` : ""}` }));
  }
  const creditCards = () => state.cards.filter((c) => c.kind === "credit");
  const debitCards = () => state.cards.filter((c) => c.kind === "debit" && c.active);
  // Tarjeta del movimiento: crédito para compras con tarjeta y pagos; débito opcional
  function txCardOptions(v, includeId) {
    if (v.type === "expense" && v.payment_method === "debit") {
      return [{ value: "", label: "— Sin especificar —" }, ...cardOptions(includeId, "debit")];
    }
    return cardOptions(includeId, "credit");
  }
  const showTxCard = (v) => v.type === "card_payment" || (v.type === "expense" && v.payment_method === "card")
    || (v.type === "expense" && v.payment_method === "debit" && debitCards().length > 0);

  function openTransactionForm(tx, preset = {}) {
    if (!tx && creditCards().length === 0 && preset.type === "card_payment") {
      toast("Primero registra una tarjeta", true);
      return;
    }
    const values = tx ? { ...tx } : {
      type: "expense", date: defaultDateForMonth(state.month), payment_method: "debit", installments: 1, ...preset,
    };
    if (!values.period) values.period = autoPeriod(values.type, values.date);
    // Si el usuario elige el mes a mano, ya no se recalcula automáticamente
    let periodTouched = !!tx || !!preset.period;
    openForm({
      title: tx ? "Editar movimiento" : preset.recurring_id ? "Registrar fijo del mes" : "Nuevo movimiento",
      values,
      fields: [
        { name: "type", label: "Tipo", type: "seg", full: true,
          options: Object.entries(TX_TYPES).map(([value, label]) => ({ value, label })) },
        { name: "amount", label: "Monto", type: "number", step: "any", min: 0, placeholder: "0" },
        { name: "date", label: "Fecha", type: "date" },
        { name: "period", label: "Corresponde al mes", type: "month", full: true,
          hint: (v) => v.date && v.period && v.period !== v.date.slice(0, 7)
            ? `Se contará en ${monthName(v.period)} aunque la fecha sea de ${monthName(v.date.slice(0, 7))}` : "" },
        { name: "description", label: "Descripción", type: "text", full: true, placeholder: "Ej: Mercado del mes" },
        { name: "category_id", label: "Categoría", type: "select", full: true,
          options: (v) => categoryOptions(v.type === "income" ? "income" : "expense"),
          hint: (v) => (v.type === "card_payment" ? "Opcional: el pago sumará al gasto y presupuesto de esta categoría" : "") },
        { name: "payment_method", label: "Medio de pago", type: "select",
          options: (v) => Object.entries(METHODS)
            .filter(([k]) => v.type === "expense" || k !== "card")
            .map(([value, label]) => ({ value, label })) },
        { name: "card_id", label: "Tarjeta", type: "select",
          options: (v) => txCardOptions(v, tx && tx.card_id), showIf: showTxCard },
        { name: "installments", label: "Número de cuotas", type: "number", min: 1, step: 1,
          showIf: (v) => v.type === "expense" && v.payment_method === "card" },
        { name: "notes", label: "Notas", type: "textarea", full: true },
      ],
      onChange: (name, v, set) => {
        if (name === "period") periodTouched = true;
        else if ((name === "date" || name === "type") && !periodTouched) set("period", autoPeriod(v.type, v.date));
      },
      onSubmit: async (data) => {
        if (preset.recurring_id) data.recurring_id = preset.recurring_id;
        if (tx) await api("PUT", `/api/transactions/${tx.id}`, data);
        else await api("POST", "/api/transactions", data);
        toast(tx ? "Movimiento actualizado" : "Movimiento registrado");
      },
    });
  }

  function openCardForm(card, kind = "credit") {
    const credit = (v) => v.kind === "credit";
    openForm({
      title: card ? "Editar tarjeta" : "Nueva tarjeta",
      values: card ? { ...card, active: !!card.active, due_next_month: card.due_next_month ? "1" : "0" }
        : { kind, color: kind === "debit" ? "#1baf7a" : "#2a78d6", active: true, initial_balance: 0, due_next_month: "1" },
      fields: [
        { name: "kind", label: "Tipo de tarjeta", type: "seg", full: true,
          options: [{ value: "credit", label: "💳 Crédito" }, { value: "debit", label: "🏧 Débito" }],
          hint: (v) => (v.kind === "debit" ? "Una tarjeta débito descuenta de tu cuenta: no tiene cupo, corte ni pago" : "") },
        { name: "name", label: "Nombre", type: "text", placeholder: "Ej: Visa Oro" },
        { name: "bank", label: "Banco / franquicia", type: "text", placeholder: "Ej: Bancolombia" },
        { name: "credit_limit", label: "Cupo total", type: "number", step: "any", min: 0, showIf: credit },
        { name: "initial_balance", label: "Deuda actual al registrarla", type: "number", step: "any", min: 0,
          hint: "Lo que ya debías antes de empezar a usar la app", showIf: credit },
        { name: "cut_day", label: "Día de corte", type: "number", min: 1, step: 1, showIf: credit },
        { name: "due_day", label: "Día límite de pago", type: "number", min: 1, step: 1, showIf: credit },
        { name: "due_next_month", label: "El pago de cada corte se hace", type: "select", full: true, showIf: credit,
          options: [{ value: "1", label: "El mes siguiente al corte (p. ej. corte 10 oct → pago en nov)" },
            { value: "0", label: "El mismo mes del corte (p. ej. corte 10 oct → pago en oct)" }] },
        { name: "last4", label: "Últimos 4 dígitos", type: "text", placeholder: "1234" },
        { name: "interest_rate", label: "Tasa de interés mensual (%)", type: "number", step: "any", min: 0, showIf: credit },
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
      values: rec ? { ...rec, active: !!rec.active }
        : { type: state.flow === "income" ? "income" : "expense", day: 1, payment_method: state.flow === "income" ? "transfer" : "debit", active: true },
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
        { name: "card_id", label: "Tarjeta", type: "select", options: (v) => txCardOptions(v, rec && rec.card_id),
          showIf: (v) => v.type === "expense" && (v.payment_method === "card" || (v.payment_method === "debit" && debitCards().length > 0)) },
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
      { label: "Inversiones", value: money(d.investments),
        delta: `<a href="#inversiones">${d.investments ? "Ver portafolio" : "Registra tus inversiones"}</a>` },
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
    const flow = state.flow;
    const qs = new URLSearchParams({ month: state.month });
    Object.entries(f).forEach(([k, v]) => { if (v && k !== "type") qs.set(k, v); });
    const typeParam = f.type || (flow === "expense" ? "expense,card_payment" : flow === "income" ? "income" : "");
    if (typeParam) qs.set("type", typeParam);
    const list = await api("GET", `/api/transactions?${qs}`);
    const typeOpts = flow === "expense"
      ? [{ value: "", label: "Gastos y pagos a tarjeta" }, { value: "expense", label: "Solo gastos" }, { value: "card_payment", label: "Solo pagos a tarjeta" }]
      : [{ value: "", label: "Todos los tipos" }, ...Object.entries(TX_TYPES).map(([value, label]) => ({ value, label }))];

    const sum = (type) => list.filter((t) => t.type === type).reduce((s, t) => s + t.amount, 0);
    const catOpts = [{ value: "", label: "Todas las categorías" }, { value: "none", label: "Sin categoría" },
      ...state.categories.filter((c) => flow === "all" || c.type === flow).map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))];
    const opt = (arr, sel) => arr.map((o) => `<option value="${esc(o.value)}"${String(o.value) === String(sel) ? " selected" : ""}>${esc(o.label)}</option>`).join("");

    content.innerHTML = `
      <div class="toolbar">
        <input type="search" id="fq" placeholder="Buscar..." value="${esc(f.q)}">
        ${flow === "income" ? "" : `<select id="ftype">${opt(typeOpts, f.type)}</select>`}
        <select id="fcat">${opt(catOpts, f.category_id)}</select>
        ${flow === "income" ? "" : `<select id="fcard">${opt([{ value: "", label: "Todas las tarjetas" }, ...state.cards.map((c) => ({ value: c.id, label: c.name }))], f.card_id)}</select>`}
        <select id="fmethod">${opt([{ value: "", label: "Todos los medios" }, ...Object.entries(METHODS).map(([value, label]) => ({ value, label }))], f.payment_method)}</select>
        <span class="grow"></span>
        <a class="btn" href="/api/transactions/export.csv?${qs}" download>⬇ Exportar CSV</a>
        <button class="btn btn-primary" data-act="new-tx">${flow === "income" ? "+ Ingreso" : flow === "expense" ? "+ Gasto" : "+ Movimiento"}</button>
      </div>
      <div class="summary-strip">
        <span>${list.length} movimiento(s)</span>
        ${showIncome() ? `<span>Ingresos: <b class="num pos">${money(sum("income"))}</b></span>` : ""}
        ${showExpense() ? `<span>Gastos: <b class="num">${money(sum("expense"))}</b></span>
        <span>Pagos a tarjetas: <b class="num">${money(sum("card_payment"))}</b></span>` : ""}
      </div>
      <div class="panel" style="padding:0">
        ${list.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Fecha</th><th>Descripción</th><th>Categoría</th><th>Tipo</th><th>Medio</th><th class="right">Monto</th><th></th></tr></thead>
          <tbody>${list.map((t) => `<tr>
            <td class="num">${fmtDate(t.date)}${t.period && t.period !== t.date.slice(0, 7)
              ? `<div class="muted" style="font-size:12px" title="Cuenta para ${esc(monthName(t.period))}">↪ ${esc(monthName(t.period, true))}</div>` : ""}</td>
            <td>${esc(t.description) || '<span class="muted">—</span>'}${t.notes ? `<div class="muted" style="font-size:12px">${esc(t.notes)}</div>` : ""}</td>
            <td>${t.category_name ? `<span class="tag"><span class="dot" style="background:${esc(t.category_color)}"></span>${esc(t.category_icon)} ${esc(t.category_name)}</span>` : '<span class="muted">—</span>'}</td>
            <td><span class="pill ${t.type}">${TX_TYPES[t.type]}</span></td>
            <td>${esc(METHODS[t.payment_method] || t.payment_method)}${t.card_name ? `<div class="muted" style="font-size:12px">${t.card_kind === "debit" ? "🏧" : "💳"} ${esc(t.card_name)}${t.installments > 1 ? ` · ${t.installments} cuotas` : ""}</div>` : ""}</td>
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
    if ($("#ftype")) bind("#ftype", "type");
    if ($("#fcard")) bind("#fcard", "card_id");
    bind("#fcat", "category_id"); bind("#fmethod", "payment_method");
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
    const credit = state.cards.filter((c) => c.kind === "credit");
    const debit = state.cards.filter((c) => c.kind === "debit");
    const face = (c, kindLabel) => `
          <div class="cc-face" style="--cc:${esc(c.color)}">
            <div class="cc-top"><div><div class="cc-name">${esc(c.name)}</div><div class="cc-bank">${esc(c.bank)}</div></div>
              <div class="cc-kind">${kindLabel}${c.active ? "" : " · Inactiva"}</div></div>
            <div class="cc-num">•••• •••• •••• ${esc(c.last4 || "••••")}</div>
          </div>`;
    const editButtons = (c) => `<span class="grow" style="flex:1"></span>
              <button class="icon-btn small" data-act="edit-card" data-id="${c.id}" title="Editar">✏️</button>
              <button class="icon-btn small" data-act="del-card" data-id="${c.id}" title="Eliminar">🗑️</button>`;

    const creditHtml = credit.map((c) => `
        <div class="panel cc${c.active ? "" : " inactive"}">${face(c, "Crédito")}
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
            <div class="cc-meta"><span>✂️ Corte: ${c.cut_date ? fmtDate(c.cut_date) : "—"}</span>
              <span title="Fecha límite para pagar el corte de ${esc(monthName(state.month))}">📅 Pago: ${c.due_date ? fmtDate(c.due_date) : "—"}</span></div>
            <div class="cc-actions">
              <button class="btn btn-sm btn-primary" data-act="pay-card" data-id="${c.id}">Registrar pago</button>
              <button class="btn btn-sm" data-act="buy-card" data-id="${c.id}">Registrar compra</button>
              <button class="btn btn-sm btn-ghost" data-act="card-tx" data-id="${c.id}">Movimientos</button>
              ${editButtons(c)}
            </div>
          </div>
        </div>`).join("");

    const debitHtml = debit.map((c) => `
        <div class="panel cc${c.active ? "" : " inactive"}">${face(c, "Débito")}
          <div class="cc-body">
            <div class="cc-stats" style="grid-template-columns:1fr">
              <div><span>Gastos del mes con esta tarjeta</span><b class="num">${money(c.month_spent)}</b></div>
            </div>
            <div class="cc-actions">
              <button class="btn btn-sm btn-primary" data-act="buy-debit" data-id="${c.id}">Registrar compra</button>
              <button class="btn btn-sm btn-ghost" data-act="card-tx" data-id="${c.id}">Movimientos</button>
              ${editButtons(c)}
            </div>
          </div>
        </div>`).join("");

    content.innerHTML = `
      <div class="toolbar"><span class="grow muted">Saldos calculados al cierre de ${esc(monthName(state.month))}.</span>
        <button class="btn" data-act="new-card" data-type="debit">+ Tarjeta débito</button>
        <button class="btn btn-primary" data-act="new-card" data-type="credit">+ Tarjeta de crédito</button></div>
      <h2 class="section-title">💳 Tarjetas de crédito</h2>
      <p class="muted section-sub">Las compras suman a la deuda y los pagos la reducen. La fecha de pago es la del corte de este mes.</p>
      ${credit.length ? `<div class="cards-grid mb">${creditHtml}</div>`
        : `<div class="panel empty mb"><p>Registra tus tarjetas de crédito para controlar su cupo, deuda y fechas de pago.</p>
           <button class="btn btn-primary" data-act="new-card" data-type="credit">+ Tarjeta de crédito</button></div>`}
      <h2 class="section-title">🏧 Tarjetas débito</h2>
      <p class="muted section-sub">Al registrar un gasto con medio de pago "Débito" puedes elegir con cuál tarjeta lo pagaste.</p>
      ${debit.length ? `<div class="cards-grid">${debitHtml}</div>`
        : `<div class="panel empty"><p>Agrega tus tarjetas débito para saber cuánto gastas con cada una.</p>
           <button class="btn" data-act="new-card" data-type="debit">+ Tarjeta débito</button></div>`}`;
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
        ${showExpense() ? `<div class="panel kpi"><div class="label">Presupuesto de ${esc(monthName(state.month))}</div><div class="value num">${money(totalBudget)}</div></div>
        <div class="panel kpi"><div class="label">Gastado</div><div class="value num">${money(totalSpent)}</div>
          <div class="delta">${totalBudget ? pct(totalSpent / totalBudget * 100) + " del presupuesto" : ""}</div></div>
        <div class="panel kpi"><div class="label">Disponible</div><div class="value num ${totalBudget - totalSpent < 0 ? "neg" : ""}">${money(totalBudget - totalSpent)}</div></div>` : ""}
        ${showIncome() ? `<div class="panel kpi"><div class="label">Ingresos del mes</div><div class="value num">${money(totalIncome)}</div>
          <div class="delta">${totalIncome ? `Presupuesto = ${pct(totalBudget / totalIncome * 100)} de tus ingresos` : ""}</div></div>` : ""}
      </div>
      ${showExpense() ? `<div class="panel mb">
        <div class="panel-head"><div><h3>Categorías de gasto</h3>
          <p class="sub">Escribe el presupuesto de ${esc(monthName(state.month))}. Se guarda solo para este mes; deja vacío para usar el valor por defecto.</p></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-sm" data-act="copy-budget">Copiar presupuesto de ${esc(monthName(shiftMonth(state.month, -1)))}</button>
            <button class="btn btn-sm btn-primary" data-act="new-cat" data-type="expense">+ Categoría</button></div></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Categoría</th><th class="right">Presupuesto</th><th class="right">Gastado</th><th class="right">Restante</th><th>Progreso</th><th></th></tr></thead>
          <tbody>${rowsExp || '<tr><td colspan="6" class="empty">Sin categorías</td></tr>'}</tbody></table></div>
      </div>` : ""}
      ${showIncome() ? `<div class="panel">
        <div class="panel-head"><div><h3>Categorías de ingreso</h3><p class="sub">Fuentes de ingreso</p></div>
          <button class="btn btn-sm btn-primary" data-act="new-cat" data-type="income">+ Categoría</button></div>
        <div class="table-wrap"><table><thead><tr><th>Categoría</th><th class="right">Recibido este mes</th><th></th></tr></thead>
        <tbody>${income.map((c) => `<tr class="${c.active ? "" : "muted"}">
          <td><span class="tag"><span class="dot" style="background:${esc(c.color)}"></span>${esc(c.icon)} ${esc(c.name)}</span></td>
          <td class="right num">${money(c.spent)}</td>
          <td class="row-actions"><button class="icon-btn small" data-act="edit-cat" data-id="${c.id}">✏️</button>
          <button class="icon-btn small" data-act="del-cat" data-id="${c.id}">🗑️</button></td></tr>`).join("")}</tbody></table></div>
      </div>` : ""}`;

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
    const list = (await api("GET", `/api/recurring?month=${state.month}`))
      .filter((r) => state.flow === "all" || r.type === state.flow);
    const pending = list.filter((r) => r.active && !r.registered);
    const totalExp = list.filter((r) => r.active && r.type === "expense").reduce((s, r) => s + r.amount, 0);
    const totalInc = list.filter((r) => r.active && r.type === "income").reduce((s, r) => s + r.amount, 0);
    content.innerHTML = `
      <div class="kpis">
        ${showExpense() ? `<div class="panel kpi"><div class="label">Gastos fijos mensuales</div><div class="value num">${money(totalExp)}</div></div>` : ""}
        ${showIncome() ? `<div class="panel kpi"><div class="label">Ingresos fijos mensuales</div><div class="value num">${money(totalInc)}</div></div>` : ""}
        <div class="panel kpi"><div class="label">Pendientes en ${esc(monthName(state.month))}</div><div class="value num">${pending.length}</div></div>
      </div>
      <div class="toolbar"><span class="grow muted">Define tus gastos e ingresos que se repiten cada mes (arriendo, servicios, salario...) y regístralos uno por uno (puedes ajustar el monto) o todos a la vez.</span>
        ${pending.length ? `<button class="btn" data-act="gen-recurring">✅ Registrar todos (${pending.length})</button>` : ""}
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
            <td>${!r.active ? '<span class="pill off">Inactivo</span>' : r.registered ? '<span class="pill ok">Registrado</span>'
              : `<span class="pill pending">Pendiente</span> <button class="btn btn-sm btn-primary" data-act="reg-rec" data-id="${r.id}">Registrar</button>`}</td>
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

  // --------------------------------------------------------------- inversiones
  const PLATFORM_SUGGESTIONS = ["Trii", "Tyba", "Nu", "Lulo Bank", "Bancolombia", "Davivienda", "BBVA",
    "Skandia", "Protección", "Porvenir", "Colfondos", "Acciones & Valores", "Credicorp Capital",
    "Binance", "Hapi", "XTB", "Interactive Brokers", "eToro"];

  async function renderInvestments() {
    const d = await api("GET", `/api/investments?month=${state.month}&display=${state.invDisplay}`);
    state._inv = d;
    const mv = (v) => money(v, false, d.display);
    // Monto en la moneda propia de la inversión cuando difiere de la que se está viendo
    const native = (h, v) => (h.currency !== d.display ? `<div class="muted" style="font-size:12px">${money(v, false, h.currency)}</div>` : "");
    const usdShare = (d.by_currency.find((c) => c.name === "USD") || {}).share || 0;
    const t = d.totals;
    const gainCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
    const sign = (v) => (v > 0 ? "+" : "");
    const shareRows = (list, labelFn) => list.length ? list.map((g) => `<div class="bar-row">
        <div class="bar-top"><span class="bar-name">${labelFn(g)}</span>
        <span class="num"><b>${mv(g.value)}</b> <span class="muted">${pct(g.share)}</span></span></div>
        <div class="meter"><span style="width:${g.share}%"></span></div></div>`).join("")
      : `<div class="empty">Sin inversiones</div>`;

    const holdingRows = d.holdings.map((h) => {
      const open = state._invOpen === h.id;
      return `<tr class="${h.value > 0 ? "" : "muted"}">
        <td><b>${esc(h.name)}</b><div class="muted" style="font-size:12px">${esc(h.asset_icon)} ${esc(h.asset_label)}${h.notes ? ` · ${esc(h.notes)}` : ""}</div></td>
        <td>${h.platform ? `<span class="tag">${esc(h.platform)}</span>` : '<span class="muted">—</span>'}
          ${h.currency !== "COP" ? `<span class="tag">${esc(h.currency)}</span>` : ""}</td>
        <td class="right num">${mv(h.invested)}${native(h, h.invested_native)}</td>
        <td class="right num"><b>${mv(h.value)}</b>${native(h, h.value_native)}${h.last_update ? `<div class="muted" style="font-size:12px">act. ${fmtDate(h.last_update)}</div>` : ""}</td>
        <td class="right num ${gainCls(h.gain)}">${sign(h.gain)}${mv(h.gain)}<div style="font-size:12px">${sign(h.gain_pct)}${pct(h.gain_pct)}</div></td>
        <td class="right num">${pct(h.share)}</td>
        <td class="row-actions">
          <button class="btn btn-sm btn-primary" data-act="inv-value" data-id="${h.id}" title="Actualizar el valor actual">Actualizar</button>
          <button class="icon-btn small" data-act="inv-add" data-id="${h.id}" title="Aportar">➕</button>
          <button class="icon-btn small" data-act="inv-sub" data-id="${h.id}" title="Retirar">➖</button>
          <button class="icon-btn small" data-act="inv-hist" data-id="${h.id}" title="Historial">🕘</button>
          <button class="icon-btn small" data-act="inv-edit" data-id="${h.id}" title="Editar">✏️</button>
          <button class="icon-btn small" data-act="inv-del" data-id="${h.id}" title="Eliminar">🗑️</button>
        </td></tr>
        ${open ? `<tr><td colspan="7" style="background:var(--surface-2)" id="invHist">Cargando historial…</td></tr>` : ""}`;
    }).join("");

    const firstIdx = d.history.findIndex((x) => x.value > 0 || x.invested > 0);
    const hasHistory = firstIdx >= 0;
    // Desde el mes anterior a la primera inversión (mínimo 2 puntos)
    if (hasHistory) d.history = d.history.slice(Math.max(0, Math.min(firstIdx - 1, d.history.length - 2)));
    content.innerHTML = `
      <div class="kpis">
        <div class="panel kpi"><div class="label">Valor del portafolio</div><div class="value num">${mv(t.value)}</div>
          <div class="delta">Al cierre de ${esc(monthName(state.month))}</div></div>
        <div class="panel kpi"><div class="label">Total aportado</div><div class="value num">${mv(t.invested)}</div>
          <div class="delta">Capital que has puesto</div></div>
        <div class="panel kpi"><div class="label">Ganancia / pérdida</div>
          <div class="value num ${gainCls(t.gain)}">${sign(t.gain)}${mv(t.gain)}</div>
          <div class="delta"><b class="${gainCls(t.gain_pct)}">${sign(t.gain_pct)}${pct(t.gain_pct)}</b> sobre lo aportado</div></div>
        <div class="panel kpi"><div class="label">Posiciones</div><div class="value num">${t.positions}</div>
          <div class="delta">En ${t.platforms} aplicación(es)${usdShare ? ` · ${pct(usdShare)} en USD` : ""}</div></div>
      </div>
      <div class="panel mb fx-bar">
        <div class="seg" role="group" aria-label="Moneda para ver el portafolio">
          ${["COP", "USD"].map((c) => `<label><input type="radio" name="invDisplay" value="${c}" data-act="inv-display" data-id="${c}"${d.display === c ? " checked" : ""}>Ver en ${c}</label>`).join("")}
        </div>
        <span>TRM de ${esc(monthName(state.month))}: <b class="num">${d.rate ? fmtRate(d.rate) : "sin definir"}</b>
          <span class="muted">(pesos por dólar)</span></span>
        <span class="grow"></span>
        <button class="btn btn-sm" data-act="fx-edit">✏️ Escribir TRM</button>
        <button class="btn btn-sm" data-act="fx-official">🌐 Traer TRM oficial</button>
      </div>
      ${d.needs_rate ? `<div class="alert warning mb">⚠️ Tienes inversiones en otra moneda: define la TRM para convertirlas; mientras tanto no suman al total.</div>` : ""}
      <div class="toolbar"><span class="grow muted">Registra cada inversión con su aplicación y tipo. Usa <b>Actualizar</b> cuando revises cuánto vale hoy;
        con <b>➕/➖</b> registras aportes y retiros.</span>
        <button class="btn btn-primary" data-act="inv-new">+ Nueva inversión</button></div>
      ${d.holdings.length ? `
      <div class="grid grid-2 mb">
        <div class="panel"><h3>Por tipo de activo</h3><p class="sub">Cómo está distribuido tu portafolio</p>
          <div class="bar-list">${shareRows(d.by_type, (g) => `${esc(g.icon)} ${esc(g.label)}`)}</div></div>
        <div class="panel"><h3>Por aplicación</h3><p class="sub">Dónde tienes tu dinero invertido</p>
          <div class="bar-list">${shareRows(d.by_platform, (g) => esc(g.name))}</div></div>
      </div>
      ${hasHistory ? `<div class="panel mb"><h3>Evolución del portafolio</h3><p class="sub">Valor al cierre de cada mes comparado con lo aportado</p>
        <div class="legend"><span><i style="background:var(--series-1)"></i>Valor</span><span><i style="background:var(--muted)"></i>Aportado</span></div>
        <div class="chart-box"><canvas id="invChart" role="img" aria-label="Evolución del valor del portafolio"></canvas></div></div>` : ""}
      <div class="panel" style="padding:0"><div class="table-wrap"><table>
        <thead><tr><th>Inversión</th><th>Aplicación</th><th class="right">Aportado</th><th class="right">Valor actual</th>
          <th class="right">Rendimiento</th><th class="right">% portafolio</th><th></th></tr></thead>
        <tbody>${holdingRows}</tbody></table></div></div>`
      : `<div class="panel empty"><div class="big">📈</div><p>Aún no tienes inversiones registradas${state.month !== currentMonth() ? " a este mes" : ""}.</p>
         <button class="btn btn-primary" data-act="inv-new">+ Registrar inversión</button></div>`}`;

    if (hasHistory && window.Chart && $("#invChart")) {
      charts.push(new Chart($("#invChart"), {
        type: "line",
        data: {
          labels: d.history.map((x) => monthName(x.month, true)),
          datasets: [
            { label: "Valor", data: d.history.map((x) => x.value), borderColor: cssVar("--series-1"),
              backgroundColor: cssVar("--series-1"), borderWidth: 2, pointRadius: 3, pointHoverRadius: 5, tension: 0.2 },
            { label: "Aportado", data: d.history.map((x) => x.invested), borderColor: cssVar("--muted"),
              backgroundColor: cssVar("--muted"), borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
          ],
        },
        options: { ...chartBase(),
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${mv(ctx.parsed.y)}` } } },
          scales: { ...chartBase().scales, y: { ...chartBase().scales.y,
            ticks: { ...chartBase().scales.y.ticks, callback: (v) => money(v, true, d.display) } } },
          onClick: (_e, els) => { if (els.length) setMonth(d.history[els[0].index].month); } },
      }));
    }
    if (state._invOpen && $("#invHist")) loadInvestmentHistory(state._invOpen);
  }

  const MOVE_LABELS = { contribution: "Aporte", withdrawal: "Retiro", valuation: "Actualización de valor" };

  async function loadInvestmentHistory(id) {
    const cell = $("#invHist");
    try {
      const moves = await api("GET", `/api/investments/${id}/moves`);
      cell.innerHTML = `<b>Historial</b><table style="margin-top:6px"><thead><tr><th>Fecha</th><th>Movimiento</th>
        <th class="right">Monto</th><th class="right">Valor después</th><th>Notas</th><th></th></tr></thead><tbody>
        ${moves.map((m) => `<tr><td class="num">${fmtDate(m.date)} ${m.date.slice(0, 4)}</td><td>${MOVE_LABELS[m.kind]}</td>
          <td class="right num">${m.kind === "withdrawal" ? "−" : m.kind === "contribution" ? "+" : ""}${money(m.amount)}</td>
          <td class="right num">${money(m.value_after)}</td><td class="muted">${esc(m.notes)}</td>
          <td class="row-actions"><button class="icon-btn small" data-act="inv-move-del" data-id="${m.id}" title="Eliminar">🗑️</button></td></tr>`).join("")}
        </tbody></table>`;
    } catch (err) { cell.textContent = err.message; }
  }

  function investmentFields(withAmounts) {
    const fields = [
      { name: "name", label: "Nombre de la inversión", type: "text", full: true, placeholder: "Ej: ETF S&P 500, CDT 360 días" },
      { name: "platform", label: "Aplicación / entidad", type: "text", placeholder: "Ej: Trii",
        datalist: [...new Set([...(state._inv?.platforms || []), ...PLATFORM_SUGGESTIONS])] },
      { name: "asset_type", label: "Tipo de activo", type: "select",
        options: (state._inv?.asset_types || []).map((a) => ({ value: a.value, label: `${a.icon} ${a.label}` })) },
    ];
    if (withAmounts) {
      fields.push(
        { name: "currency", label: "Moneda de la inversión", type: "seg", full: true,
          options: [{ value: "COP", label: "Pesos (COP)" }, { value: "USD", label: "Dólares (USD)" }],
          hint: (v) => (v.currency === "USD" ? "Los montos se registran en dólares y se convierten con la TRM" : "") },
        { name: "invested", label: "Total aportado", type: "number", step: "any", min: 0,
          hint: (v) => `En ${v.currency === "USD" ? "dólares" : "pesos"}: cuánto dinero has puesto` },
        { name: "value", label: "Valor actual", type: "number", step: "any", min: 0,
          hint: (v) => `En ${v.currency === "USD" ? "dólares" : "pesos"}: cuánto vale hoy` },
        { name: "date", label: "Fecha", type: "date", hint: "Desde cuándo la tienes (o la fecha de hoy)" });
    }
    fields.push({ name: "notes", label: "Notas", type: "textarea", full: true });
    return fields;
  }

  function openInvestmentForm(h) {
    openForm({
      title: h ? "Editar inversión" : "Nueva inversión",
      values: h ? { ...h } : { asset_type: "etf", currency: "COP", date: defaultDateForMonth(state.month) },
      fields: investmentFields(!h),
      onChange: (name, v, set) => { if (name === "invested" && !h) set("value", v.invested); },
      onSubmit: async (data) => {
        if (h) await api("PUT", `/api/investments/${h.id}`, data);
        else await api("POST", "/api/investments", data);
        toast(h ? "Inversión actualizada" : "Inversión registrada");
      },
    });
  }

  function openInvestmentMove(id, kind) {
    const h = state._inv.holdings.find((x) => x.id === Number(id));
    const titles = { contribution: `Aportar a ${h.name}`, withdrawal: `Retirar de ${h.name}`,
      valuation: `Actualizar valor de ${h.name}` };
    openForm({
      title: titles[kind],
      submitLabel: kind === "valuation" ? "Actualizar" : "Guardar",
      values: { date: defaultDateForMonth(state.month), amount: kind === "valuation" ? h.value_native : "" },
      fields: [
        { name: "amount", label: `${kind === "valuation" ? "Valor actual" : "Monto"} (${h.currency})`, type: "number", step: "any", min: 0,
          hint: kind === "valuation" ? `Valor anterior: ${money(h.value_native, false, h.currency)} · Aportado: ${money(h.invested_native, false, h.currency)}` : "" },
        { name: "date", label: "Fecha", type: "date" },
        { name: "notes", label: "Notas", type: "textarea", full: true },
      ],
      onSubmit: async (data) => {
        await api("POST", `/api/investments/${id}/moves`, { ...data, kind });
        toast(kind === "valuation" ? "Valor actualizado" : kind === "contribution" ? "Aporte registrado" : "Retiro registrado");
      },
    });
  }

  // ------------------------------------------------------------------ ajustes
  async function renderSettings() {
    const info = await api("GET", "/api/info").catch(() => ({ db_path: "" }));
    content.innerHTML = `
      <div class="grid grid-2">
        <div class="panel"><h3>Moneda</h3><p class="sub">Formato en el que se muestran los montos</p>
          <div class="toolbar"><select id="currency">
            ${["COP", "USD", "EUR", "MXN", "ARS", "CLP", "PEN"].map((c) => `<option${c === state.settings.currency ? " selected" : ""}>${c}</option>`).join("")}
          </select></div></div>
        <div class="panel"><h3>Respaldo</h3><p class="sub">Todos tus datos viven en este archivo de tu equipo:<br>
          <code style="word-break:break-all">${esc(info.db_path)}</code><br>Descarga una copia de seguridad de vez en cuando.</p>
          <div class="toolbar">
            <a class="btn" href="/api/backup">⬇ Descargar respaldo</a>
            <label class="btn">⬆ Restaurar respaldo<input type="file" id="restoreFile" accept=".db" hidden></label>
          </div>
          <p class="sub" style="margin:0">Restaurar reemplaza <b>todos</b> los datos actuales por los del archivo. Sirve para pasar tus datos a otro computador.</p></div>
        <div class="panel"><h3>Mes de los ingresos</h3>
          <p class="sub">Si te pagan al final del mes (por ejemplo el penúltimo día hábil), los ingresos recibidos desde este día
            se asignarán automáticamente al mes siguiente. Siempre puedes cambiarlo en cada movimiento con "Corresponde al mes".</p>
          <div class="toolbar"><label for="incomeShift">Desde el día</label>
            <input type="number" id="incomeShift" min="1" max="31" style="width:90px;min-width:0" placeholder="—"
              value="${esc(state.settings.income_shift_day || "")}">
            <span class="muted">Vacío = cada ingreso cuenta en el mes de su fecha</span></div></div>
        <div class="panel"><h3>Datos de ejemplo</h3><p class="sub">¿Quieres probar la app con datos ficticios? Ejecuta <code>python seed_demo.py</code> con la app cerrada.</p></div>
        <div class="panel"><h3>Atajos de teclado</h3><p class="sub" style="margin:0">
          <b>N</b>: nuevo movimiento · <b>←</b>/<b>→</b>: mes anterior/siguiente · <b>T</b>: volver al mes actual</p></div>
      </div>`;
    $("#restoreFile").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      e.target.value = "";
      if (!file || !confirmAction(`¿Reemplazar todos tus datos actuales por los de "${file.name}"? Esto no se puede deshacer.`)) return;
      const form = new FormData();
      form.append("file", file);
      try {
        const res = await fetch("/api/restore", { method: "POST", body: form });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || `Error ${res.status}`);
        state.settings = await api("GET", "/api/settings");
        toast("Respaldo restaurado");
      } catch (err) { toast(err.message, true); }
    });
    $("#incomeShift").addEventListener("change", async (e) => {
      try {
        state.settings = await api("PUT", "/api/settings", { income_shift_day: e.target.value });
        toast(e.target.value ? `Ingresos desde el día ${e.target.value} contarán para el mes siguiente` : "Ajuste desactivado");
      } catch (err) { toast(err.message, true); }
    });
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
    "new-tx": () => openTransactionForm(null, state.flow === "income" ? { type: "income", payment_method: "transfer" } : {}),
    "edit-tx": (id) => openTransactionForm(state._txList.find((t) => t.id === Number(id))),
    "del-tx": (id) => removeItem("¿Eliminar este movimiento?", `/api/transactions/${id}`, "Movimiento eliminado"),
    "new-card": (_id, el) => openCardForm(null, el.dataset.type || "credit"),
    "edit-card": (id) => openCardForm(findCard(id)),
    "del-card": (id) => removeItem("¿Eliminar esta tarjeta? Sus movimientos se conservarán pero quedarán sin tarjeta asociada.",
      `/api/cards/${id}`, "Tarjeta eliminada"),
    "pay-card": (id) => {
      const c = findCard(id);
      openTransactionForm(null, { type: "card_payment", card_id: c.id, payment_method: "transfer",
        amount: c.balance > 0 ? c.balance : "", description: `Pago ${c.name}` });
    },
    "buy-card": (id) => openTransactionForm(null, { type: "expense", payment_method: "card", card_id: Number(id) }),
    "buy-debit": (id) => openTransactionForm(null, { type: "expense", payment_method: "debit", card_id: Number(id) }),
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
    "reg-rec": (id) => {
      const r = state._recList.find((x) => x.id === Number(id));
      openTransactionForm(null, {
        recurring_id: r.id, type: r.type, amount: r.amount, date: r.expected_date || dayInMonth(state.month, r.day), period: state.month,
        description: r.description, category_id: r.category_id ?? "", payment_method: r.payment_method,
        card_id: r.card_id ?? "", notes: "Gasto fijo",
      });
    },
    "gen-recurring": async () => {
      try {
        const r = await api("POST", "/api/recurring/generate",
          { month: state.month, type: state.flow === "all" ? "" : state.flow });
        toast(`${r.created} movimiento(s) registrado(s)`);
        render();
      } catch (err) { toast(err.message, true); }
    },
    "new-goal": () => openGoalForm(null),
    "inv-new": () => openInvestmentForm(null),
    "inv-display": (cur) => { state.invDisplay = cur; storageSet("finanzas.invDisplay", cur); render(); },
    "fx-edit": () => openForm({
      title: `TRM de ${monthName(state.month)}`,
      values: { rate: state._inv?.rate || "" },
      fields: [{ name: "rate", label: "Pesos colombianos por 1 dólar", type: "number", step: "any", min: 0, full: true,
        hint: "Se usa para este mes y los siguientes hasta que definas otra. Vacío = quitar la de este mes." }],
      onSubmit: async (data) => {
        await api("PUT", "/api/fx-rates", { month: state.month, rate: data.rate });
        toast("TRM guardada");
      },
    }),
    "fx-official": async () => {
      try {
        const r = await api("POST", "/api/fx-rates/official", { month: state.month });
        toast(`TRM oficial del ${fmtDate(r.date)}: ${fmtRate(r.rate)}`);
        render();
      } catch (err) { toast(err.message, true); }
    },
    "inv-edit": (id) => openInvestmentForm(state._inv.holdings.find((h) => h.id === Number(id))),
    "inv-del": (id) => removeItem("¿Eliminar esta inversión y todo su historial?", `/api/investments/${id}`, "Inversión eliminada"),
    "inv-value": (id) => openInvestmentMove(id, "valuation"),
    "inv-add": (id) => openInvestmentMove(id, "contribution"),
    "inv-sub": (id) => openInvestmentMove(id, "withdrawal"),
    "inv-hist": (id) => { state._invOpen = state._invOpen === Number(id) ? null : Number(id); render(); },
    "inv-move-del": (id) => removeItem("¿Eliminar este movimiento del historial?", `/api/investments/moves/${id}`, "Movimiento eliminado"),
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
  $("#quickAdd").addEventListener("click", () =>
    openTransactionForm(null, state.flow === "income" ? { type: "income", payment_method: "transfer" } : {}));
  $("#flowToggle").addEventListener("change", (e) => {
    state.flow = e.target.value;
    storageSet("finanzas.flow", state.flow);
    Object.assign(state.txFilters, { type: "", category_id: "" });
    render();
  });
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
  if (storageGet("finanzas.invDisplay") === "USD") state.invDisplay = "USD";
  if (["expense", "income"].includes(storageGet("finanzas.flow"))) state.flow = storageGet("finanzas.flow");
  api("GET", "/api/settings")
    .then((s) => { state.settings = s; })
    .catch(() => {})
    .finally(routeFromHash);
})();
