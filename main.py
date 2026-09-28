import os
import aiohttp
from fastapi import FastAPI, HTTPException
from pyrogram import Client, filters

app = FastAPI()

# Credentials from Environment Variables
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Pyrogram Bot Client
tg_bot = Client("terabox_tg_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def fetch_terabox_direct_link(terabox_url: str) -> str:
    """
    Terabox link se direct stream/download link extract karne ka logic.
    Yeh aapke API backend se connect karega ya link parse karega.
    """
    try:
        # Aap yahan apna Terabox API URL daal sakte hain ya scraping logic use kar sakte hain
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
    return {"status": "Terabox Telegram Uploader Bot is Running Successfully!"}

@tg_bot.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message):
    if len(message.command) < 2:
        await message.reply("❌ **Kripya Terabox link bhejein!**\n\nUsage: `/terabox <link>`")
        return
    
    url = message.command[1]
    status_msg = await message.reply("🔄 **Processing Terabox link...** Please wait.")

    file_name = "terabox_downloaded_file.mp4"
    try:
        # Step 1: Get Direct Download Link
        direct_link = await fetch_terabox_direct_link(url)
        if not direct_link:
            await status_msg.edit("❌ **Failed:** Direct download link extract nahi ho paya.")
            return

        await status_msg.edit("📥 **Downloading file to server...**")
        
        # Step 2: Download file locally from the direct link
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

        # Step 3: Send file to Telegram chat
        await client.send_document(
            chat_id=message.chat.id,
            document=file_name,
            caption=f"✅ **Terabox File Uploaded Successfully!**\n\n🔗 **Source:** `{url}`"
        )
        
        # Cleanup status message
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit(f"❌ **An Error Occurred:**\n`{str(e)}`")
    
    finally:
        # Server storage clean karne ke liye local file delete kar dein
        if os.path.exists(file_name):
            os.remove(file_name)

# FastAPI Startup aur Shutdown events par Telegram Bot ko manage karna
@app.on_event("startup")
async def startup_event():
    if API_ID and API_HASH and BOT_TOKEN:
        await tg_bot.start()
        print("🤖 Telegram Bot started successfully in background!")
    else:
        print("⚠️ Warning: Telegram credentials missing in environment variables!")

@app.on_event("shutdown")
async def shutdown_event():
    if tg_bot.is_initialized:
        await tg_bot.stop()
        print("🛑 Telegram Bot stopped.")
