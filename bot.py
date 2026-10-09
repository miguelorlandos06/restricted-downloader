import os, re, asyncio, logging
from pyrogram import Client, filters
from pyrogram.types import Message

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("downloader")

# Cliente de usuario (para leer canales restringidos)
user_app = Client("user_dl", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
# Cliente del bot (para hablar contigo)
bot_app = Client("bot_dl", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def parse_link(link):
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m: return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r"t\.me/([^/]+)/(\d+)", link)
    if m: return m.group(1), int(m.group(2))
    return None, None

@bot_app.on_message(filters.text & filters.private)
async def handle_link(client, message: Message):
    link = message.text.strip()
    chat, msg_id = parse_link(link)
    if not chat:
        await message.reply_text("Enlace inválido. Usa t.me/c/... o t.me/canal/...")
        return
    status = await message.reply_text("Extrayendo...")
    try:
        async with user_app:
            msg = await user_app.get_messages(chat, msg_id)
            if not msg:
                await status.edit_text("Mensaje no encontrado.")
                return
            path = await user_app.download_media(msg, file_name="/tmp/")
        await status.edit_text(f"Descargado: {path}")
        await message.reply_document(path)
    except Exception as e:
        await status.edit_text(f"Error: {str(e)[:200]}")

async def main():
    await user_app.start()
    await bot_app.start()
    log.info("Bot listo")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
