import os
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
    """
    Terabox link se direct stream/download link extract karne ka logic with debugging.
    """
    try:
        api_endpoint = f"https://terabox-dl-api.example.com/api?url={terabox_url}"
        print(f"[DEBUG] Fetching direct link from API endpoint: {api_endpoint}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=15) as resp:
                print(f"[DEBUG] API Response Status: {resp.status}")
                text_response = await resp.text()
                print(f"[DEBUG] API Raw Response: {text_response[:300]}")
                
                if resp.status == 200:
                    data = await resp.json()
                    download_url = data.get("download_url")
                    print(f"[DEBUG] Extracted Download URL: {download_url}")
                    return download_url
    except Exception as e:
        print(f"[ERROR] Exception in fetch_terabox_direct_link: {e}")
        traceback.print_exc()
    return None

@app.get("/")
def home():
    return {"status": "Terabox Telegram Uploader Bot is Running with Debug Mode!"}

@tg_bot.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message):
    print(f"[DEBUG] Received /terabox command from user: {message.from_user.id}")
    if len(message.command) < 2:
        await message.reply("❌ **Kripya Terabox link bhejein!**\n\nUsage: `/terabox <link>`")
        return
    
    url = message.command[1]
    status_msg = await message.reply("🔄 **Processing Terabox link...** (Debugging Enabled)")

    file_name = "terabox_downloaded_file.mp4"
    try:
        # Step 1: Get Direct Link
        direct_link = await fetch_terabox_direct_link(url)
        if not direct_link:
            await status_msg.edit("❌ **Debug Error:** Direct download link extract nahi ho paya. (Check server logs)")
            return

        await status_msg.edit("📥 **Downloading file to server...**")
        
        # Step 2: Download file locally
        print(f"[DEBUG] Starting download from: {direct_link}")
        async with aiohttp.ClientSession() as session:
            async with session.get(direct_link) as resp:
                print(f"[DEBUG] Download Response Status: {resp.status}")
                if resp.status == 200:
                    with open(file_name, "wb") as f:
                        while chunk := await resp.content.read(1024 * 1024):
                            f.write(chunk)
                    print(f"[DEBUG] File downloaded successfully locally. Size: {os.path.getsize(file_name)} bytes")
                else:
                    await status_msg.edit(f"❌ **Download Error:** Status code {resp.status}")
                    return

        await status_msg.edit("📤 **Uploading file to Telegram...**")
        print(f"[DEBUG] Starting upload to Telegram chat ID: {message.chat.id}")

        # Step 3: Send to Telegram
        await client.send_document(
            chat_id=message.chat.id,
            document=file_name,
            caption=f"✅ **Terabox File Uploaded Successfully!**\n\n🔗 **Source:** `{url}`"
        )
        print("[DEBUG] File uploaded to Telegram successfully!")
        await status_msg.delete()

    except Exception as e:
        print(f"[ERROR] Critical error in terabox_command: {e}")
        traceback.print_exc()
        await status_msg.edit(f"❌ **Critical Error Occurred:**\n`{str(e)}`")
    
    finally:
        # Cleanup
        if os.path.exists(file_name):
            os.remove(file_name)
            print("[DEBUG] Temporary local file cleaned up.")

# FastAPI Startup event
@app.on_event("startup")
async def startup_event():
    print("[DEBUG] FastAPI startup event triggered. Starting Telegram Bot...")
    if API_ID and API_HASH and BOT_TOKEN:
        try:
            await tg_bot.start()
            print("🤖 Telegram Bot started successfully and listening for messages!")
        except Exception as e:
            print(f"[ERROR] Failed to start Pyrogram bot: {e}")
            traceback.print_exc()
    else:
        print("⚠️ [WARNING] Telegram credentials (API_ID, API_HASH, BOT_TOKEN) are missing or invalid in environment variables!")

@app.on_event("shutdown")
async def shutdown_event():
    print("[DEBUG] FastAPI shutdown event triggered. Stopping Telegram Bot...")
    if tg_bot.is_initialized:
        await tg_bot.stop()
        print("🛑 Telegram Bot stopped safely.")
