"""Capa de acceso a datos: esquema SQLite, conexión y datos iniciales."""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_db_path():
    """Ubicación de la base de datos.

    Desde el código fuente: data/finanzas.db junto a la app. En el ejecutable (.exe) la
    carpeta de la app es temporal, así que los datos van a la carpeta del usuario
    (en Windows: %LOCALAPPDATA%\\MisFinanzas).
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "MisFinanzas", "finanzas.db")
    return os.path.join(BASE_DIR, "data", "finanzas.db")


DEFAULT_DB_PATH = default_db_path()

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
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Mes (AAAA-MM) al que corresponde el movimiento; puede diferir de la fecha
    period         TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_card ON transactions(card_id);

-- Inversiones: cada posición vive en una aplicación/plataforma y tiene un tipo de activo
CREATE TABLE IF NOT EXISTS investments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    platform   TEXT    NOT NULL DEFAULT '',
    asset_type TEXT    NOT NULL DEFAULT 'otro',
    notes      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- TRM (pesos colombianos por dólar) por mes, para convertir inversiones en USD
CREATE TABLE IF NOT EXISTS fx_rates (
    month TEXT PRIMARY KEY,
    rate  REAL NOT NULL CHECK (rate > 0)
);

-- kind: contribution (aporte) | withdrawal (retiro) | valuation (valor actual)
CREATE TABLE IF NOT EXISTS investment_moves (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    investment_id INTEGER NOT NULL REFERENCES investments(id) ON DELETE CASCADE,
    date          TEXT    NOT NULL,
    kind          TEXT    NOT NULL CHECK (kind IN ('contribution', 'withdrawal', 'valuation')),
    amount        REAL    NOT NULL CHECK (amount >= 0),
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_inv_moves ON investment_moves(investment_id, date);

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


# Se ejecuta después del esquema para que también aplique a bases ya existentes
MIGRATIONS = """
UPDATE transactions SET period = substr(date, 1, 7) WHERE period IS NULL;
CREATE INDEX IF NOT EXISTS idx_tx_period ON transactions(period);
CREATE TRIGGER IF NOT EXISTS tx_default_period AFTER INSERT ON transactions
WHEN NEW.period IS NULL OR NEW.period = ''
BEGIN
    UPDATE transactions SET period = substr(NEW.date, 1, 7) WHERE id = NEW.id;
END;
"""


def migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "period" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN period TEXT")
    inv_cols = {r[1] for r in conn.execute("PRAGMA table_info(investments)")}
    if "currency" not in inv_cols:
        conn.execute("ALTER TABLE investments ADD COLUMN currency TEXT NOT NULL DEFAULT 'COP'")
    conn.executescript(MIGRATIONS)


def init_db(db_path):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
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
