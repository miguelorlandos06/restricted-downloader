import os
import re
import uuid
import asyncio
import logging
import http.server
import socketserver
import threading
import requests
import urllib3
from pathlib import Path
from urllib.parse import quote
from pyrogram import Client as PyroClient
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ptbcontrib.aiohttp_request import AiohttpRequest

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
PORT = int(os.environ.get("PORT", 10000))
STREAM_BUCKET = "https://s3.todus.cu/stream"
WORK_DIR = "/tmp/restricted_jobs"
PENDING_TTL = 900
os.makedirs(WORK_DIR, exist_ok=True)

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

pending_txt = {}
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def fmt_size(b):
    if b < 1024: return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.2f} GB"


def progress_bar(pct, width=15):
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def parse_link(link):
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r"t\.me/([^/]+)/(\d+)", link)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def upload_to_stream(local_path):
    """Sube un archivo a s3.todus.cu/stream."""
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


class EditState:
    def __init__(self):
        self.last = 0.0
        self.last_text = ""
    def can_edit(self, text, force=False):
        import time
        now = time.time()
        if force:
            self.last = now; self.last_text = text; return True
        if now - self.last < 2.0: return False
        if text == self.last_text: return False
        self.last = now; self.last_text = text; return True


def _cleanup_pending():
    import time
    now = time.time()
    for u in [k for k, v in pending_txt.items() if now - v.get("ts", 0) > PENDING_TTL]:
        pending_txt.pop(u, None)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Restricted Downloader\n\n"
        "Envía un archivo .txt con URLs de mensajes de canales restringidos.\n"
        "Cada URL debe estar en una línea.\n\n"
        "Formatos:\n"
        "t.me/c/1234567890/456\n"
        "t.me/canal/456"
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa el .txt con múltiples enlaces de canales."""
    uid = update.effective_user.id
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("Solo acepto archivos .txt con URLs.")
        return

    status = await update.message.reply_text("📄 Leyendo archivo...")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text = content.decode("utf-8", errors="ignore")

        urls = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = URL_RE.search(line)
            if m:
                urls.append(m.group(0))

        if not urls:
            await status.edit_text("No encontre URLs en el archivo.")
            return

        total = len(urls)
        state = EditState()

        async def on_edit(txt, force=False):
            if state.can_edit(txt, force):
                try:
                    await status.edit_text(txt)
                except Exception:
                    pass

        await on_edit(f"🚀 Procesando {total} URLs...", force=True)

        resultados = []
        errores = []

        async with user_app:
            for i, url in enumerate(urls, 1):
                await on_edit(f"📥 [{i}/{total}] Extrayendo...\n{url[:60]}", force=True)
                try:
                    chat, msg_id = parse_link(url)
                    if not chat:
                        errores.append(f"❌ Enlace invalido: {url[:50]}")
                        continue

                    msg = await user_app.get_messages(chat, msg_id)
                    if not msg or not msg.media:
                        errores.append(f"❌ Sin media: {url[:50]}")
                        continue

                    # Descargar
                    temp_path = await user_app.download_media(msg, file_name=f"{WORK_DIR}/")
                    if not temp_path:
                        errores.append(f"❌ Sin descarga: {url[:50]}")
                        continue

                    size = os.path.getsize(temp_path)
                    name = os.path.basename(temp_path)

                    # Subir a S3
                    await on_edit(f"⬆️ [{i}/{total}] Subiendo a S3...\n{name}", force=True)
                    s3_url = await asyncio.to_thread(upload_to_stream, temp_path)

                    resultados.append({"name": name, "size": size, "url": s3_url})

                    # Limpiar archivo local
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass

                except Exception as e:
                    log.exception(f"Error en {url}")
                    errores.append(f"❌ {url[:40]}: {str(e)[:60]}")

        # ─── Armar respuesta formateada ───
        partes = []
        for r in resultados:
            partes.append(
                f"┎ NAME: {r['name']}\n"
                f"┠ SIZE: {fmt_size(r['size'])}\n"
                f"┖ URL: {r['url']}"
            )

        if errores:
            partes.append("⚠️ Errores:\n" + "\n".join(errores[:5]))

        respuesta = "\n\n".join(partes) if partes else "Sin resultados."
        if len(respuesta) > 4000:
            respuesta = respuesta[:4000] + "\n...(truncado)"

        await status.edit_text(respuesta)

    except Exception as e:
        log.exception("Error leyendo txt")
        await status.edit_text(f"Error: {str(e)[:200]}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Si escribe una URL suelta, la procesa."""
    text = (update.message.text or "").strip()
    m = URL_RE.search(text)
    if not m:
        await update.message.reply_text("Envia un archivo .txt o una URL.")
        return

    url = m.group(0)
    status = await update.message.reply_text("📥 Extrayendo...")

    try:
        chat, msg_id = parse_link(url)
        if not chat:
            await status.edit_text("Enlace invalido. Usa t.me/c/... o t.me/canal/...")
            return

        async with user_app:
            msg = await user_app.get_messages(chat, msg_id)
            if not msg or not msg.media:
                await status.edit_text("Sin media en el mensaje.")
                return
            temp_path = await user_app.download_media(msg, file_name=f"{WORK_DIR}/")

        if not temp_path:
            await status.edit_text("No se pudo descargar.")
            return

        size = os.path.getsize(temp_path)
        name = os.path.basename(temp_path)

        await status.edit_text(f"⬆️ Subiendo a S3 ({fmt_size(size)})...")
        s3_url = await asyncio.to_thread(upload_to_stream, temp_path)

        await status.edit_text(
            f"┎ NAME: {name}\n"
            f"┠ SIZE: {fmt_size(size)}\n"
            f"┖ URL: {s3_url}"
        )

        try:
            os.unlink(temp_path)
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
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot listo. Polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
