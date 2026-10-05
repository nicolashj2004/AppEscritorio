"""API REST y servidor de la app de finanzas personales."""
import calendar
import csv
import io
import os
import re
from datetime import date

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

from . import db
from . import installments as inst
from . import investments as inv

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TX_TYPES = ("expense", "income", "card_payment")
PAYMENT_METHODS = ("cash", "debit", "transfer", "card")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- utilidades


def current_month():
    return date.today().strftime("%Y-%m")


def shift_month(month, delta):
    y, m = map(int, month.split("-"))
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_start(month):
    return f"{month}-01"


def next_month_start(month):
    return month_start(shift_month(month, 1))


def days_in_month(month):
    y, m = map(int, month.split("-"))
    return calendar.monthrange(y, m)[1]


def day_in_month(month, day):
    """Fecha del día `day` dentro de `month`, ajustada al último día si no existe."""
    return f"{month}-{min(max(int(day), 1), days_in_month(month)):02d}"


def income_shift_day(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'income_shift_day'").fetchone()
    try:
        day = int(row[0]) if row and row[0] else 0
    except ValueError:
        day = 0
    return day if 1 <= day <= 31 else 0


def default_period(conn, tx_type, tx_date):
    """Mes al que corresponde un movimiento si el usuario no lo indica.

    Los ingresos recibidos desde el día configurado cuentan para el mes siguiente
    (por ejemplo, un salario pagado al final del mes para el mes que empieza).
    """
    month = tx_date[:7]
    shift = income_shift_day(conn)
    if tx_type == "income" and shift and int(tx_date[8:10]) >= shift:
        return shift_month(month, 1)
    return month


def arg_month():
    month = request.args.get("month") or current_month()
    if not MONTH_RE.match(month):
        raise ApiError("Mes inválido, use el formato AAAA-MM")
    return month


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Se esperaba un cuerpo JSON")
    return data


def req_str(data, key, required=True, default=""):
    value = data.get(key, default)
    value = "" if value is None else str(value).strip()
    if required and not value:
        raise ApiError(f"El campo '{key}' es obligatorio")
    return value


def req_num(data, key, required=True, default=0.0, minimum=None):
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ApiError(f"El campo '{key}' es obligatorio")
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"El campo '{key}' debe ser numérico")
    if minimum is not None and value < minimum:
        raise ApiError(f"El campo '{key}' debe ser mayor o igual a {minimum}")
    return value


def opt_day(data, key):
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"El campo '{key}' debe ser un día (1-31)")
    if not 1 <= value <= 31:
        raise ApiError(f"El campo '{key}' debe ser un día (1-31)")
    return value


def opt_id(data, key):
    value = data.get(key)
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(f"El campo '{key}' es inválido")


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def ensure_exists(conn, table, row_id):
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone():
        raise ApiError("Registro no encontrado", 404)


# ------------------------------------------------------------ lógica de negocio


def due_in_next_month(card):
    """Si la fecha límite de pago cae en el mes siguiente al corte.

    Por defecto sí (corte el 10 de octubre → pago en noviembre). Si el día de pago
    es anterior o igual al de corte, siempre es el mes siguiente.
    """
    if card.get("cut_day") and card.get("due_day") and card["due_day"] <= card["cut_day"]:
        return True
    return True if card.get("due_next_month") is None else bool(card["due_next_month"])


def card_dates(card, month):
    """(fecha de corte en `month`, fecha límite de pago de ese corte)."""
    cut = day_in_month(month, card["cut_day"]) if card.get("cut_day") else None
    if not card.get("due_day"):
        return cut, None
    due_month = shift_month(month, 1) if due_in_next_month(card) else month
    return cut, day_in_month(due_month, card["due_day"])


def card_summaries(conn, month):
    """Estado de cada tarjeta al cierre de `month`."""
    end = next_month_start(month)
    start = month_start(month)
    cards = rows(conn.execute("SELECT * FROM cards ORDER BY active DESC, name"))
    for c in cards:
        agg = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN type = 'expense' AND date < ? THEN amount END), 0),
              COALESCE(SUM(CASE WHEN type = 'card_payment' AND date < ? THEN amount END), 0),
              COALESCE(SUM(CASE WHEN type = 'expense' AND date >= ? AND date < ? THEN amount END), 0),
              COALESCE(SUM(CASE WHEN type = 'card_payment' AND date >= ? AND date < ? THEN amount END), 0)
            FROM transactions WHERE card_id = ?
            """,
            (end, end, start, end, start, end, c["id"]),
        ).fetchone()
        # Los intereses de las compras a cuotas se suman a la deuda en cada corte
        interest = conn.execute(
            """SELECT COALESCE(SUM(s.interest), 0) FROM installment_schedule s
               JOIN transactions t ON t.id = s.purchase_id
               WHERE t.card_id = ? AND s.statement_date < ?""", (c["id"], end)).fetchone()[0]
        balance = c["initial_balance"] + agg[0] - agg[1] + interest
        c["balance"] = round(balance, 2)
        c["available"] = round(c["credit_limit"] - balance, 2)
        c["utilization"] = (
            round(balance / c["credit_limit"] * 100, 1) if c["credit_limit"] > 0 else 0
        )
        c["month_spent"] = agg[2]
        c["month_paid"] = agg[3]
        c["due_next_month"] = due_in_next_month(c)
        c["cut_date"], c["due_date"] = card_dates(c, month)
        c["plans"] = inst.plans_for_card(conn, c["id"], month)
        # Pago estimado del corte de este mes: cuotas del corte + compras de contado
        # hechas entre el corte anterior y este
        if c["cut_date"]:
            prev_cut = card_dates(c, shift_month(month, -1))[0]
            c["statement_payment"] = round(conn.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM transactions
                   WHERE card_id = ? AND type = 'expense'
                     AND NOT (financed = 1 AND installments > 1)
                     AND date > ? AND date <= ?""", (c["id"], prev_cut, c["cut_date"])
            ).fetchone()[0] + conn.execute(
                """SELECT COALESCE(SUM(s.capital + s.interest), 0) FROM installment_schedule s
                   JOIN transactions t ON t.id = s.purchase_id
                   WHERE t.card_id = ? AND s.statement_date = ?""", (c["id"], c["cut_date"])
            ).fetchone()[0], 2)
        else:
            c["statement_payment"] = None
        # Compras con cuotas registradas antes del plan de pagos: el usuario las revisa
        # (solo las que aún tendrían cuotas por pagar en este mes)
        c["unplanned"] = [
            u for u in rows(conn.execute(
                """SELECT id, date, description, amount, installments FROM transactions
                   WHERE card_id = ? AND type = 'expense' AND installments > 1 AND financed = 0
                     AND period <= ? ORDER BY date DESC""", (c["id"], month)))
            if shift_month(u["date"][:7], u["installments"]) >= month
        ]
        if c["kind"] == "debit":  # sin cupo ni deuda: solo se resumen sus gastos
            c.update(balance=0, available=0, utilization=0, month_paid=0, plans=[],
                     statement_payment=None, unplanned=[])
    return cards


