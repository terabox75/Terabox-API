import os
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
    Terabox link se direct stream/download link extract karne ka logic.
    """
    try:
        api_endpoint = f"https://terabox-dl-api.example.com/api?url={terabox_url}"
        print(f"[DEBUG] Fetching direct link from API endpoint: {api_endpoint}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_endpoint, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("download_url")
    except Exception as e:
        print(f"[ERROR] Exception in fetch_terabox_direct_link: {e}")
    return None

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    print(f"[DEBUG] Received /start command from user: {message.from_user.id}")
    await message.reply("👋 **Hello!** Send me a `/terabox <link>` command to download and upload files to Telegram.")

@app.on_message(filters.command("terabox") & filters.private)
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
            await status_msg.edit("❌ **Error:** Direct download link extract nahi ho paya.")
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
        print(f"[ERROR] Critical error: {e}")
        await status_msg.edit(f"❌ **Critical Error Occurred:**\n`{str(e)}`")
    
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)

if __name__ == "__main__":
    print("🤖 Starting Telegram Bot natively...")
    app.run()
