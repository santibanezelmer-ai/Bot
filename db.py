"""
db.py — Capa de persistencia SQLite con aiosqlite.

Tablas:
  watches   — productos que un usuario quiere monitorear
  history   — historial de precios scrapeados
"""

import aiosqlite
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "price_bot.db"

# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass
class Watch:
    id: int
    chat_id: int
    url: str
    nombre: str                  # etiqueta amigable del usuario
    precio_inicial: Optional[float]
    precio_ultimo: Optional[float]
    umbral_pct: float            # alerta si baja >= X%  (0 = cualquier bajada)
    activo: bool

@dataclass
class PriceRecord:
    watch_id: int
    precio: Optional[float]
    en_stock: bool
    timestamp: str               # ISO-8601


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS watches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id         INTEGER NOT NULL,
                url             TEXT    NOT NULL,
                nombre          TEXT    NOT NULL,
                precio_inicial  REAL,
                precio_ultimo   REAL,
                stock_ultimo    INTEGER NOT NULL DEFAULT 1,
                umbral_pct      REAL    NOT NULL DEFAULT 0,
                activo          INTEGER NOT NULL DEFAULT 1,
                creado_en       TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id    INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                precio      REAL,
                en_stock    INTEGER NOT NULL DEFAULT 1,
                ts          TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_watches_chat ON watches(chat_id);
            CREATE INDEX IF NOT EXISTS idx_history_watch ON history(watch_id);
        """)
        await db.commit()
    logger.info("DB inicializada en %s", DB_PATH)


# ---------------------------------------------------------------------------
# Watches CRUD
# ---------------------------------------------------------------------------

async def add_watch(chat_id: int, url: str, nombre: str, umbral_pct: float = 0.0) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO watches (chat_id, url, nombre, umbral_pct)
               VALUES (?, ?, ?, ?)""",
            (chat_id, url, nombre, umbral_pct),
        )
        await db.commit()
        return cur.lastrowid


async def get_watches(chat_id: int) -> list[Watch]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM watches WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        )
        rows = await cur.fetchall()
    return [_row_to_watch(r) for r in rows]


async def get_all_active_watches() -> list[Watch]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM watches WHERE activo = 1")
        rows = await cur.fetchall()
    return [_row_to_watch(r) for r in rows]


async def delete_watch(watch_id: int, chat_id: int) -> bool:
    """Elimina un watch sólo si pertenece al chat_id dado."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM watches WHERE id = ? AND chat_id = ?",
            (watch_id, chat_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_precio(watch_id: int, precio: Optional[float], en_stock: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE watches
               SET precio_ultimo = ?,
                   stock_ultimo  = ?,
                   precio_inicial = COALESCE(precio_inicial, ?)
               WHERE id = ?""",
            (precio, int(en_stock), precio, watch_id),
        )
        await db.execute(
            "INSERT INTO history (watch_id, precio, en_stock) VALUES (?, ?, ?)",
            (watch_id, precio, int(en_stock)),
        )
        await db.commit()


async def get_history(watch_id: int, limit: int = 10) -> list[PriceRecord]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT watch_id, precio, en_stock, ts FROM history
               WHERE watch_id = ? ORDER BY ts DESC LIMIT ?""",
            (watch_id, limit),
        )
        rows = await cur.fetchall()
    return [PriceRecord(r["watch_id"], r["precio"], bool(r["en_stock"]), r["ts"]) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_watch(r: aiosqlite.Row) -> Watch:
    return Watch(
        id=r["id"],
        chat_id=r["chat_id"],
        url=r["url"],
        nombre=r["nombre"],
        precio_inicial=r["precio_inicial"],
        precio_ultimo=r["precio_ultimo"],
        umbral_pct=r["umbral_pct"],
        activo=bool(r["activo"]),
    )