def category_summaries(conn, month):
    return rows(
        conn.execute(
            """
            SELECT c.*,
                   COALESCE(b.amount, c.default_budget) AS budget,
                   b.amount IS NOT NULL AS budget_custom,
                   COALESCE((SELECT SUM(t.amount) FROM ledger t
                             WHERE t.category_id = c.id
                               AND (t.type = c.type
                                    OR (c.type = 'expense' AND t.type = 'card_payment'))
                               AND t.period = ?), 0) AS spent
            FROM categories c
            LEFT JOIN budgets b ON b.category_id = c.id AND b.month = ?
            ORDER BY c.type DESC, c.active DESC, c.name
            """,
            (month, month),
        )
    )


def month_totals(conn, month):
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN type = 'income' THEN amount END), 0) AS income,
          COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense,
          COALESCE(SUM(CASE WHEN type = 'card_payment' THEN amount END), 0) AS card_payments,
          COUNT(*) AS count
        FROM ledger WHERE period = ?
        """,
        (month,),
    ).fetchone()
    totals = dict(row)
    totals["balance"] = totals["income"] - totals["expense"]
    totals["savings_rate"] = (
        round(totals["balance"] / totals["income"] * 100, 1) if totals["income"] else 0
    )
    return totals


def pending_recurring(conn, month):
    return rows(
        conn.execute(
            """
            SELECT r.* FROM recurring r
            WHERE r.active = 1 AND NOT EXISTS (
              SELECT 1 FROM transactions t
              WHERE t.recurring_id = r.id AND t.period = ?)
            ORDER BY r.day
            """,
            (month,),
        )
    )


def recurring_date(conn, r, month):
    """Fecha en que se recibe/paga un fijo para que corresponda a `month`."""
    tx_date = day_in_month(month, r["day"])
    if default_period(conn, r["type"], tx_date) != month:
        # Ingreso que llega a fin del mes anterior (p. ej. salario el día 29)
        tx_date = day_in_month(shift_month(month, -1), r["day"])
    return tx_date


def build_dashboard(conn, month):
    totals = month_totals(conn, month)
    prev = month_totals(conn, shift_month(month, -1))

    categories = [
        c for c in category_summaries(conn, month) if c["type"] == "expense"
    ]
    by_category = sorted(
        (c for c in categories if c["spent"] > 0 or c["budget"] > 0),
        key=lambda c: c["spent"],
        reverse=True,
    )
    uncategorized = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) FROM ledger
           WHERE type = 'expense' AND category_id IS NULL AND period = ?""",
        (month,),
    ).fetchone()[0]
    if uncategorized:
        by_category.append(
            {"id": None, "name": "Sin categoría", "color": "#94a3b8", "icon": "❔",
             "spent": uncategorized, "budget": 0}
        )

    trend = []
    for i in range(11, -1, -1):
        m = shift_month(month, -i)
        t = month_totals(conn, m)
        trend.append({"month": m, "income": t["income"], "expense": t["expense"],
                      "balance": t["balance"]})

    def cumulative(m):
        # Los movimientos asignados a este mes con fecha fuera de él se ubican
        # en el primer o último día del mes
        last = days_in_month(m)
        per_day = {}
        for tx_date, amount in conn.execute(
            "SELECT date, amount FROM ledger WHERE type = 'expense' AND period = ?",
            (m,),
        ):
            day = 1 if tx_date < month_start(m) else \
                last if tx_date >= next_month_start(m) else int(tx_date[8:10])
            per_day[day] = per_day.get(day, 0) + amount
        acc, out = 0, []
        for d in range(1, days_in_month(m) + 1):
            acc += per_day.get(d, 0)
            out.append(round(acc, 2))
        return out

    by_method = rows(
        conn.execute(
            """SELECT payment_method, SUM(amount) AS total FROM ledger
               WHERE type = 'expense' AND period = ?
               GROUP BY payment_method ORDER BY total DESC""",
            (month,),
        )
    )

    top_expenses = rows(
        conn.execute(
            """SELECT t.*, c.name AS category_name, c.icon AS category_icon,
                      c.color AS category_color, k.name AS card_name
               FROM ledger t
               LEFT JOIN categories c ON c.id = t.category_id
               LEFT JOIN cards k ON k.id = t.card_id
               WHERE t.type = 'expense' AND t.period = ?
               ORDER BY t.amount DESC LIMIT 5""",
            (month,),
        )
    )

    cards = [c for c in card_summaries(conn, month) if c["active"] and c["kind"] == "credit"]
    card_totals = {
        "limit": sum(c["credit_limit"] for c in cards),
        "balance": sum(c["balance"] for c in cards),
        "available": sum(c["available"] for c in cards),
    }
    card_totals["utilization"] = (
        round(card_totals["balance"] / card_totals["limit"] * 100, 1)
        if card_totals["limit"] else 0
    )

    budget_total = sum(c["budget"] for c in categories if c["active"])
    budget_spent = sum(c["spent"] for c in categories if c["budget"] > 0)

    today = date.today().isoformat()
    alerts = []
    fmt = lambda v: f"{v:,.0f}".replace(",", ".")
    over = [c for c in categories if c["budget"] > 0 and c["spent"] > c["budget"]]
    near = [c for c in categories
            if c["budget"] > 0 and c["budget"] * 0.85 <= c["spent"] <= c["budget"]]
    if over:
        alerts.append({"level": "danger",
                       "text": "Superaste el presupuesto en: " + ", ".join(
                           f"{c['icon']} {c['name']} (+{fmt(c['spent'] - c['budget'])})"
                           for c in over)})
    if near:
        alerts.append({"level": "warning",
                       "text": "Cerca del límite del presupuesto: " + ", ".join(
                           f"{c['icon']} {c['name']} ({c['spent'] / c['budget'] * 100:.0f}%)"
                           for c in near)})
    for c in cards:
        if c["utilization"] >= 70:
            alerts.append({"level": "danger" if c["utilization"] >= 90 else "warning",
                           "text": f"💳 {c['name']}: uso del cupo al {c['utilization']:.0f}%"})
        if c["balance"] > 0 and month == current_month():
            # El pago próximo puede ser del corte de este mes o del anterior
            dues = sorted(d for _, d in (card_dates(c, shift_month(month, -1)),
                                         card_dates(c, month)) if d and d >= today)
            if dues:
                days = (date.fromisoformat(dues[0]) - date.today()).days
                if days <= 7:
                    alerts.append({"level": "info",
                                   "text": f"📅 {c['name']}: fecha límite de pago en {days} "
                                           f"día(s) ({dues[0]})"})
    pending = pending_recurring(conn, month)
    if pending:
        alerts.append({"level": "info",
                       "text": f"🔁 Tienes {len(pending)} gasto(s)/ingreso(s) fijo(s) sin "
                               f"registrar este mes"})
    if totals["income"] and totals["balance"] < 0:
        alerts.append({"level": "danger",
                       "text": "📉 Este mes estás gastando más de lo que ingresas"})

    return {
        "month": month,
        "totals": totals,
        "previous": prev,
        "by_category": by_category,
        "budget": {"total": budget_total, "spent": budget_spent},
        "trend": trend,
        "daily": {"current": cumulative(month),
                  "previous": cumulative(shift_month(month, -1))},
        "by_method": by_method,
        "top_expenses": top_expenses,
        "cards": cards,
        "card_totals": card_totals,
        "alerts": alerts,
        "pending_recurring": len(pending),
        "installments_ahead": [
            {"month": m, "amount": round(conn.execute(
                """SELECT COALESCE(SUM(capital + interest), 0) FROM installment_schedule
                   WHERE period = ?""", (m,)).fetchone()[0], 2)}
            for m in (shift_month(month, i) for i in range(0, 12))
        ],
        "investments": inv.total_value(conn, month, next_month_start(month), "COP"),
    }


