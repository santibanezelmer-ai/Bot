"""
bot.py — Entry point del bot de Telegram.

Comandos:
  /start          — bienvenida y ayuda
  /agregar <url> [nombre] [umbral%]  — agrega un producto a monitorear
  /lista          — muestra todos los watches del usuario
  /ver <id>       — chequea precio ahora mismo
  /historial <id> — últimos 10 precios registrados
  /borrar <id>    — elimina un watch
  /ayuda          — resumen de comandos
"""

import os
import logging
import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

import db
from db import Watch
from scraper import PriceScraper
from monitor import PriceMonitor, _formatear_precio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _watch_resumen(w: Watch) -> str:
    precio = _formatear_precio(w.precio_ultimo)
    umbral = f"{w.umbral_pct:.0f}%" if w.umbral_pct > 0 else "cualquier bajada"
    stock  = "✅" if w.precio_ultimo is not None else "❓"
    return (
        f"{stock} *[{w.id}] {w.nombre}*\n"
        f"    Precio actual: `{precio}`\n"
        f"    Umbral alerta: {umbral}\n"
        f"    🔗 [Link]({w.url})"
    )


def _parse_args(text: Optional[str]) -> list[str]:
    return (text or "").split() if text else []


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Bot de monitoreo de precios*\n\n"
        "Te aviso cuando baje el precio o vuelva a haber stock "
        "en Falabella, Paris, Samsung, Cruz Verde y más.\n\n"
        "Usa /ayuda para ver los comandos disponibles.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 *Comandos disponibles*\n\n"
        "`/agregar <url>` — agrega producto a monitorear\n"
        "`/agregar <url> nombre` — con nombre personalizado\n"
        "`/agregar <url> nombre 10` — alerta solo si baja ≥10%\n\n"
        "`/lista` — ver todos tus productos\n"
        "`/ver <id>` — consultar precio ahora\n"
        "`/historial <id>` — últimos 10 precios\n"
        "`/borrar <id>` — dejar de monitorear\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_agregar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _parse_args(context.args[0] if context.args else None)
    # Reconstruir: los args ya vienen separados por telegram.ext
    partes = context.args or []

    if not partes:
        await update.message.reply_text(
            "⚠️ Uso: `/agregar <url> [nombre] [umbral%]`\n"
            "Ejemplo: `/agregar https://falabella.com/... Samsung TV 10`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = partes[0]
    if not url.startswith("http"):
        await update.message.reply_text("❌ La URL debe comenzar con http.")
        return

    nombre   = " ".join(partes[1:-1]) if len(partes) > 2 else (partes[1] if len(partes) == 2 else url[:40])
    umbral   = 0.0
    if len(partes) >= 2:
        try:
            umbral = float(partes[-1])
            if len(partes) == 2:
                # El segundo argumento era el umbral, no el nombre
                nombre = url[:40]
        except ValueError:
            if len(partes) >= 2:
                nombre = " ".join(partes[1:])

    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("🔎 Verificando producto…")

    # Chequeo inicial inmediato
    monitor: PriceMonitor = context.bot_data["monitor"]
    watch_id = await db.add_watch(chat_id, url, nombre or url[:40], umbral)
    watches = await db.get_watches(chat_id)
    watch = next((w for w in watches if w.id == watch_id), None)

    if watch:
        result = await monitor.chequear_ahora(watch)
        precio_str = _formatear_precio(result.precio)
        stock_str  = "✅ En stock" if result.en_stock else "❌ Sin stock"
        umbral_str = f"≥ {umbral:.0f}%" if umbral > 0 else "cualquier bajada"
        await msg.edit_text(
            f"✅ *Agregado correctamente*\n\n"
            f"📦 *{nombre}*\n"
            f"💰 Precio inicial: `{precio_str}`\n"
            f"📊 Stock: {stock_str}\n"
            f"🔔 Alerta si baja: {umbral_str}\n\n"
            f"ID del watch: `{watch_id}` — úsalo con /ver o /borrar",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await msg.edit_text("❌ Error al agregar el producto. Intenta de nuevo.")


async def cmd_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    watches = await db.get_watches(chat_id)

    if not watches:
        await update.message.reply_text(
            "📭 No tienes productos monitoreados.\n"
            "Usa /agregar <url> para comenzar.",
        )
        return

    lines = [f"📋 *Tus {len(watches)} producto(s):*\n"]
    for w in watches:
        lines.append(_watch_resumen(w))

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def cmd_ver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    partes = context.args or []
    if not partes or not partes[0].isdigit():
        await update.message.reply_text("Uso: `/ver <id>`", parse_mode=ParseMode.MARKDOWN)
        return

    watch_id = int(partes[0])
    chat_id  = update.effective_chat.id
    watches  = await db.get_watches(chat_id)
    watch    = next((w for w in watches if w.id == watch_id), None)

    if not watch:
        await update.message.reply_text("❌ ID no encontrado o no es tuyo.")
        return

    msg = await update.message.reply_text(f"🔎 Consultando precio de *{watch.nombre}*…", parse_mode=ParseMode.MARKDOWN)
    monitor: PriceMonitor = context.bot_data["monitor"]
    result = await monitor.chequear_ahora(watch)

    precio_str = _formatear_precio(result.precio)
    stock_str  = "✅ En stock" if result.en_stock else "❌ Sin stock"

    # Comparar con precio anterior
    comparacion = ""
    if watch.precio_ultimo and result.precio:
        diff = watch.precio_ultimo - result.precio
        if diff > 0:
            comparacion = f"\n📉 Bajó {_formatear_precio(diff)} respecto al último registro"
        elif diff < 0:
            comparacion = f"\n📈 Subió {_formatear_precio(-diff)} respecto al último registro"
        else:
            comparacion = "\n➡️ Sin cambios"

    await msg.edit_text(
        f"📦 *{watch.nombre}*\n"
        f"💰 Precio: `{precio_str}`\n"
        f"📊 {stock_str}"
        f"{comparacion}\n\n"
        f"🔗 [Ver en tienda]({watch.url})",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=False,
    )


async def cmd_historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    partes = context.args or []
    if not partes or not partes[0].isdigit():
        await update.message.reply_text("Uso: `/historial <id>`", parse_mode=ParseMode.MARKDOWN)
        return

    watch_id = int(partes[0])
    chat_id  = update.effective_chat.id
    watches  = await db.get_watches(chat_id)
    watch    = next((w for w in watches if w.id == watch_id), None)

    if not watch:
        await update.message.reply_text("❌ ID no encontrado o no es tuyo.")
        return

    records = await db.get_history(watch_id, limit=10)
    if not records:
        await update.message.reply_text("📭 Sin historial aún.")
        return

    lines = [f"📊 *Historial: {watch.nombre}*\n"]
    for r in records:
        stock_icon = "✅" if r.en_stock else "❌"
        precio_str = _formatear_precio(r.precio)
        # Mostrar solo fecha y hora sin segundos
        ts = r.timestamp[:16].replace("T", " ")
        lines.append(f"`{ts}` — {precio_str} {stock_icon}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    partes = context.args or []
    if not partes or not partes[0].isdigit():
        await update.message.reply_text("Uso: `/borrar <id>`", parse_mode=ParseMode.MARKDOWN)
        return

    watch_id = int(partes[0])
    chat_id  = update.effective_chat.id

    # Pedir confirmación con inline keyboard
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sí, borrar", callback_data=f"del:{watch_id}"),
            InlineKeyboardButton("❌ Cancelar",   callback_data="del:cancel"),
        ]
    ])
    await update.message.reply_text(
        f"¿Seguro que quieres dejar de monitorear el watch `{watch_id}`?",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )


async def callback_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data    = query.data
    chat_id = update.effective_chat.id

    if data == "del:cancel":
        await query.edit_message_text("❌ Cancelado.")
        return

    watch_id = int(data.split(":")[1])
    ok = await db.delete_watch(watch_id, chat_id)
    if ok:
        await query.edit_message_text(f"🗑️ Watch `{watch_id}` eliminado.", parse_mode=ParseMode.MARKDOWN)
    else:
        await query.edit_message_text("❌ No se pudo eliminar (ID incorrecto o no es tuyo).")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def post_init(app) -> None:
    await db.init_db()

    scraper = PriceScraper()
    await scraper.start()

    async def send_alert(chat_id: int, mensaje: str) -> None:
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=mensaje,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error("Error enviando alerta a %d: %s", chat_id, e)

    monitor = PriceMonitor(scraper, on_alert=send_alert)
    monitor.start()

    app.bot_data["scraper"] = scraper
    app.bot_data["monitor"] = monitor


async def post_shutdown(app) -> None:
    monitor: PriceMonitor = app.bot_data.get("monitor")
    scraper: PriceScraper = app.bot_data.get("scraper")
    if monitor:
        monitor.stop()
    if scraper:
        await scraper.stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en variables de entorno")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("ayuda",     cmd_ayuda))
    app.add_handler(CommandHandler("agregar",   cmd_agregar))
    app.add_handler(CommandHandler("lista",     cmd_lista))
    app.add_handler(CommandHandler("ver",       cmd_ver))
    app.add_handler(CommandHandler("historial", cmd_historial))
    app.add_handler(CommandHandler("borrar",    cmd_borrar))
    app.add_handler(CallbackQueryHandler(callback_borrar, pattern=r"^del:"))

    logger.info("Bot corriendo…")
    app.run_polling()


if __name__ == "__main__":
    main()
