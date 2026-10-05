"""Carga datos ficticios para probar la app (últimos 6 meses).

Uso:  python seed_demo.py [--db ruta.db] [--force]
Solo carga los datos si la base no tiene movimientos, salvo que uses --force.
"""
import argparse
import random
from datetime import date

from finanzas import db
from finanzas.app import day_in_month, shift_month


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    db.init_db(args.db)
    conn = db.connect(args.db)
    if conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] and not args.force:
        print("La base ya tiene movimientos. Usa --force para agregar datos de ejemplo igual.")
        return

    rnd = random.Random(42)
    cat = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    budgets = {"Vivienda": 1_900_000, "Servicios": 450_000, "Mercado": 1_100_000,
               "Restaurantes": 500_000, "Transporte": 450_000, "Entretenimiento": 250_000,
               "Suscripciones": 120_000, "Salud": 200_000, "Ropa": 200_000}
    for name, amount in budgets.items():
        conn.execute("UPDATE categories SET default_budget = ? WHERE id = ?", (amount, cat[name]))

    cards = []
    for name, bank, limit, initial, cut, due, color, last4 in [
        ("Visa Platinum", "Bancolombia", 8_000_000, 1_200_000, 15, 30, "#2a78d6", "4821"),
        ("Mastercard Black", "Davivienda", 5_000_000, 0, 5, 20, "#4a3aa7", "9034"),
    ]:
        cur = conn.execute(
            """INSERT INTO cards (name, bank, last4, credit_limit, initial_balance, cut_day,
                   due_day, interest_rate, color) VALUES (?, ?, ?, ?, ?, ?, ?, 2.1, ?)""",
            (name, bank, last4, limit, initial, cut, due, color))
        cards.append(cur.lastrowid)
    debit_card = conn.execute(
        """INSERT INTO cards (name, bank, last4, color, kind)
           VALUES ('Débito Ahorros', 'Bancolombia', '7712', '#1baf7a', 'debit')""").lastrowid

    rec = [
        ("Salario", 6_500_000, "income", "Salario", "transfer", None, 25),
        ("Arriendo", 1_800_000, "expense", "Vivienda", "transfer", None, 5),
        ("Internet y celular", 150_000, "expense", "Servicios", "debit", None, 10),
        ("Netflix + Spotify", 65_000, "expense", "Suscripciones", "card", cards[0], 12),
        ("Gimnasio", 110_000, "expense", "Salud", "card", cards[1], 3),
    ]
    rec_ids = []
    for desc, amount, rtype, cname, method, card, day in rec:
        cur = conn.execute(
            """INSERT INTO recurring (description, amount, type, category_id, payment_method,
                   card_id, day) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (desc, amount, rtype, cat[cname], method, card, day))
        rec_ids.append(cur.lastrowid)

    variable = [
        ("Mercado", ["Éxito", "D1", "Carulla", "Ara"], 60_000, 280_000, 5),
        ("Restaurantes", ["Almuerzo", "Crepes & Waffles", "Domicilio Rappi", "Café"], 15_000, 120_000, 6),
        ("Transporte", ["Gasolina", "Uber", "Peajes", "Parqueadero"], 10_000, 150_000, 5),
        ("Entretenimiento", ["Cine", "Concierto", "Bar"], 30_000, 180_000, 2),
        ("Servicios", ["Energía", "Agua", "Gas"], 40_000, 120_000, 3),
        ("Ropa", ["Zara", "Tennis", "Adidas"], 80_000, 300_000, 1),
        ("Salud", ["Droguería", "Consulta"], 20_000, 150_000, 1),
    ]

    today = date.today()
    this_month = today.strftime("%Y-%m")
    for back in range(5, -1, -1):
        month = shift_month(this_month, -back)
        last_day = today.day if month == this_month else 28
        for rid, (desc, amount, rtype, cname, method, card, day) in zip(rec_ids, rec):
            if month == this_month and day > today.day:
                continue
            conn.execute(
                """INSERT INTO transactions (date, description, amount, type, category_id,
                       payment_method, card_id, notes, recurring_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'Gasto fijo', ?)""",
                (day_in_month(month, day), desc, amount, rtype, cat[cname], method, card, rid))
        for cname, names, lo, hi, count in variable:
            for _ in range(count + rnd.randint(-1, 2)):
                use_card = rnd.random() < 0.45
                method = "card" if use_card else rnd.choice(["debit", "cash"])
                conn.execute(
                    """INSERT INTO transactions (date, description, amount, type, category_id,
                           payment_method, card_id, installments)
                       VALUES (?, ?, ?, 'expense', ?, ?, ?, ?)""",
                    (day_in_month(month, rnd.randint(1, max(last_day, 1))), rnd.choice(names),
                     round(rnd.uniform(lo, hi), -3), cat[cname],
                     method,
                     rnd.choice(cards) if use_card else debit_card if method == "debit" else None,
                     rnd.choice([1, 1, 1, 3, 6]) if use_card else 1))
        if month != this_month or today.day > 20:
            conn.execute(
                """INSERT INTO transactions (date, description, amount, type, category_id,
                       payment_method) VALUES (?, 'Freelance', ?, 'income', ?, 'transfer')""",
                (day_in_month(month, 18), rnd.choice([0, 400_000, 800_000]) or 250_000,
                 cat["Ingresos extra"]))
        if month != this_month or today.day >= 20:
            for card_id in cards:
                spent = conn.execute(
                    """SELECT COALESCE(SUM(amount), 0) FROM transactions
                       WHERE card_id = ? AND type = 'expense' AND substr(date, 1, 7) = ?""",
                    (card_id, month)).fetchone()[0]
                conn.execute(
                    """INSERT INTO transactions (date, description, amount, type, payment_method,
                           card_id) VALUES (?, 'Pago tarjeta', ?, 'card_payment', 'transfer', ?)""",
                    (day_in_month(month, 20), round(spent * rnd.uniform(0.7, 1.0), -3), card_id))

    # Compras a cuotas: con plan de pagos y tasa propia (una queda sin plan, como las antiguas)
    from finanzas import installments
    financed = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE installments > 1 ORDER BY date DESC")]
    for tx_id in financed[1:]:
        conn.execute("UPDATE transactions SET financed = 1, rate = ? WHERE id = ?",
                     (rnd.choice([0, 1.6, 1.9, 2.1]), tx_id))
        installments.regenerate(conn, tx_id)

    # Inversiones: aporte inicial hace 6 meses, aportes mensuales y valorizaciones
    for back, rate in zip(range(5, -1, -1), [4100, 4050, 3980, 3900, 3800, 3700]):
        conn.execute("INSERT OR REPLACE INTO fx_rates (month, rate) VALUES (?, ?)",
                     (shift_month(this_month, -back), rate))
    for name, platform, asset_type, initial, monthly, growth, currency in [
        ("ETF S&P 500", "Trii", "etf", 1_000, 75, 0.012, "USD"),
        ("Acciones Ecopetrol", "Trii", "acciones", 1_500_000, 0, -0.008, "COP"),
        ("CDT 360 días", "Bancolombia", "renta_fija", 5_000_000, 0, 0.009, "COP"),
        ("Fondo de inversión", "Tyba", "fic", 2_000_000, 200_000, 0.007, "COP"),
        ("Bitcoin", "Binance", "cripto", 1_000_000, 100_000, 0.03, "COP"),
        ("Cajita", "Nu", "ahorro", 3_000_000, 250_000, 0.008, "COP"),
    ]:
        inv_id = conn.execute(
            "INSERT INTO investments (name, platform, asset_type, currency) VALUES (?, ?, ?, ?)",
            (name, platform, asset_type, currency)).lastrowid
        value = invested = 0
        for back in range(5, -1, -1):
            month = shift_month(this_month, -back)
            amount = initial if back == 5 else monthly
            if amount:
                conn.execute("""INSERT INTO investment_moves (investment_id, date, kind, amount)
                                VALUES (?, ?, 'contribution', ?)""", (inv_id, f"{month}-02", amount))
                value += amount
            value = round(value * (1 + growth + rnd.uniform(-0.01, 0.01)), 2 if currency == "USD" else -3)
            conn.execute("""INSERT INTO investment_moves (investment_id, date, kind, amount)
                            VALUES (?, ?, 'valuation', ?)""",
                         (inv_id, day_in_month(month, min(28, today.day) if month == this_month else 28), value))

    for name, target, saved, deadline, color in [
        ("Fondo de emergencia", 15_000_000, 6_200_000, f"{today.year + 1}-06-30", "#1baf7a"),
        ("Viaje a Cartagena", 3_500_000, 1_100_000, f"{today.year + 1}-01-15", "#eb6834"),
    ]:
        conn.execute("INSERT INTO goals (name, target, saved, deadline, color) VALUES (?, ?, ?, ?, ?)",
                     (name, target, saved, deadline, color))
    conn.commit()
    conn.close()
    print(f"Datos de ejemplo cargados en {args.db}")


if __name__ == "__main__":
    main()
