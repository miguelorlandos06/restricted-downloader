import os
import re
import asyncio
import logging
import http.server
import socketserver
import threading
from pyrogram import Client, filters
from pyrogram.types import Message

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("downloader")

user_app = Client(
    "user_dl",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True,
)

bot_app = Client(
    "bot_dl",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
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


@bot_app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    await message.reply_text(
        "Envia un enlace de mensaje y lo extraigo.\n\n"
        "Formatos:\n"
        "t.me/c/1234567890/456\n"
        "t.me/canal/456"
    )


@bot_app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def handle_link(client, message: Message):
    link = message.text.strip()
    chat, msg_id = parse_link(link)
    if not chat:
        await message.reply_text("Enlace invalido. Usa t.me/c/... o t.me/canal/...")
        return

    status = await message.reply_text("Extrayendo...")

    try:
        async with user_app:
            msg = await user_app.get_messages(chat, msg_id)
            if not msg:
                await status.edit_text("Mensaje no encontrado.")
                return

            path = await user_app.download_media(msg, file_name="/tmp/")

        if path:
            await status.edit_text("Enviando archivo...")
            await message.reply_document(path)
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


async def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    await bot_app.start()
    me = await bot_app.get_me()
    log.info(f"Bot listo: @{me.username}")
    log.info("Esperando enlaces...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
