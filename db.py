import sqlite3, logging, os
from typing import Any

logger = logging.getLogger("db")
DB_PATH = os.getenv("DB_PATH", "price_bot.db")

class DB:
    def __init__(self):
        self.path = DB_PATH
        self._init()
        logger.info(f"DB inicializada en {self.path}")

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    nombre TEXT NOT NULL,
                    ultimo_precio INTEGER,
                    ultimo_stock INTEGER DEFAULT 1,
                    creado_en TEXT DEFAULT (datetime('now')),
                    UNIQUE(chat_id, url)
                )
            """)

    def agregar(self, chat_id, url, nombre, precio, stock):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO productos (chat_id, url, nombre, ultimo_precio, ultimo_stock) VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id, url) DO UPDATE SET nombre=excluded.nombre, ultimo_precio=excluded.ultimo_precio, ultimo_stock=excluded.ultimo_stock",
                (chat_id, url, nombre, precio, int(stock))
            )
            return cur.lastrowid

    def eliminar(self, prod_id, chat_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM productos WHERE id=? AND chat_id=?", (prod_id, chat_id))

    def actualizar_precio(self, prod_id, precio, stock):
        with self._conn() as conn:
            conn.execute("UPDATE productos SET ultimo_precio=?, ultimo_stock=? WHERE id=?", (precio, int(stock), prod_id))

    def listar_por_chat(self, chat_id):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM productos WHERE chat_id=? ORDER BY creado_en DESC", (chat_id,)).fetchall()
        return [dict(r) for r in rows]

    def listar_todos(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM productos").fetchall()
        return [dict(r) for r in rows]

    def contar_por_chat(self, chat_id):
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as n FROM productos WHERE chat_id=?", (chat_id,)).fetchone()
        return row["n"]