# ------------------------------------------------------------------ aplicación


def create_app(db_path=None):
    db_path = db_path or os.environ.get("FINANZAS_DB", db.DEFAULT_DB_PATH)
    db.init_db(db_path)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app = Flask(__name__, static_folder=static_dir, static_url_path="/static")
    app.config["DB_PATH"] = db_path
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    def conn():
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify({"error": err.message}), err.status

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    # ----------------------------------------------------------- ajustes

    @app.get("/api/settings")
    def get_settings():
        return jsonify(dict(conn().execute("SELECT key, value FROM settings").fetchall()))

    @app.put("/api/settings")
    def put_settings():
        data = body()
        c = conn()
        if "currency" in data:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      ("currency", req_str(data, "currency")))
        if "income_shift_day" in data:
            day = opt_day(data, "income_shift_day")
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                      ("income_shift_day", str(day) if day else ""))
        c.commit()
        return get_settings()

    @app.get("/api/months")
    def months():
        found = [r[0] for r in conn().execute(
            "SELECT DISTINCT period FROM transactions ORDER BY 1 DESC")]
        return jsonify({"current": current_month(), "with_data": found})

    # ---------------------------------------------------------- tarjetas

    def card_payload(data):
        kind = req_str(data, "kind", required=False) or "credit"
        if kind not in ("credit", "debit"):
            raise ApiError("Tipo de tarjeta inválido")
        common = (req_str(data, "name"), req_str(data, "bank", required=False),
                  req_str(data, "last4", required=False)[-4:])
        tail = (req_str(data, "color", required=False) or "#4f46e5",
                1 if data.get("active", True) else 0, kind)
        if kind == "debit":  # una tarjeta débito no tiene cupo, corte ni pago
            return (*common, 0, 0, None, None, 0, *tail, None)
        due_next = data.get("due_next_month")
        due_next = None if due_next in (None, "") else int(str(due_next).lower() in ("1", "true"))
        return (*common,
                req_num(data, "credit_limit", minimum=0),
                req_num(data, "initial_balance", required=False, minimum=0),
                opt_day(data, "cut_day"),
                opt_day(data, "due_day"),
                req_num(data, "interest_rate", required=False, minimum=0),
                *tail, due_next)

    @app.get("/api/cards")
    def list_cards():
        return jsonify(card_summaries(conn(), arg_month()))

    @app.post("/api/cards")
    def create_card():
        c = conn()
        cur = c.execute(
            """INSERT INTO cards (name, bank, last4, credit_limit, initial_balance,
                   cut_day, due_day, interest_rate, color, active, kind, due_next_month)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            card_payload(body()),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM cards WHERE id = ?", (cur.lastrowid,))), 201

    @app.put("/api/cards/<int:card_id>")
    def update_card(card_id):
        c = conn()
        ensure_exists(c, "cards", card_id)
        c.execute(
            """UPDATE cards SET name = ?, bank = ?, last4 = ?, credit_limit = ?,
                   initial_balance = ?, cut_day = ?, due_day = ?, interest_rate = ?,
                   color = ?, active = ?, kind = ?, due_next_month = ?
               WHERE id = ?""",
            (*card_payload(body()), card_id),
        )
        inst.regenerate_card(c, card_id)
        c.commit()
        return jsonify(one(c, "SELECT * FROM cards WHERE id = ?", (card_id,)))

    @app.delete("/api/cards/<int:card_id>")
    def delete_card(card_id):
        c = conn()
        ensure_exists(c, "cards", card_id)
        c.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        c.commit()
        return "", 204

    # -------------------------------------------------------- categorías

    def category_payload(data):
        ctype = req_str(data, "type")
        if ctype not in ("expense", "income"):
            raise ApiError("Tipo de categoría inválido")
        return (
            req_str(data, "name"),
            ctype,
            req_str(data, "color", required=False) or "#64748b",
            req_str(data, "icon", required=False),
            req_num(data, "default_budget", required=False, minimum=0),
            1 if data.get("active", True) else 0,
        )

    @app.get("/api/categories")
    def list_categories():
        return jsonify(category_summaries(conn(), arg_month()))

    @app.post("/api/categories")
    def create_category():
        c = conn()
        cur = c.execute(
            """INSERT INTO categories (name, type, color, icon, default_budget, active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            category_payload(body()),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,))), 201

    @app.put("/api/categories/<int:cat_id>")
    def update_category(cat_id):
        c = conn()
        ensure_exists(c, "categories", cat_id)
        c.execute(
            """UPDATE categories SET name = ?, type = ?, color = ?, icon = ?,
                   default_budget = ?, active = ? WHERE id = ?""",
            (*category_payload(body()), cat_id),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM categories WHERE id = ?", (cat_id,)))

    @app.delete("/api/categories/<int:cat_id>")
    def delete_category(cat_id):
        c = conn()
        ensure_exists(c, "categories", cat_id)
        c.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        c.commit()
        return "", 204

    # ------------------------------------------------------- presupuestos

    @app.put("/api/budgets")
    def set_budget():
        data = body()
        month = req_str(data, "month")
        if not MONTH_RE.match(month):
            raise ApiError("Mes inválido")
        cat_id = opt_id(data, "category_id")
        if cat_id is None:
            raise ApiError("El campo 'category_id' es obligatorio")
        c = conn()
        ensure_exists(c, "categories", cat_id)
        if data.get("amount") in (None, ""):
            # Sin valor: vuelve a usar el presupuesto por defecto de la categoría
            c.execute("DELETE FROM budgets WHERE category_id = ? AND month = ?",
                      (cat_id, month))
        else:
            c.execute(
                """INSERT INTO budgets (category_id, month, amount) VALUES (?, ?, ?)
                   ON CONFLICT (category_id, month) DO UPDATE SET amount = excluded.amount""",
                (cat_id, month, req_num(data, "amount", minimum=0)),
            )
        c.commit()
        return jsonify({"ok": True})

    @app.post("/api/budgets/copy")
    def copy_budgets():
        data = body()
        source, target = req_str(data, "from"), req_str(data, "to")
        if not (MONTH_RE.match(source) and MONTH_RE.match(target)):
            raise ApiError("Mes inválido")
        c = conn()
        cur = c.execute(
            """INSERT INTO budgets (category_id, month, amount)
               SELECT c.id, ?, COALESCE(b.amount, c.default_budget)
               FROM categories c
               LEFT JOIN budgets b ON b.category_id = c.id AND b.month = ?
               WHERE c.type = 'expense'
               ON CONFLICT (category_id, month) DO UPDATE SET amount = excluded.amount""",
            (target, source),
        )
        c.commit()
        return jsonify({"copied": cur.rowcount})

    # -------------------------------------------------------- movimientos

    def tx_payload(c, data):
        tx_date = req_str(data, "date")
        if not DATE_RE.match(tx_date):
            raise ApiError("Fecha inválida, use AAAA-MM-DD")
        try:
            date.fromisoformat(tx_date)
        except ValueError:
            raise ApiError("Fecha inválida")
        ttype = req_str(data, "type")
        if ttype not in TX_TYPES:
            raise ApiError("Tipo de movimiento inválido")
        method = req_str(data, "payment_method", required=False) or "cash"
        if method not in PAYMENT_METHODS:
            raise ApiError("Medio de pago inválido")
        amount = req_num(data, "amount", minimum=0)
        if amount <= 0:
            raise ApiError("El monto debe ser mayor a 0")
        card_id = opt_id(data, "card_id")
        category_id = opt_id(data, "category_id")

        if ttype == "card_payment":
            if card_id is None:
                raise ApiError("Selecciona la tarjeta a la que abonas")
            if method == "card":
                method = "transfer"
            require_card(c, card_id, "credit")
        elif ttype == "expense" and method == "card":
            if card_id is None:
                raise ApiError("Selecciona la tarjeta con la que pagaste")
            require_card(c, card_id, "credit")
        elif ttype == "expense" and method == "debit" and card_id is not None:
            require_card(c, card_id, "debit")
        else:
            card_id = None
            if ttype == "income" and method == "card":
                method = "transfer"

        if category_id is not None:
            row = c.execute("SELECT type FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not row:
                raise ApiError("Registro no encontrado", 404)
            if row["type"] != ("income" if ttype == "income" else "expense"):
                raise ApiError("La categoría no corresponde al tipo de movimiento")
        installments = int(req_num(data, "installments", required=False, default=1,
                                    minimum=1))
        if method != "card":
            installments = 1
        period = req_str(data, "period", required=False) or default_period(c, ttype, tx_date)
        if not MONTH_RE.match(period):
            raise ApiError("El mes al que corresponde es inválido, use AAAA-MM")
        # Compra a cuotas con plan de pagos: cada mes cuenta solo la cuota
        financed = 1 if (ttype == "expense" and method == "card" and installments > 1
                         and str(data.get("financed", "")).lower() in ("1", "true")) else 0
        rate = None
        if financed:
            rate = req_num(data, "rate", required=False, default=None, minimum=0)
            if rate is None:
                rate = c.execute("SELECT interest_rate FROM cards WHERE id = ?",
                                 (card_id,)).fetchone()[0]
        # Abono a capital de una compra a cuotas (solo en pagos a la tarjeta)
        applies_to = opt_id(data, "applies_to") if ttype == "card_payment" else None
        if applies_to is not None:
            target = c.execute("SELECT card_id, financed FROM transactions WHERE id = ?",
                               (applies_to,)).fetchone()
            if not target or not target["financed"] or target["card_id"] != card_id:
                raise ApiError("El abono debe ser a una compra a cuotas de esa tarjeta")
        return (tx_date, req_str(data, "description", required=False), amount, ttype,
                category_id, method, card_id, installments,
                req_str(data, "notes", required=False), period, financed, rate, applies_to)

    def require_card(c, card_id, kind):
        row = c.execute("SELECT kind FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not row:
            raise ApiError("Registro no encontrado", 404)
        if row["kind"] != kind:
            raise ApiError("Esa tarjeta es de crédito" if row["kind"] == "credit"
                           else "Esa tarjeta es débito")

    def refresh_plans(c, tx_id, *targets):
        """Recalcula el plan de la compra y el de las compras a las que se abonó."""
        for purchase in {tx_id, *targets} - {None}:
            inst.regenerate(c, purchase)

    TX_SELECT = """
        SELECT t.*, c.name AS category_name, c.icon AS category_icon,
               c.color AS category_color, k.name AS card_name, k.color AS card_color,
               k.kind AS card_kind
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN cards k ON k.id = t.card_id
    """

    def query_transactions(month):
        where = ["t.period = ?"]
        params = [month]
        types = [t for t in (request.args.get("type") or "").split(",") if t in TX_TYPES]
        if types:
            where.append(f"t.type IN ({','.join('?' * len(types))})")
            params += types
        if request.args.get("category_id"):
            if request.args["category_id"] == "none":
                where.append("t.category_id IS NULL")
            else:
                where.append("t.category_id = ?")
                params.append(request.args["category_id"])
        if request.args.get("card_id"):
            where.append("t.card_id = ?")
            params.append(request.args["card_id"])
        if request.args.get("payment_method") in PAYMENT_METHODS:
            where.append("t.payment_method = ?")
            params.append(request.args["payment_method"])
        if request.args.get("q"):
            where.append("(t.description LIKE ? OR t.notes LIKE ?)")
            like = f"%{request.args['q']}%"
            params += [like, like]
        sql = f"{TX_SELECT} WHERE {' AND '.join(where)} ORDER BY t.date DESC, t.id DESC"
        result = rows(conn().execute(sql, params))
        for t in result:
            # Una compra a cuotas no suma completa: suman sus cuotas en cada mes
            t["counts"] = not (t["financed"] and t["type"] == "expense" and t["installments"] > 1)
        return sorted(result + installment_rows(month), key=lambda t: (t["date"], t["id"]),
                      reverse=True)

    def installment_rows(month):
        """Cuotas (y sus intereses) que se pagan en `month`, como filas del listado."""
        args = request.args
        types = (args.get("type") or "").split(",")
        if args.get("type") and "expense" not in types:
            return []
        if args.get("payment_method") not in (None, "", "card"):
            return []
        interest_cat = inst.interest_category_id(conn())
        out = []
        for r in conn().execute(
                f"""SELECT s.*, t.date AS purchase_date, t.description, t.installments,
                           t.category_id, t.card_id,
                           t.notes, c.name AS category_name, c.icon AS category_icon,
                           c.color AS category_color, k.name AS card_name, k.color AS card_color,
                           k.kind AS card_kind
                    FROM installment_schedule s
                    JOIN transactions t ON t.id = s.purchase_id
                    LEFT JOIN categories c ON c.id = t.category_id
                    LEFT JOIN cards k ON k.id = t.card_id
                    WHERE s.period = ?""", (month,)):
            r = dict(r)
            label = f"Cuota {r['number']}/{r['installments']}"
            for part, amount, cat in (("capital", r["capital"], r["category_id"]),
                                      ("interest", r["interest"], interest_cat)):
                if amount <= 0:
                    continue
                row = {
                    "id": -r["id"] if part == "capital" else -r["id"] - 1000000000,
                    "installment_of": r["purchase_id"], "installment_label": label,
                    "installment_part": part, "purchase_date": r["purchase_date"],
                    "date": r["due_date"], "period": r["period"],
                    "description": r["description"] if part == "capital"
                    else f"Intereses: {r['description']}",
                    "amount": amount, "type": "expense", "category_id": cat,
                    "payment_method": "card", "card_id": r["card_id"], "installments": 1,
                    "notes": "", "counts": True, "card_name": r["card_name"],
                    "card_color": r["card_color"], "card_kind": r["card_kind"],
                }
                if part == "capital":
                    row.update(category_name=r["category_name"], category_icon=r["category_icon"],
                               category_color=r["category_color"])
                else:
                    ic = conn().execute("SELECT name, icon, color FROM categories WHERE id = ?",
                                        (cat,)).fetchone()
                    row.update(category_name=ic and ic["name"], category_icon=ic and ic["icon"],
                               category_color=ic and ic["color"])
                out.append(row)
        # Mismos filtros que los movimientos normales
        if args.get("category_id"):
            want = None if args["category_id"] == "none" else int(args["category_id"])
            out = [o for o in out if o["category_id"] == want]
        if args.get("card_id"):
            out = [o for o in out if str(o["card_id"]) == args["card_id"]]
        if args.get("q"):
            q = args["q"].lower()
            out = [o for o in out if q in (o["description"] or "").lower()]
        return out

    @app.get("/api/transactions")
    def list_transactions():
        return jsonify(query_transactions(arg_month()))

    @app.post("/api/transactions")
    def create_transaction():
        c = conn()
        data = body()
        payload = tx_payload(c, data)
        # Registro individual de un gasto/ingreso fijo: queda marcado para ese mes
        recurring_id = opt_id(data, "recurring_id")
        if recurring_id is not None:
            ensure_exists(c, "recurring", recurring_id)
            if c.execute(
                "SELECT 1 FROM transactions WHERE recurring_id = ? AND period = ?",
                (recurring_id, payload[9]),
            ).fetchone():
                raise ApiError("Este fijo ya está registrado en ese mes")
        cur = c.execute(
            """INSERT INTO transactions (date, description, amount, type, category_id,
                   payment_method, card_id, installments, notes, period, financed, rate,
                   applies_to, recurring_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*payload, recurring_id),
        )
        refresh_plans(c, cur.lastrowid, payload[12])
        c.commit()
        return jsonify(one(c, f"{TX_SELECT} WHERE t.id = ?", (cur.lastrowid,))), 201

    @app.get("/api/transactions/<int:tx_id>")
    def get_transaction(tx_id):
        ensure_exists(conn(), "transactions", tx_id)
        return jsonify(one(conn(), f"{TX_SELECT} WHERE t.id = ?", (tx_id,)))

    @app.put("/api/transactions/<int:tx_id>")
    def update_transaction(tx_id):
        c = conn()
        ensure_exists(c, "transactions", tx_id)
        old_target = c.execute("SELECT applies_to FROM transactions WHERE id = ?",
                               (tx_id,)).fetchone()[0]
        payload = tx_payload(c, body())
        c.execute(
            """UPDATE transactions SET date = ?, description = ?, amount = ?, type = ?,
                   category_id = ?, payment_method = ?, card_id = ?, installments = ?,
                   notes = ?, period = ?, financed = ?, rate = ?, applies_to = ?
               WHERE id = ?""",
            (*payload, tx_id),
        )
        refresh_plans(c, tx_id, payload[12], old_target)
        c.commit()
        return jsonify(one(c, f"{TX_SELECT} WHERE t.id = ?", (tx_id,)))

    @app.delete("/api/transactions/<int:tx_id>")
    def delete_transaction(tx_id):
        c = conn()
        ensure_exists(c, "transactions", tx_id)
        target = c.execute("SELECT applies_to FROM transactions WHERE id = ?", (tx_id,)).fetchone()[0]
        c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        refresh_plans(c, None, target)
        c.commit()
        return "", 204

    @app.get("/api/transactions/export.csv")
    def export_transactions():
        month = arg_month()
        labels = {"expense": "Gasto", "income": "Ingreso", "card_payment": "Pago tarjeta"}
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(["Fecha", "Mes", "Tipo", "Descripción", "Categoría", "Medio de pago",
                         "Tarjeta", "Cuotas", "Monto", "Notas"])
        for t in query_transactions(month):
            writer.writerow([t["date"], t["period"], labels[t["type"]], t["description"],
                             t["category_name"] or "", t["payment_method"],
                             t["card_name"] or "", t["installments"],
                             f"{t['amount']:.2f}".replace(".", ","), t["notes"]])
        return Response(
            "﻿" + out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename=movimientos-{month}.csv"},
        )

    # ------------------------------------------------------- gastos fijos

    def recurring_payload(c, data):
        rtype = req_str(data, "type")
        if rtype not in ("expense", "income"):
            raise ApiError("Tipo inválido")
        method = req_str(data, "payment_method", required=False) or "cash"
        if method not in PAYMENT_METHODS:
            raise ApiError("Medio de pago inválido")
        card_id = (opt_id(data, "card_id")
                   if method in ("card", "debit") and rtype == "expense" else None)
        if method == "card" and card_id is None:
            if rtype == "expense":
                raise ApiError("Selecciona la tarjeta")
            method = "transfer"
        if card_id is not None:
            require_card(c, card_id, "credit" if method == "card" else "debit")
        category_id = opt_id(data, "category_id")
        if category_id is not None:
            ensure_exists(c, "categories", category_id)
        amount = req_num(data, "amount", minimum=0)
        if amount <= 0:
            raise ApiError("El monto debe ser mayor a 0")
        return (req_str(data, "description"), amount, rtype, category_id, method, card_id,
                opt_day(data, "day") or 1, 1 if data.get("active", True) else 0)

    @app.get("/api/recurring")
    def list_recurring():
        month = arg_month()
        pending = {r["id"] for r in pending_recurring(conn(), month)}
        items = rows(conn().execute(
            """SELECT r.*, c.name AS category_name, c.icon AS category_icon,
                      k.name AS card_name
               FROM recurring r
               LEFT JOIN categories c ON c.id = r.category_id
               LEFT JOIN cards k ON k.id = r.card_id
               ORDER BY r.active DESC, r.day"""))
        for r in items:
            r["registered"] = r["active"] and r["id"] not in pending
            r["expected_date"] = recurring_date(conn(), r, month)
        return jsonify(items)

    @app.post("/api/recurring")
    def create_recurring():
        c = conn()
        cur = c.execute(
            """INSERT INTO recurring (description, amount, type, category_id,
                   payment_method, card_id, day, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            recurring_payload(c, body()),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM recurring WHERE id = ?", (cur.lastrowid,))), 201

    @app.put("/api/recurring/<int:rec_id>")
    def update_recurring(rec_id):
        c = conn()
        ensure_exists(c, "recurring", rec_id)
        c.execute(
            """UPDATE recurring SET description = ?, amount = ?, type = ?, category_id = ?,
                   payment_method = ?, card_id = ?, day = ?, active = ? WHERE id = ?""",
            (*recurring_payload(c, body()), rec_id),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM recurring WHERE id = ?", (rec_id,)))

    @app.delete("/api/recurring/<int:rec_id>")
    def delete_recurring(rec_id):
        c = conn()
        ensure_exists(c, "recurring", rec_id)
        c.execute("DELETE FROM recurring WHERE id = ?", (rec_id,))
        c.commit()
        return "", 204

    @app.post("/api/recurring/generate")
    def generate_recurring():
        data = body()
        month = req_str(data, "month")
        if not MONTH_RE.match(month):
            raise ApiError("Mes inválido")
        c = conn()
        pending = pending_recurring(c, month)
        if data.get("type") in ("expense", "income"):
            pending = [r for r in pending if r["type"] == data["type"]]
        for r in pending:
            c.execute(
                """INSERT INTO transactions (date, description, amount, type, category_id,
                       payment_method, card_id, installments, notes, period, recurring_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'Gasto fijo', ?, ?)""",
                (recurring_date(c, r, month), r["description"], r["amount"], r["type"],
                 r["category_id"], r["payment_method"], r["card_id"], month, r["id"]),
            )
        c.commit()
        return jsonify({"created": len(pending)})

    # ---------------------------------------------------- metas de ahorro

    def goal_payload(data):
        deadline = req_str(data, "deadline", required=False) or None
        if deadline and not DATE_RE.match(deadline):
            raise ApiError("Fecha límite inválida")
        target = req_num(data, "target", minimum=0)
        if target <= 0:
            raise ApiError("La meta debe ser mayor a 0")
        return (req_str(data, "name"), target,
                req_num(data, "saved", required=False, minimum=0), deadline,
                req_str(data, "color", required=False) or "#16a34a")

    @app.get("/api/goals")
    def list_goals():
        return jsonify(rows(conn().execute("SELECT * FROM goals ORDER BY deadline IS NULL, deadline, name")))

    @app.post("/api/goals")
    def create_goal():
        c = conn()
        cur = c.execute(
            "INSERT INTO goals (name, target, saved, deadline, color) VALUES (?, ?, ?, ?, ?)",
            goal_payload(body()),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM goals WHERE id = ?", (cur.lastrowid,))), 201

    @app.put("/api/goals/<int:goal_id>")
    def update_goal(goal_id):
        c = conn()
        ensure_exists(c, "goals", goal_id)
        c.execute(
            "UPDATE goals SET name = ?, target = ?, saved = ?, deadline = ?, color = ? WHERE id = ?",
            (*goal_payload(body()), goal_id),
        )
        c.commit()
        return jsonify(one(c, "SELECT * FROM goals WHERE id = ?", (goal_id,)))

    @app.post("/api/goals/<int:goal_id>/contribute")
    def contribute_goal(goal_id):
        c = conn()
        ensure_exists(c, "goals", goal_id)
        amount = req_num(body(), "amount")
        c.execute("UPDATE goals SET saved = MAX(0, saved + ?) WHERE id = ?", (amount, goal_id))
        c.commit()
        return jsonify(one(c, "SELECT * FROM goals WHERE id = ?", (goal_id,)))

    @app.delete("/api/goals/<int:goal_id>")
    def delete_goal(goal_id):
        c = conn()
        ensure_exists(c, "goals", goal_id)
        c.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        c.commit()
        return "", 204

    # --------------------------------------------------------- dashboard

    # ---------------------------------------------------------- inversiones

    def move_payload(data):
        kind = req_str(data, "kind")
        if kind not in inv.MOVE_KINDS:
            raise ApiError("Tipo de movimiento de inversión inválido")
        move_date = req_str(data, "date")
        if not DATE_RE.match(move_date):
            raise ApiError("Fecha inválida, use AAAA-MM-DD")
        amount = req_num(data, "amount", minimum=0)
        if kind != "valuation" and amount <= 0:
            raise ApiError("El monto debe ser mayor a 0")
        return kind, move_date, amount, req_str(data, "notes", required=False)

    def investment_payload(data):
        asset_type = req_str(data, "asset_type", required=False) or "otro"
        if asset_type not in inv.ASSET_TYPES:
            raise ApiError("Tipo de activo inválido")
        return (req_str(data, "name"), req_str(data, "platform", required=False), asset_type,
                req_str(data, "notes", required=False))

    @app.get("/api/investments")
    def get_portfolio():
        month = arg_month()
        months = [shift_month(month, -i) for i in range(11, -1, -1)]
        display = request.args.get("display") or "COP"
        if display not in inv.CURRENCIES:
            raise ApiError("Moneda inválida")
        data = inv.portfolio(conn(), month, next_month_start(month),
                             [(m, next_month_start(m)) for m in months], display)
        data["asset_types"] = [{"value": k, "label": v[0], "icon": v[1]}
                               for k, v in inv.ASSET_TYPES.items()]
        data["platforms"] = [r[0] for r in conn().execute(
            "SELECT DISTINCT platform FROM investments WHERE platform != '' ORDER BY 1")]
        return jsonify(data)

    @app.post("/api/investments")
    def create_investment():
        data = body()
        c = conn()
        fields = investment_payload(data)
        start = req_str(data, "date")
        if not DATE_RE.match(start):
            raise ApiError("Fecha inválida, use AAAA-MM-DD")
        invested = req_num(data, "invested", minimum=0)
        if invested <= 0:
            raise ApiError("Indica cuánto has aportado (mayor a 0)")
        value = req_num(data, "value", required=False, default=invested, minimum=0)
        currency = req_str(data, "currency", required=False) or "COP"
        if currency not in inv.CURRENCIES:
            raise ApiError("Moneda inválida")
        cur = c.execute(
            """INSERT INTO investments (name, platform, asset_type, notes, currency)
               VALUES (?, ?, ?, ?, ?)""", (*fields, currency))
        c.execute("""INSERT INTO investment_moves (investment_id, date, kind, amount, notes)
                     VALUES (?, ?, 'contribution', ?, 'Aporte inicial')""",
                  (cur.lastrowid, start, invested))
        if value != invested:
            c.execute("""INSERT INTO investment_moves (investment_id, date, kind, amount)
                         VALUES (?, ?, 'valuation', ?)""", (cur.lastrowid, start, value))
        c.commit()
        return jsonify(one(c, "SELECT * FROM investments WHERE id = ?", (cur.lastrowid,))), 201

    @app.put("/api/investments/<int:inv_id>")
    def update_investment(inv_id):
        c = conn()
        ensure_exists(c, "investments", inv_id)
        c.execute("UPDATE investments SET name = ?, platform = ?, asset_type = ?, notes = ? "
                  "WHERE id = ?", (*investment_payload(body()), inv_id))
        c.commit()
        return jsonify(one(c, "SELECT * FROM investments WHERE id = ?", (inv_id,)))

    @app.delete("/api/investments/<int:inv_id>")
    def delete_investment(inv_id):
        c = conn()
        ensure_exists(c, "investments", inv_id)
        c.execute("DELETE FROM investments WHERE id = ?", (inv_id,))
        c.commit()
        return "", 204

    @app.get("/api/investments/<int:inv_id>/moves")
    def list_investment_moves(inv_id):
        c = conn()
        ensure_exists(c, "investments", inv_id)
        moves = rows(c.execute(
            "SELECT * FROM investment_moves WHERE investment_id = ? ORDER BY date, id", (inv_id,)))
        state = {"invested": 0.0, "value": 0.0}
        for m in moves:
            inv.apply_move(state, m["kind"], m["amount"])
            m["value_after"] = round(state["value"], 2)
            m["invested_after"] = round(state["invested"], 2)
        return jsonify(list(reversed(moves)))

    @app.post("/api/investments/<int:inv_id>/moves")
    def create_investment_move(inv_id):
        c = conn()
        ensure_exists(c, "investments", inv_id)
        cur = c.execute(
            """INSERT INTO investment_moves (investment_id, kind, date, amount, notes)
               VALUES (?, ?, ?, ?, ?)""", (inv_id, *move_payload(body())))
        c.commit()
        return jsonify(one(c, "SELECT * FROM investment_moves WHERE id = ?", (cur.lastrowid,))), 201

    @app.delete("/api/investments/moves/<int:move_id>")
    def delete_investment_move(move_id):
        c = conn()
        ensure_exists(c, "investment_moves", move_id)
        c.execute("DELETE FROM investment_moves WHERE id = ?", (move_id,))
        c.commit()
        return "", 204

    # ------------------------------------------------- TRM (dólar a pesos)

    def fx_month(data):
        month = req_str(data, "month")
        if not MONTH_RE.match(month):
            raise ApiError("Mes inválido")
        return month

    @app.put("/api/fx-rates")
    def set_fx_rate():
        data = body()
        month = fx_month(data)
        c = conn()
        if data.get("rate") in (None, ""):
            c.execute("DELETE FROM fx_rates WHERE month = ?", (month,))
        else:
            rate = req_num(data, "rate")
            if rate <= 0:
                raise ApiError("La TRM debe ser mayor a 0")
            c.execute("INSERT OR REPLACE INTO fx_rates (month, rate) VALUES (?, ?)", (month, rate))
        c.commit()
        return jsonify({"month": month, "rate": inv.rate_for(c, month)})

    @app.post("/api/fx-rates/official")
    def fetch_official_rate():
        """Trae la TRM oficial (datos.gov.co) del último día del mes, o de hoy."""
        import json
        import urllib.parse
        import urllib.request

        month = fx_month(body())
        last = day_in_month(month, 31)
        on = min(last, date.today().isoformat())
        query = urllib.parse.urlencode({
            "$where": f"vigenciadesde <= '{on}T00:00:00'",
            "$order": "vigenciadesde DESC",
            "$limit": 1,
        })
        try:
            with urllib.request.urlopen(
                    f"https://www.datos.gov.co/resource/32sa-8pi3.json?{query}", timeout=10) as res:
                found = json.load(res)
            rate = float(found[0]["valor"])
        except Exception:
            raise ApiError("No se pudo consultar la TRM oficial. Revisa tu conexión a internet "
                           "o escríbela a mano.", 502)
        c = conn()
        c.execute("INSERT OR REPLACE INTO fx_rates (month, rate) VALUES (?, ?)", (month, rate))
        c.commit()
        return jsonify({"month": month, "rate": rate, "date": found[0]["vigenciadesde"][:10]})

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(build_dashboard(conn(), arg_month()))

    @app.get("/api/info")
    def info():
        return jsonify({"db_path": os.path.abspath(app.config["DB_PATH"]),
                        "can_shutdown": callable(app.config.get("SHUTDOWN"))})

    @app.post("/api/shutdown")
    def shutdown():
        """Cierra la aplicación (lo configura run.py; en pruebas no existe)."""
        stop = app.config.get("SHUTDOWN")
        if not callable(stop):
            raise ApiError("La aplicación no se puede cerrar desde aquí")
        # Se cierra un instante después para que la respuesta alcance a llegar
        import threading
        threading.Timer(0.5, stop).start()
        return jsonify({"ok": True})

    @app.post("/api/restore")
    def restore():
        """Reemplaza todos los datos por los de un archivo de respaldo (.db)."""
        import sqlite3
        import tempfile

        upload = request.files.get("file")
        if not upload:
            raise ApiError("Selecciona el archivo de respaldo")
        payload = upload.read()
        if not payload.startswith(b"SQLite format 3"):
            raise ApiError("El archivo no es un respaldo válido de Mis Finanzas")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            tmp.write(payload)
            tmp.close()
            src = sqlite3.connect(tmp.name)
            try:
                tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"transactions", "categories", "cards"} <= tables:
                    raise ApiError("El archivo no es un respaldo válido de Mis Finanzas")
                c = g.pop("conn", None)
                if c is not None:
                    c.close()
                dest = sqlite3.connect(app.config["DB_PATH"])
                src.backup(dest)
                dest.close()
            finally:
                src.close()
        finally:
            os.unlink(tmp.name)
        db.init_db(app.config["DB_PATH"])  # aplica migraciones si el respaldo es antiguo
        return jsonify({"ok": True})

    @app.get("/api/backup")
    def backup():
        # Copia consistente de la base de datos usando la API de backup de SQLite
        import sqlite3
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        dest = sqlite3.connect(tmp.name)
        conn().backup(dest)
        dest.close()
        with open(tmp.name, "rb") as fh:
            payload = io.BytesIO(fh.read())
        os.unlink(tmp.name)
        return send_file(payload, as_attachment=True, mimetype="application/octet-stream",
                         download_name=f"finanzas-backup-{date.today().isoformat()}.db")

    return app
