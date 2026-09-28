import os
import aiohttp
from fastapi import FastAPI
from pyrogram import Client, filters

app = FastAPI()

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Pyrogram Bot Client
tg_bot = Client(
    "terabox_tg_bot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN
)

async def fetch_terabox_direct_link(terabox_url: str) -> str:
    try:
        api_endpoint = f"https://terabox-dl-api.example.com/api?url={terabox_url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("download_url")
    except Exception as e:
        print(f"Extraction Error: {e}")
    return None

@app.get("/")
def home():
    return {"status": "Terabox Telegram Uploader Bot is Running!"}

@tg_bot.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message):
    if len(message.command) < 2:
        await message.reply("❌ **Kripya Terabox link bhejein!**\n\nUsage: `/terabox <link>`")
        return
    
    url = message.command[1]
    status_msg = await message.reply("🔄 **Processing Terabox link...** Please wait.")

    file_name = "terabox_downloaded_file.mp4"
    try:
        direct_link = await fetch_terabox_direct_link(url)
        if not direct_link:
            await status_msg.edit("❌ **Failed:** Direct download link extract nahi ho paya.")
            return

        await status_msg.edit("📥 **Downloading file to server...**")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(direct_link) as resp:
                if resp.status == 200:
                    with open(file_name, "wb") as f:
                        while chunk := await resp.content.read(1024 * 1024):
                            f.write(chunk)
                else:
                    await status_msg.edit("❌ **Failed:** Direct link se file download nahi ho saki.")
                    return

        await status_msg.edit("📤 **Uploading file to Telegram...**")

        await client.send_document(
            chat_id=message.chat.id,
            document=file_name,
            caption=f"✅ **Terabox File Uploaded Successfully!**\n\n🔗 **Source:** `{url}`"
        )
        await status_msg.delete()

    except Exception as f_err:
        await status_msg.edit(f"❌ **Error:** `{str(f_err)}`")
    
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)

# FastAPI startup event
@app.on_event("startup")
async def startup_event():
    if API_ID and API_HASH and BOT_TOKEN:
        # Pyrogram client ko start karke background listening enable karna
        await tg_bot.start()
        print("🤖 Telegram Bot started and listening for messages!")
    else:
        print("⚠️ Warning: Telegram credentials missing!")

@app.on_event("shutdown")
async def shutdown_event():
    if tg_bot.is_initialized:
        await tg_bot.stop()
        print("🛑 Telegram Bot stopped.")
