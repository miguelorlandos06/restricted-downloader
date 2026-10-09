import os
import re
import uuid
import asyncio
import logging
import http.server
import socketserver
import threading
from pathlib import Path
from urllib.parse import quote
import requests
import urllib3
from pyrogram import Client as PyroClient
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ptbcontrib.aiohttp_request import AiohttpRequest

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
PORT = int(os.environ.get("PORT", 10000))
STREAM_BUCKET = "https://s3.todus.cu/stream"
MAX_TELEGRAM_SIZE = 50 * 1024 * 1024  # 50 MB para enviar por Telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("downloader")
logging.getLogger("httpx").setLevel(logging.WARNING)

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


def fmt_size(b):
    if b < 1024: return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.2f} GB"


def upload_to_stream(local_path):
    """Sube un archivo a s3.todus.cu/stream y devuelve la URL publica."""
    filename = os.path.basename(local_path)
    prefix = uuid.uuid4().hex[:8]
    object_name = f"{prefix}_{filename}"
    url = f"{STREAM_BUCKET}/{quote(object_name)}"
    with open(local_path, "rb") as f:
        data = f.read()
    r = requests.put(
        url, data=data,
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(data))},
        timeout=600, verify=False,
    )
    r.raise_for_status()
    return url


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envia un enlace de mensaje y lo extraigo.\n\n"
        "Formatos:\n"
        "t.me/c/1234567890/456\n"
        "t.me/canal/456\n\n"
        "Los archivos grandes se suben a S3 y te devuelvo el enlace."
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

        if not path:
            text = msg.text or msg.caption or "(sin contenido)"
            await status.edit_text(f"Texto:\n\n{text}")
            return

        size = os.path.getsize(path)
        name = os.path.basename(path)
        log.info(f"Descargado: {name} ({fmt_size(size)})")

        # Si es pequeño, enviar directo por Telegram
        if size <= MAX_TELEGRAM_SIZE:
            await status.edit_text(f"Enviando archivo ({fmt_size(size)})...")
            await update.message.reply_document(path)
            await status.delete()
            try:
                os.unlink(path)
            except Exception:
                pass
            return

        # Si es grande, subir a S3
        await status.edit_text(f"Subiendo a S3 ({fmt_size(size)})...")
        s3_url = await asyncio.to_thread(upload_to_stream, path)

        await status.edit_text(
            f"Archivo subido\n\n"
            f"Nombre: {name}\n"
            f"Tamanio: {fmt_size(size)}\n"
            f"Enlace: {s3_url}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Descargar", url=s3_url)
            ]])
        )

        try:
            os.unlink(path)
        except Exception:
            pass

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
