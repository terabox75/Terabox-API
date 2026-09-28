import os
import asyncio
import traceback
import aiohttp
from pyrogram import Client, filters

# Credentials from Environment Variables
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Pyrogram Bot Client Initialization
app = Client(
    "terabox_tg_bot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN
)

async def fetch_terabox_direct_link(terabox_url: str) -> str:
    """
    Apni deployed Render Gateway API se direct download link fetch karna.
    """
    try:
        gateway_api_url = "https://terabox-gateway-g64q.onrender.com"
        api_endpoint = f"{gateway_api_url}/api?url={terabox_url}"
        
        print(f"[DEBUG] Fetching direct link from personal gateway: {api_endpoint}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=30) as resp:
                print(f"[DEBUG] API Response Status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"[DEBUG] API Response Data: {data}")
                    
                    # Gateway response se direct download link extract karna
                    direct_url = data.get("download_url") or data.get("direct_link") or data.get("url")
                    return direct_url
    except Exception as e:
        print(f"[ERROR] Exception in fetch_terabox_direct_link: {e}")
        traceback.print_exc()
    return None

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    print(f"[DEBUG] Received /start command from user: {message.from_user.id}")
    await message.reply("👋 **Hello!** Send me a `/terabox <link>` command and I will download and upload it to Telegram for you.")

@app.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message):
    print(f"[DEBUG] Received /terabox command from user: {message.from_user.id}")
    if len(message.command) < 2:
        await message.reply("❌ **Kripya Terabox link bhejein!**\n\nUsage: `/terabox <link>`")
        return
    
    url = message.command[1]
    status_msg = await message.reply("🔄 **Processing Terabox link via Personal Gateway...**")

    file_name = "terabox_downloaded_file.mp4"
    try:
        # Step 1: Get Direct Link from Personal Gateway API
        direct_link = await fetch_terabox_direct_link(url)
        if not direct_link:
            await status_msg.edit("❌ **Error:** Direct download link extract nahi ho paya. Gateway status check karein.")
            return

        await status_msg.edit("📥 **Downloading file to server...**")
        
        # Step 2: Download file locally
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
        
        # Step 3: Send file to Telegram
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
        # Cleanup temporary file
        if os.path.exists(file_name):
            os.remove(file_name)

if __name__ == "__main__":
    print("🤖 Starting Telegram Bot natively...")
    app.run()
