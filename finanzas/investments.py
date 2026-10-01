"""Cálculos del portafolio de inversiones a partir del historial de movimientos."""
from collections import defaultdict

ASSET_TYPES = {
    "acciones": ("Acciones", "📈"),
    "etf": ("ETF / Fondos indexados", "🧺"),
    "renta_fija": ("CDT / Renta fija", "🏦"),
    "fic": ("Fondo de inversión colectiva", "💼"),
    "cripto": ("Criptomonedas", "🪙"),
    "pension": ("Pensión voluntaria", "🧓"),
    "ahorro": ("Cuenta de ahorro / Bolsillo", "💵"),
    "finca_raiz": ("Finca raíz", "🏠"),
    "otro": ("Otro", "📦"),
}
MOVE_KINDS = ("contribution", "withdrawal", "valuation")
CURRENCIES = ("COP", "USD")


def apply_move(state, kind, amount):
    """Actualiza {'invested', 'value'} de una posición con un movimiento."""
    if kind == "contribution":
        state["invested"] += amount
        state["value"] += amount
    elif kind == "withdrawal":
        # Un retiro saca capital aportado en la misma proporción que el valor retirado
        if state["value"] > 0:
            ratio = min(amount / state["value"], 1)
            state["invested"] -= state["invested"] * ratio
        state["value"] = max(state["value"] - amount, 0)
    else:
        state["value"] = amount
    return state


def positions_at(conn, before):
    """Estado de cada posición con los movimientos de fecha < `before` (AAAA-MM-DD)."""
    states = defaultdict(lambda: {"invested": 0.0, "value": 0.0, "last_update": None, "moves": 0})
    for inv_id, kind, amount, move_date in conn.execute(
        """SELECT investment_id, kind, amount, date FROM investment_moves
           WHERE date < ? ORDER BY date, id""",
        (before,),
    ):
        st = apply_move(states[inv_id], kind, amount)
        st["last_update"] = move_date
        st["moves"] += 1
    return states


def group_share(items, key, total):
    groups = defaultdict(float)
    for it in items:
        groups[key(it)] += it["value"]
    return sorted(
        ({"name": name, "value": round(v, 2), "share": round(v / total * 100, 1) if total else 0}
         for name, v in groups.items() if v > 0),
        key=lambda g: g["value"], reverse=True,
    )


def rate_for(conn, month):
    """TRM (pesos por dólar) vigente para `month`: la del mes o la última anterior."""
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE month <= ? ORDER BY month DESC LIMIT 1", (month,)
    ).fetchone()
    return row[0] if row else None


def convert(amount, currency, display, rate):
    """Convierte entre COP y USD. Sin TRM, lo que requiere conversión vale 0."""
    if currency == display:
        return amount
    if not rate:
        return 0.0
    return amount * rate if display == "COP" else amount / rate


def total_value(conn, month, month_end, display="COP"):
    """Valor total del portafolio al cierre de `month` en la moneda `display`."""
    rate = rate_for(conn, month)
    currencies = dict(conn.execute("SELECT id, currency FROM investments"))
    return round(sum(convert(st["value"], currencies.get(i, "COP"), display, rate)
                     for i, st in positions_at(conn, month_end).items()), 2)


def portfolio(conn, month, month_end, history_months, display="COP"):
    """Portafolio valorado al cierre de `month` (month_end = primer día del mes siguiente).

    Cada posición se lleva en su propia moneda; los totales y la distribución se
    expresan en `display` usando la TRM del mes. history_months: lista de
    (mes, primer día del mes siguiente) para la evolución.
    """
    rate = rate_for(conn, month)
    states = positions_at(conn, month_end)
    holdings = []
    for inv in conn.execute("SELECT * FROM investments ORDER BY name"):
        inv = dict(inv)
        st = states.get(inv["id"])
        if not st:
            continue  # aún no existía en este mes
        label, icon = ASSET_TYPES.get(inv["asset_type"], ASSET_TYPES["otro"])
        cur = inv["currency"]
        inv.update(
            invested_native=round(st["invested"], 2),
            value_native=round(st["value"], 2),
            gain_native=round(st["value"] - st["invested"], 2),
            # Rendimiento en la moneda de la inversión (sin efecto del tipo de cambio)
            gain_pct=round((st["value"] - st["invested"]) / st["invested"] * 100, 2)
            if st["invested"] > 0 else 0,
            invested=round(convert(st["invested"], cur, display, rate), 2),
            value=round(convert(st["value"], cur, display, rate), 2),
            last_update=st["last_update"],
            asset_label=label,
            asset_icon=icon,
        )
        inv["gain"] = round(inv["value"] - inv["invested"], 2)
        holdings.append(inv)

    total = sum(h["value"] for h in holdings)
    total_invested = sum(h["invested"] for h in holdings)
    for h in holdings:
        h["share"] = round(h["value"] / total * 100, 1) if total else 0
    holdings.sort(key=lambda h: h["value"], reverse=True)

    currencies = {h["id"]: h["currency"] for h in holdings}
    currencies.update(dict(conn.execute("SELECT id, currency FROM investments")))
    history = []
    for m, end in history_months:
        r = rate_for(conn, m)
        sts = positions_at(conn, end)
        history.append({
            "month": m,
            "value": round(sum(convert(s["value"], currencies.get(i, "COP"), display, r)
                               for i, s in sts.items()), 2),
            "invested": round(sum(convert(s["invested"], currencies.get(i, "COP"), display, r)
                                  for i, s in sts.items()), 2),
        })

    by_currency = defaultdict(float)
    for h in holdings:
        by_currency[h["currency"]] += h["value"]

    return {
        "month": month,
        "display": display,
        "rate": rate,
        "needs_rate": rate is None and any(h["currency"] != display for h in holdings),
        "holdings": holdings,
        "totals": {
            "value": round(total, 2),
            "invested": round(total_invested, 2),
            "gain": round(total - total_invested, 2),
            "gain_pct": round((total - total_invested) / total_invested * 100, 2)
            if total_invested > 0 else 0,
            "positions": sum(1 for h in holdings if h["value_native"] > 0),
            "platforms": len({h["platform"] for h in holdings if h["value_native"] > 0}),
        },
        "by_type": [
            {**g, "icon": ASSET_TYPES.get(g["name"], ASSET_TYPES["otro"])[1],
             "label": ASSET_TYPES.get(g["name"], ASSET_TYPES["otro"])[0]}
            for g in group_share(holdings, lambda h: h["asset_type"], total)
        ],
        "by_platform": group_share(holdings, lambda h: h["platform"] or "Sin aplicación", total),
        "by_currency": [
            {"name": c, "value": round(v, 2), "share": round(v / total * 100, 1) if total else 0}
            for c, v in sorted(by_currency.items(), key=lambda x: -x[1]) if v > 0
        ],
        "history": history,
    }
