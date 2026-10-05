"""Compras con tarjeta a cuotas: plan de pagos mes a mes.

Una compra "a cuotas" (financed=1) no cuenta completa en el mes de la compra: cada mes
cuenta la cuota que se paga (capital + intereses), en el mes de la fecha límite de pago
del corte que la cobra. El capital se divide en partes iguales y el interés se calcula
sobre el saldo pendiente con la tasa mensual de la compra, como lo hacen las tarjetas
en Colombia. Los abonos a capital (pagos a la tarjeta con applies_to) reducen el saldo
y el resto se reparte en las cuotas que faltan.
"""


def interest_category_id(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'interest_category_id'").fetchone()
    return int(row[0]) if row else None


def first_statement_month(card, purchase_date):
    """Mes del primer corte que cobra una compra hecha en `purchase_date`."""
    from .app import shift_month

    month = purchase_date[:7]
    if card.get("cut_day") and int(purchase_date[8:10]) > card["cut_day"]:
        return shift_month(month, 1)
    return month


def regenerate(conn, purchase_id):
    """Recalcula el plan de una compra (o lo borra si ya no es a cuotas)."""
    from .app import card_dates, day_in_month, shift_month

    conn.execute("DELETE FROM installment_schedule WHERE purchase_id = ?", (purchase_id,))
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (purchase_id,)).fetchone()
    if not tx or not (tx["financed"] and tx["type"] == "expense" and tx["installments"] > 1
                      and tx["card_id"]):
        return
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (tx["card_id"],)).fetchone()
    if not card:
        return
    card = dict(card)
    rate = (tx["rate"] or 0) / 100
    n = tx["installments"]
    prepayments = [dict(r) for r in conn.execute(
        """SELECT date, amount FROM transactions
           WHERE applies_to = ? AND type = 'card_payment' ORDER BY date, id""", (purchase_id,))]

    balance = tx["amount"]
    month = first_statement_month(card, tx["date"])
    for number in range(1, n + 1):
        cut, due = card_dates(card, month)
        statement = cut or day_in_month(month, 31)
        due = due or statement
        # Los abonos hechos antes de este corte reducen el capital pendiente
        while prepayments and prepayments[0]["date"] <= statement:
            balance -= prepayments.pop(0)["amount"]
        if balance <= 0.005:
            break
        capital = balance / (n - number + 1)
        interest = balance * rate
        conn.execute(
            """INSERT INTO installment_schedule
                   (purchase_id, number, statement_date, due_date, period, capital, interest)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (purchase_id, number, statement, due, due[:7], round(capital, 2), round(interest, 2)),
        )
        balance -= capital
        month = shift_month(month, 1)


def regenerate_card(conn, card_id):
    for (tx_id,) in conn.execute(
            "SELECT id FROM transactions WHERE card_id = ? AND financed = 1", (card_id,)).fetchall():
        regenerate(conn, tx_id)


def plans_for_card(conn, card_id, month):
    """Compras a cuotas de una tarjeta vistas desde `month` (las que siguen vivas)."""
    plans = []
    for tx in conn.execute(
            """SELECT * FROM transactions WHERE card_id = ? AND financed = 1
               AND type = 'expense' AND installments > 1 ORDER BY date""", (card_id,)):
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM installment_schedule WHERE purchase_id = ? ORDER BY number", (tx["id"],))]
        if not rows or rows[-1]["period"] < month or tx["period"] > month:
            continue  # ya terminó o aún no se ha hecho la compra
        current = next((r for r in rows if r["period"] == month), None)
        pending = sum(r["capital"] for r in rows if r["period"] > month)
        plans.append({
            "id": tx["id"], "description": tx["description"], "date": tx["date"],
            "amount": tx["amount"], "installments": tx["installments"], "rate": tx["rate"] or 0,
            "current_number": current["number"] if current else None,
            "current_payment": round(current["capital"] + current["interest"], 2) if current else 0,
            "pending_capital": round(pending, 2),
            "remaining": sum(1 for r in rows if r["period"] > month),
            "last_period": rows[-1]["period"],
            "total_interest": round(sum(r["interest"] for r in rows), 2),
        })
    return plans
