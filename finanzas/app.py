"""API REST y servidor de la app de finanzas personales."""
import calendar
import csv
import io
import os
import re
from datetime import date

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

from . import db

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
        balance = c["initial_balance"] + agg[0] - agg[1]
        c["balance"] = round(balance, 2)
        c["available"] = round(c["credit_limit"] - balance, 2)
        c["utilization"] = (
            round(balance / c["credit_limit"] * 100, 1) if c["credit_limit"] > 0 else 0
        )
        c["month_spent"] = agg[2]
        c["month_paid"] = agg[3]
        c["cut_date"] = day_in_month(month, c["cut_day"]) if c["cut_day"] else None
        c["due_date"] = day_in_month(month, c["due_day"]) if c["due_day"] else None
    return cards


def category_summaries(conn, month):
    start, end = month_start(month), next_month_start(month)
    return rows(
        conn.execute(
            """
            SELECT c.*,
                   COALESCE(b.amount, c.default_budget) AS budget,
                   b.amount IS NOT NULL AS budget_custom,
                   COALESCE((SELECT SUM(t.amount) FROM transactions t
                             WHERE t.category_id = c.id
                               AND (t.type = c.type
                                    OR (c.type = 'expense' AND t.type = 'card_payment'))
                               AND t.date >= ? AND t.date < ?), 0) AS spent
            FROM categories c
            LEFT JOIN budgets b ON b.category_id = c.id AND b.month = ?
            ORDER BY c.type DESC, c.active DESC, c.name
            """,
            (start, end, month),
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
        FROM transactions WHERE date >= ? AND date < ?
        """,
        (month_start(month), next_month_start(month)),
    ).fetchone()
    totals = dict(row)
    totals["balance"] = totals["income"] - totals["expense"]
    totals["savings_rate"] = (
        round(totals["balance"] / totals["income"] * 100, 1) if totals["income"] else 0
    )
    return totals


def pending_recurring(conn, month):
    start, end = month_start(month), next_month_start(month)
    return rows(
        conn.execute(
            """
            SELECT r.* FROM recurring r
            WHERE r.active = 1 AND NOT EXISTS (
              SELECT 1 FROM transactions t
              WHERE t.recurring_id = r.id AND t.date >= ? AND t.date < ?)
            ORDER BY r.day
            """,
            (start, end),
        )
    )


def build_dashboard(conn, month):
    start, end = month_start(month), next_month_start(month)
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
        """SELECT COALESCE(SUM(amount), 0) FROM transactions
           WHERE type = 'expense' AND category_id IS NULL AND date >= ? AND date < ?""",
        (start, end),
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
        per_day = dict(
            conn.execute(
                """SELECT CAST(substr(date, 9, 2) AS INTEGER), SUM(amount)
                   FROM transactions WHERE type = 'expense' AND date >= ? AND date < ?
                   GROUP BY 1""",
                (month_start(m), next_month_start(m)),
            ).fetchall()
        )
        acc, out = 0, []
        for d in range(1, days_in_month(m) + 1):
            acc += per_day.get(d, 0)
            out.append(round(acc, 2))
        return out

    by_method = rows(
        conn.execute(
            """SELECT payment_method, SUM(amount) AS total FROM transactions
               WHERE type = 'expense' AND date >= ? AND date < ?
               GROUP BY payment_method ORDER BY total DESC""",
            (start, end),
        )
    )

    top_expenses = rows(
        conn.execute(
            """SELECT t.*, c.name AS category_name, c.icon AS category_icon,
                      c.color AS category_color, k.name AS card_name
               FROM transactions t
               LEFT JOIN categories c ON c.id = t.category_id
               LEFT JOIN cards k ON k.id = t.card_id
               WHERE t.type = 'expense' AND t.date >= ? AND t.date < ?
               ORDER BY t.amount DESC LIMIT 5""",
            (start, end),
        )
    )

    cards = [c for c in card_summaries(conn, month) if c["active"]]
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
        if c["due_date"] and c["balance"] > 0 and month == current_month() \
                and c["due_date"] >= today:
            days = (date.fromisoformat(c["due_date"]) - date.today()).days
            if days <= 7:
                alerts.append({"level": "info",
                               "text": f"📅 {c['name']}: fecha límite de pago en {days} día(s) "
                                       f"({c['due_date']})"})
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
        for key in ("currency",):
            if key in data:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                          (key, req_str(data, key)))
        c.commit()
        return get_settings()

    @app.get("/api/months")
    def months():
        found = [r[0] for r in conn().execute(
            "SELECT DISTINCT substr(date, 1, 7) FROM transactions ORDER BY 1 DESC")]
        return jsonify({"current": current_month(), "with_data": found})

    # ---------------------------------------------------------- tarjetas

    def card_payload(data):
        return (
            req_str(data, "name"),
            req_str(data, "bank", required=False),
            req_str(data, "last4", required=False)[-4:],
            req_num(data, "credit_limit", minimum=0),
            req_num(data, "initial_balance", required=False, minimum=0),
            opt_day(data, "cut_day"),
            opt_day(data, "due_day"),
            req_num(data, "interest_rate", required=False, minimum=0),
            req_str(data, "color", required=False) or "#4f46e5",
            1 if data.get("active", True) else 0,
        )

    @app.get("/api/cards")
    def list_cards():
        return jsonify(card_summaries(conn(), arg_month()))

    @app.post("/api/cards")
    def create_card():
        c = conn()
        cur = c.execute(
            """INSERT INTO cards (name, bank, last4, credit_limit, initial_balance,
                   cut_day, due_day, interest_rate, color, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                   color = ?, active = ?
               WHERE id = ?""",
            (*card_payload(body()), card_id),
        )
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
        elif ttype == "expense" and method == "card":
            if card_id is None:
                raise ApiError("Selecciona la tarjeta con la que pagaste")
        else:
            card_id = None
            if ttype == "income" and method == "card":
                method = "transfer"

        if card_id is not None:
            ensure_exists(c, "cards", card_id)
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
        return (tx_date, req_str(data, "description", required=False), amount, ttype,
                category_id, method, card_id, installments,
                req_str(data, "notes", required=False))

    TX_SELECT = """
        SELECT t.*, c.name AS category_name, c.icon AS category_icon,
               c.color AS category_color, k.name AS card_name, k.color AS card_color
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN cards k ON k.id = t.card_id
    """

    def query_transactions(month):
        where = ["t.date >= ?", "t.date < ?"]
        params = [month_start(month), next_month_start(month)]
        if request.args.get("type") in TX_TYPES:
            where.append("t.type = ?")
            params.append(request.args["type"])
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
        return rows(conn().execute(sql, params))

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
            month = payload[0][:7]
            if c.execute(
                """SELECT 1 FROM transactions
                   WHERE recurring_id = ? AND date >= ? AND date < ?""",
                (recurring_id, month_start(month), next_month_start(month)),
            ).fetchone():
                raise ApiError("Este fijo ya está registrado en ese mes")
        cur = c.execute(
            """INSERT INTO transactions (date, description, amount, type, category_id,
                   payment_method, card_id, installments, notes, recurring_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*payload, recurring_id),
        )
        c.commit()
        return jsonify(one(c, f"{TX_SELECT} WHERE t.id = ?", (cur.lastrowid,))), 201

    @app.put("/api/transactions/<int:tx_id>")
    def update_transaction(tx_id):
        c = conn()
        ensure_exists(c, "transactions", tx_id)
        c.execute(
            """UPDATE transactions SET date = ?, description = ?, amount = ?, type = ?,
                   category_id = ?, payment_method = ?, card_id = ?, installments = ?,
                   notes = ?
               WHERE id = ?""",
            (*tx_payload(c, body()), tx_id),
        )
        c.commit()
        return jsonify(one(c, f"{TX_SELECT} WHERE t.id = ?", (tx_id,)))

    @app.delete("/api/transactions/<int:tx_id>")
    def delete_transaction(tx_id):
        c = conn()
        ensure_exists(c, "transactions", tx_id)
        c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        c.commit()
        return "", 204

    @app.get("/api/transactions/export.csv")
    def export_transactions():
        month = arg_month()
        labels = {"expense": "Gasto", "income": "Ingreso", "card_payment": "Pago tarjeta"}
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(["Fecha", "Tipo", "Descripción", "Categoría", "Medio de pago",
                         "Tarjeta", "Cuotas", "Monto", "Notas"])
        for t in query_transactions(month):
            writer.writerow([t["date"], labels[t["type"]], t["description"],
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
        card_id = opt_id(data, "card_id") if method == "card" and rtype == "expense" else None
        if method == "card" and card_id is None:
            if rtype == "expense":
                raise ApiError("Selecciona la tarjeta")
            method = "transfer"
        if card_id is not None:
            ensure_exists(c, "cards", card_id)
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
        for r in pending:
            c.execute(
                """INSERT INTO transactions (date, description, amount, type, category_id,
                       payment_method, card_id, installments, notes, recurring_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'Gasto fijo', ?)""",
                (day_in_month(month, r["day"]), r["description"], r["amount"], r["type"],
                 r["category_id"], r["payment_method"], r["card_id"], r["id"]),
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

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(build_dashboard(conn(), arg_month()))

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
