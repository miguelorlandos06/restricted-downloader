import os
import re
import asyncio
import logging
import http.server
import socketserver
import threading
from pyrogram import Client as PyroClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ptbcontrib.aiohttp_request import AiohttpRequest

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("downloader")
logging.getLogger("httpx").setLevel(logging.WARNING)

# Cliente de usuario (Pyrogram) - solo se conecta bajo demanda
user_app = PyroClient(
    "user_dl",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)


def parse_link(link):
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r"t\.me/([^/]+)/(\d+)", link)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envia un enlace de mensaje y lo extraigo.\n\n"
        "Formatos:\n"
        "t.me/c/1234567890/456\n"
        "t.me/canal/456"
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = (update.message.text or "").strip()
    chat, msg_id = parse_link(link)
    if not chat:
        await update.message.reply_text("Enlace invalido. Usa t.me/c/... o t.me/canal/...")
        return

    status = await update.message.reply_text("Extrayendo...")

    try:
        async with user_app:
            msg = await user_app.get_messages(chat, msg_id)
            if not msg:
                await status.edit_text("Mensaje no encontrado.")
                return
            path = await user_app.download_media(msg, file_name="/tmp/")

        if path:
            await status.edit_text("Enviando archivo...")
            await update.message.reply_document(path)
            await status.delete()
            try:
                os.unlink(path)
            except Exception:
                pass
        else:
            text = msg.text or msg.caption or "(sin contenido)"
            await status.edit_text(f"Texto:\n\n{text}")

    except Exception as e:
        log.exception("Error")
        await status.edit_text(f"Error: {str(e)[:200]}")


def run_health_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()


def main():
    threading.Thread(target=run_health_server, daemon=True).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(AiohttpRequest(connection_pool_size=256))
        .get_updates_request(AiohttpRequest())
        .build()
    )
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    log.info("Bot listo. Polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
