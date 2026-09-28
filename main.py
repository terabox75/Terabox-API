import os
import asyncio
import traceback
import aiohttp
from fastapi import FastAPI
from pyrogram import Client, filters

app = FastAPI()

# Credentials from Environment Variables
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
        print(f"[DEBUG] Fetching direct link from API endpoint: {api_endpoint}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=15) as resp:
                print(f"[DEBUG] API Response Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("download_url")
    except Exception as e:
        print(f"[ERROR] Exception in fetch_terabox_direct_link: {e}")
        traceback.print_exc()
    return None

@app.get("/")
def home():
    return {"status": "Terabox Telegram Uploader Bot is Running!"}

# /start command handler
@tg_bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    print(f"[DEBUG] Received /start command from user: {message.from_user.id}")
    await message.reply("👋 **Hello!** Send me a `/terabox <link>` command to download and upload files to Telegram.")

@tg_bot.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message):
    print(f"[DEBUG] Received /terabox command from user: {message.from_user.id}")
    if len(message.command) < 2:
        await message.reply("❌ **Kripya Terabox link bhejein!**\n\nUsage: `/terabox <link>`")
        return
    
    url = message.command[1]
    status_msg = await message.reply("🔄 **Processing Terabox link...**")

    file_name = "terabox_downloaded_file.mp4"
    try:
        direct_link = await fetch_terabox_direct_link(url)
        if not direct_link:
            await status_msg.edit("❌ **Debug Error:** Direct download link extract nahi ho paya.")
            return

        await status_msg.edit("📥 **Downloading file to server...**")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(direct_link) as resp:
                if resp.status == 200:
                    with open(file_name, "wb") as f:
                        while chunk := await resp.content.read(1024 * 1024):
                            f.write(chunk)
                else:
                    await status_msg.edit(f"❌ **Download Error:** Status code {resp.status}")
                    return

        await status_msg.edit("📤 **Uploading file to Telegram...**")
        await client.send_document(
            chat_id=message.chat.id,
            document=file_name,
            caption=f"✅ **Terabox File Uploaded Successfully!**\n\n🔗 **Source:** `{url}`"
        )
        await status_msg.delete()

    except Exception as e:
        print(f"[ERROR] Critical error in terabox_command: {e}")
        traceback.print_exc()
        await status_msg.edit(f"❌ **Critical Error Occurred:**\n`{str(e)}`")
    
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)

# FastAPI Startup event with background task handling for Pyrogram
@app.on_event("startup")
async def startup_event():
    print("[DEBUG] FastAPI startup event triggered...")
    if API_ID and API_HASH and BOT_TOKEN:
        try:
            # Pyrogram bot ko start kar rahe hain
            await tg_bot.start()
            print("🤖 Telegram Bot started successfully!")
        except Exception as e:
            print(f"[ERROR] Failed to start Pyrogram bot: {e}")
            traceback.print_exc()
    else:
        print("⚠️ [WARNING] Telegram credentials are missing in environment variables!")

@app.on_event("shutdown")
async def shutdown_event():
    print("[DEBUG] FastAPI shutdown event triggered...")
    if tg_bot.is_initialized:
        await tg_bot.stop()
        print("🛑 Telegram Bot stopped safely.")
