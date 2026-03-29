import os, logging, asyncio, re
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHECK_EVERY = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def scrape_producto(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Error: {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    nombre_el = soup.select_one("h1")
    nombre = nombre_el.get_text(strip=True) if nombre_el else url
    precio_el = soup.select_one("span[class*='price'], p[class*='price'], div[class*='price']")
    precio_texto = precio_el.get_text(strip=True) if precio_el else ""
    numeros = re.sub(r"[^\d]", "", precio_texto)
    precio = int(numeros) if "$" in precio_texto and numeros else None
    sin_stock = any(x in resp.text.lower() for x in ["sin stock", "agotado", "out of stock"])
    return {"nombre": nombre[:200], "precio": precio, "stock": not sin_stock}

async def revisar_todos(app):
    db = app.bot_data["db"]
    for prod in db.listar_todos():
        await asyncio.sleep(2)
        r = scrape_producto(prod["url"])
        if not r:
            continue
        alertas = []
        if r["precio"] and prod["ultimo_precio"] and r["precio"] < prod["ultimo_precio"]:
            pct = round((prod["ultimo_precio"] - r["precio"]) / prod["ultimo_precio"] * 100, 1)
            alertas.append("Bajo el precio " + str(pct) + "%\nAntes: $" + str(prod["ultimo_precio"]) + " Ahora: $" + str(r["precio"]))
        if r["stock"] and not prod["ultimo_stock"]:
            alertas.append("Volvio el stock!")
        if alertas:
            try:
                await app.bot.send_message(chat_id=prod["chat_id"], text="Alerta\n\n" + r["nombre"] + "\n" + prod["url"] + "\n\n" + "\n".join(alertas), disable_web_page_preview=True)
            except Exception as e:
                logger.error(e)
        db.actualizar_precio(prod["id"], r["precio"], r["stock"])

AYUDA = "Price Monitor Bot\n\n/agregar <url> - monitorear producto\n/lista - ver productos\n/verificar <url> - precio ahora\n/eliminar - eliminar producto"

async def cmd_start(u, c):
    await u.message.reply_text(AYUDA)

async def cmd_ayuda(u, c):
    await u.message.reply_text(AYUDA)

async def cmd_agregar(update, context):
    if not context.args:
        await update.message.reply_text("Uso: /agregar <url>")
        return
    url = context.args[0].strip()
    if not url.startswith("http"):
        await update.message.reply_text("URL invalida.")
        return
    db = context.bot_data["db"]
    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("Verificando...")
    r = scrape_producto(url)
    if not r:
        await msg.edit_text("No pude acceder a esa URL.")
        return
    db.agregar(chat_id=chat_id, url=url, nombre=r["nombre"], precio=r["precio"], stock=r["stock"])
    precio_str = "$" + str(r["precio"]) if r["precio"] else "No detectado"
    stock_str = "En stock" if r["stock"] else "Sin stock"
    await msg.edit_text("Agregado\n\n" + r["nombre"] + "\n" + precio_str + "\n" + stock_str)

async def cmd_verificar(update, context):
    if not context.args:
        await update.message.reply_text("Uso: /verificar <url>")
        return
    msg = await update.message.reply_text("Verificando...")
    r = scrape_producto(context.args[0].strip())
    if not r:
        await msg.edit_text("No pude acceder.")
        return
    precio_str = "$" + str(r["precio"]) if r["precio"] else "No detectado"
    stock_str = "En stock" if r["stock"] else "Sin stock"
    await msg.edit_text(r["nombre"] + "\n" + precio_str + "\n" + stock_str)

async def cmd_lista(update, context):
    db = context.bot_data["db"]
    prods = db.listar_por_chat(update.effective_chat.id)
    if not prods:
        await update.message.reply_text("Sin productos. Usa /agregar <url>")
        return
    lineas = [str(len(prods)) + " productos:\n"]
    for p in prods:
        stock_str = "En stock" if p["ultimo_stock"] else "Sin stock"
        precio_str = "$" + str(p["ultimo_precio"]) if p["ultimo_precio"] else "-"
        lineas.append(p["nombre"][:50] + "\n  " + precio_str + " " + stock_str)
    await update.message.reply_text("\n".join(lineas))

async def cmd_eliminar(update, context):
    db = context.bot_data["db"]
    prods = db.listar_por_chat(update.effective_chat.id)
    if not prods:
        await update.message.reply_text("Sin productos.")
        return
    botones = [[InlineKeyboardButton(p["nombre"][:40], callback_data="del:" + str(p["id"]))] for p in prods]
    await update.message.reply_text("Cual eliminar?", reply_markup=InlineKeyboardMarkup(botones))

async def callback_eliminar(update, context):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split(":")[1])
    context.bot_data["db"].eliminar(prod_id, query.message.chat_id)
    await query.edit_message_text("Eliminado.")

async def post_init(app):
    app.bot_data["db"] = DB()
    s = AsyncIOScheduler()
    s.add_job(revisar_todos, "interval", minutes=CHECK_EVERY, args=[app], next_run_time=None)
    s.start()
    app.bot_data["scheduler"] = s

async def post_shutdown(app):
    s = app.bot_data.get("scheduler")
    if s:
        s.shutdown(wait=False)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("agregar", cmd_agregar))
    app.add_handler(CommandHandler("verificar", cmd_verificar))
    app.add_handler(CommandHandler("lista", cmd_lista))
    app.add_handler(CommandHandler("eliminar", cmd_eliminar))
    app.add_handler(CallbackQueryHandler(callback_eliminar, pattern=r"^del:"))
    logger.info("Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
