"""Capa de acceso a datos: esquema SQLite, conexión y datos iniciales."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "finanzas.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    bank            TEXT    NOT NULL DEFAULT '',
    last4           TEXT    NOT NULL DEFAULT '',
    credit_limit    REAL    NOT NULL DEFAULT 0,
    initial_balance REAL    NOT NULL DEFAULT 0,
    cut_day         INTEGER,
    due_day         INTEGER,
    interest_rate   REAL    NOT NULL DEFAULT 0,
    color           TEXT    NOT NULL DEFAULT '#4f46e5',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL CHECK (type IN ('expense', 'income')),
    color          TEXT    NOT NULL DEFAULT '#64748b',
    icon           TEXT    NOT NULL DEFAULT '',
    default_budget REAL    NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1
);

-- Presupuesto específico de un mes (si no existe se usa categories.default_budget)
CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    month       TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    UNIQUE (category_id, month)
);

CREATE TABLE IF NOT EXISTS recurring (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    description    TEXT    NOT NULL,
    amount         REAL    NOT NULL,
    type           TEXT    NOT NULL CHECK (type IN ('expense', 'income')),
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    payment_method TEXT    NOT NULL DEFAULT 'cash',
    card_id        INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    day            INTEGER NOT NULL DEFAULT 1,
    active         INTEGER NOT NULL DEFAULT 1
);

-- type: expense | income | card_payment (abono a tarjeta, no cuenta como gasto)
-- payment_method: cash | debit | transfer | card
CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    amount         REAL    NOT NULL CHECK (amount >= 0),
    type           TEXT    NOT NULL CHECK (type IN ('expense', 'income', 'card_payment')),
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    payment_method TEXT    NOT NULL DEFAULT 'cash',
    card_id        INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    installments   INTEGER NOT NULL DEFAULT 1,
    notes          TEXT    NOT NULL DEFAULT '',
    recurring_id   INTEGER REFERENCES recurring(id) ON DELETE SET NULL,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_card ON transactions(card_id);

CREATE TABLE IF NOT EXISTS goals (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    target   REAL NOT NULL,
    saved    REAL NOT NULL DEFAULT 0,
    deadline TEXT,
    color    TEXT NOT NULL DEFAULT '#16a34a'
);
"""

DEFAULT_CATEGORIES = [
    # (nombre, tipo, color, icono)
    ("Vivienda", "expense", "#6366f1", "🏠"),
    ("Servicios", "expense", "#0ea5e9", "💡"),
    ("Mercado", "expense", "#22c55e", "🛒"),
    ("Restaurantes", "expense", "#f97316", "🍔"),
    ("Transporte", "expense", "#eab308", "🚗"),
    ("Salud", "expense", "#ef4444", "🩺"),
    ("Educación", "expense", "#8b5cf6", "📚"),
    ("Entretenimiento", "expense", "#ec4899", "🎬"),
    ("Suscripciones", "expense", "#14b8a6", "📺"),
    ("Ropa", "expense", "#a855f7", "👕"),
    ("Otros gastos", "expense", "#64748b", "📦"),
    ("Salario", "income", "#16a34a", "💼"),
    ("Ingresos extra", "income", "#0d9488", "💰"),
]


def connect(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories (name, type, color, icon) VALUES (?, ?, ?, ?)",
                DEFAULT_CATEGORIES,
            )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('currency', 'COP')"
        )
        conn.commit()
    finally:
        conn.close()
