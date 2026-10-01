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


def portfolio(conn, month, month_end, history_months):
    """Portafolio valorado al cierre de `month` (month_end = primer día del mes siguiente).

    history_months: lista de (mes, primer día del mes siguiente) para la evolución.
    """
    states = positions_at(conn, month_end)
    holdings = []
    for inv in conn.execute("SELECT * FROM investments ORDER BY name"):
        inv = dict(inv)
        st = states.get(inv["id"])
        if not st:
            continue  # aún no existía en este mes
        label, icon = ASSET_TYPES.get(inv["asset_type"], ASSET_TYPES["otro"])
        inv.update(
            invested=round(st["invested"], 2),
            value=round(st["value"], 2),
            gain=round(st["value"] - st["invested"], 2),
            gain_pct=round((st["value"] - st["invested"]) / st["invested"] * 100, 2)
            if st["invested"] > 0 else 0,
            last_update=st["last_update"],
            asset_label=label,
            asset_icon=icon,
        )
        holdings.append(inv)

    total_value = sum(h["value"] for h in holdings)
    total_invested = sum(h["invested"] for h in holdings)
    for h in holdings:
        h["share"] = round(h["value"] / total_value * 100, 1) if total_value else 0
    holdings.sort(key=lambda h: h["value"], reverse=True)

    history = []
    for m, end in history_months:
        sts = positions_at(conn, end).values()
        history.append({"month": m,
                        "value": round(sum(s["value"] for s in sts), 2),
                        "invested": round(sum(s["invested"] for s in sts), 2)})

    return {
        "month": month,
        "holdings": holdings,
        "totals": {
            "value": round(total_value, 2),
            "invested": round(total_invested, 2),
            "gain": round(total_value - total_invested, 2),
            "gain_pct": round((total_value - total_invested) / total_invested * 100, 2)
            if total_invested > 0 else 0,
            "positions": sum(1 for h in holdings if h["value"] > 0),
            "platforms": len({h["platform"] for h in holdings if h["value"] > 0}),
        },
        "by_type": [
            {**g, "icon": ASSET_TYPES.get(g["name"], ASSET_TYPES["otro"])[1],
             "label": ASSET_TYPES.get(g["name"], ASSET_TYPES["otro"])[0]}
            for g in group_share(holdings, lambda h: h["asset_type"], total_value)
        ],
        "by_platform": group_share(holdings, lambda h: h["platform"] or "Sin aplicación",
                                   total_value),
        "history": history,
    }
