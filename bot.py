import os
import json
import asyncio
import logging
import tempfile
from typing import Any, Sequence

import httpx
from dotenv import load_dotenv
from telegram import Message, MessageEntity, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Configuration ---
channel_ids = [
    int(channel_id.strip())
    for channel_id in os.getenv("CHANNEL_IDS", "-100_00000_00000").split(",")
]
bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise ValueError("BOT_TOKEN environment variable is required")

# QQ / Napcat Config
NAPCAT_URL = os.getenv("NAPCAT_HTTP_URL")
QQ_GROUP_ID = int(os.getenv("QQ_GROUP_ID", 0))
QQ_ID = int(os.getenv("QQ_ID", 10000))
QQ_NICKNAME = os.getenv("QQ_NICKNAME", "QQ")

# Discord Config
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Feishu Config
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_RECEIVE_ID = os.getenv("FEISHU_RECEIVE_ID")
FEISHU_RECEIVE_ID_TYPE = os.getenv("FEISHU_RECEIVE_ID_TYPE", "chat_id")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL") # NEW

# State & HTTP Client
media_group_messages: dict[str, list[Message]] = {}
MEDIA_GROUP_TIMEOUT = 5
http_client = httpx.AsyncClient(timeout=30.0) # Increased timeout for video downloads


# --- Platform Sender Implementations ---

async def send_to_qq(text: str | None, photo_urls: list[str], video_urls: list[str], urls: dict[str, str]):
    if not NAPCAT_URL or not QQ_GROUP_ID:
        return

    try:
        content_list: list[dict[str, Any]] = []
        
        for video_url in video_urls:
            content_list.append({"type": "video", "data": {"file": video_url}})
            
        for photo_url in photo_urls:
            content_list.append({"type": "image", "data": {"file": photo_url}})
            
        if text:
            content_list.append({"type": "text", "data": {"text": text}})
            
        for link_text, url in urls.items():
            content_list.append({"type": "text", "data": {"text": f"{link_text}\n{url}"}})

        if not content_list:
            return

        if len(content_list) == 1 and content_list[0]["type"] == "text" and len(text or "") <= 200:
            post_url = f"{NAPCAT_URL}/send_group_msg"
            json_data = {"group_id": QQ_GROUP_ID, "message": content_list}
        else:
            post_url = f"{NAPCAT_URL}/send_group_forward_msg"
            messages_list = [
                {
                    "type": "node",
                    "data": {
                        "user_id": QQ_ID,
                        "nickname": QQ_NICKNAME,
                        "content": content,
                    },
                }
                for content in content_list
            ]
            json_data = {"group_id": QQ_GROUP_ID, "messages": messages_list}

        response = await http_client.post(post_url, json=json_data)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"QQ Sender failed: {e}")


async def compress_video(video_bytes: bytes) -> bytes | None:
    """Compresses video with a dynamic bitrate cap to maximize quality within 8MB."""
    logging.info(f"Compressing video (original size: {len(video_bytes) / 1024 / 1024:.2f} MB)...")
    
    fd_in, in_path = tempfile.mkstemp(suffix=".mp4")
    out_path = in_path + "_out.mp4"
    
    try:
        with os.fdopen(fd_in, 'wb') as f:
            f.write(video_bytes)
            
        # -crf 24: High baseline quality for shorter clips
        # -maxrate 580k & -bufsize 1160k: Strict ceiling to ensure 90s videos stay under ~7.5MB
        # scale=-2:720: Maintains 720p HD resolution 
        # -b:a 96k: Much better audio quality
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', in_path,
            '-c:v', 'libx264', '-preset', 'faster', 
            '-crf', '24', '-maxrate', '580k', '-bufsize', '1160k',
            '-vf', 'scale=-2:720', 
            '-c:a', 'aac', '-b:a', '96k',
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr_data = await process.communicate()
        
        if process.returncode != 0:
            logging.error(f"FFmpeg failed: {stderr_data.decode()}")
            return None
            
        if os.path.exists(out_path):
            with open(out_path, 'rb') as f:
                compressed = f.read()
                logging.info(f"Compression finished. New size: {len(compressed) / 1024 / 1024:.2f} MB.")
                return compressed
    except Exception as e:
        logging.error(f"Compression error: {e}")
    finally:
        # Clean up temporary files
        if os.path.exists(in_path): os.remove(in_path)
        if os.path.exists(out_path): os.remove(out_path)
        
    return None


async def extract_thumbnail(video_bytes: bytes) -> bytes | None:
    """Extracts the first frame of a video using FFmpeg to use as a cover image."""
    fd_in, in_path = tempfile.mkstemp(suffix=".mp4")
    fd_out, out_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd_out) # Close so FFmpeg can overwrite it cleanly
    
    try:
        with os.fdopen(fd_in, 'wb') as f:
            f.write(video_bytes)
            
        # Extract exactly 1 frame at the 0-second mark
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', in_path,
            '-ss', '00:00:00.000', '-vframes', '1',
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if process.returncode == 0 and os.path.exists(out_path):
            with open(out_path, 'rb') as f:
                return f.read()
    except Exception as e:
        logging.error(f"Thumbnail extraction error: {e}")
    finally:
        if os.path.exists(in_path): os.remove(in_path)
        if os.path.exists(out_path): os.remove(out_path)
        
    return None


async def send_to_discord(text: str | None, photo_urls: list[str], video_urls: list[str], urls: dict[str, str]):
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        return

    api_url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    
    content = text or ""
    for link_text, url in urls.items():
        content += f"\n{link_text}: {url}"
        
    payload: dict[str, Any] = {"content": content}
    if photo_urls:
        payload["embeds"] = [{"image": {"url": photo_url}} for photo_url in photo_urls[:10]]
        
    if not content and not photo_urls and not video_urls:
        return

    try:
        if not video_urls:
            headers["Content-Type"] = "application/json"
            response = await http_client.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
            return

        data = {"payload_json": json.dumps(payload)}
        files = []
        
        # Discord's strictest limit is 8MB. We use 8MB as our ceiling.
        MAX_PAYLOAD_SIZE = 8 * 1024 * 1024 
        total_size = 0
        
        for i, video_url in enumerate(video_urls):
            res = await http_client.get(video_url)
            if res.status_code == 200:
                video_bytes = res.content
                
                # If over 8MB, crush it with FFmpeg
                if total_size + len(video_bytes) > MAX_PAYLOAD_SIZE:
                    logging.info(f"Video {i} ({len(video_bytes)/1024/1024:.2f}MB) exceeds 8MB Discord limit. Compressing...")
                    compressed_bytes = await compress_video(video_bytes)
                    if compressed_bytes:
                        video_bytes = compressed_bytes
                        
                # Ensure it actually fits after compression
                if total_size + len(video_bytes) <= MAX_PAYLOAD_SIZE:
                    total_size += len(video_bytes)
                    files.append((f"files[{i}]", (f"video_{i}.mp4", video_bytes, "video/mp4")))
                else:
                    logging.warning(f"Video {i} is STILL too large ({len(video_bytes)/1024/1024:.2f}MB) after compression. Skipping.")

        if not files:
            headers["Content-Type"] = "application/json"
            payload["content"] += "\n\n*(Note: Attached videos were too large for Discord)*"
            response = await http_client.post(api_url, headers=headers, json=payload)
        else:
            response = await http_client.post(api_url, headers=headers, data=data, files=files)
            
        response.raise_for_status()

    except httpx.HTTPStatusError as e:
        # FALLBACK: If Discord STILL complains it's too large, send text/images only
        if e.response.status_code == 413:
            logging.error("Discord Payload Too Large (413). Attempting text/image-only fallback.")
            try:
                fallback_payload = {
                    "content": content + "\n\n*(Media omitted: File too large for Discord's limits)*"
                }
                if photo_urls:
                    fallback_payload["embeds"] = [{"image": {"url": photo_url}} for photo_url in photo_urls[:10]]
                
                headers["Content-Type"] = "application/json"
                await http_client.post(api_url, headers=headers, json=fallback_payload)
            except Exception as fallback_e:
                logging.error(f"Discord fallback failed: {fallback_e}")
        else:
            logging.error(f"Discord API HTTP error: {e}")
    except Exception as e:
        logging.error(f"Discord API error: {e}")



async def get_feishu_tenant_access_token() -> str | None:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    response = await http_client.post(url, json=payload)
    if response.status_code == 200:
        return response.json().get("tenant_access_token")
    return None


async def upload_image_to_feishu(token: str, photo_url: str) -> str | None:
    img_response = await http_client.get(photo_url)
    if img_response.status_code != 200:
        return None
    upload_url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"image_type": "message"}
    files = {"image": ("image.jpg", img_response.content, "image/jpeg")}
    response = await http_client.post(upload_url, headers=headers, data=data, files=files)
    if response.status_code == 200 and response.json().get("code") == 0:
        return response.json().get("data", {}).get("image_key")
    return None


async def send_to_feishu(text: str | None, photo_urls: list[str], video_urls: list[str], urls: dict[str, str]):
    if not FEISHU_WEBHOOK_URL:
        return

    try:
        # We STILL need the Tenant Access Token to upload images to Feishu's cloud
        token = await get_feishu_tenant_access_token()
        if not token:
            logging.error("Feishu Webhook warning: App ID/Secret missing or invalid. Images will fail to upload.")

        content = text or ""
        for link_text, url in urls.items():
            content += f"\n{link_text}: {url}"
            
        # Feishu Webhooks use 'post' (rich text) to send text + images together
        post_content = []
        
        # 1. Add text part
        if content:
            # Feishu requires each line to be a separate array in the post structure
            for line in content.split('\n'):
                post_content.append([{"tag": "text", "text": line}])

        # 2. Upload and add photos
        for photo_url in photo_urls:
            if token:
                image_key = await upload_image_to_feishu(token, photo_url)
                if image_key:
                    post_content.append([{"tag": "img", "image_key": image_key}])

        # 3. Handle Videos (Webhooks CANNOT send video files)
        for i, video_url in enumerate(video_urls):
            post_content.append([{"tag": "text", "text": "🎬 [Video Received - View in Telegram/QQ/Discord]"}])
            
            # Extract the thumbnail and send it as a photo instead
            if token:
                res = await http_client.get(video_url)
                if res.status_code == 200:
                    video_bytes = res.content
                    thumbnail_bytes = await extract_thumbnail(video_bytes)
                    if thumbnail_bytes:
                        upload_img_url = "https://open.feishu.cn/open-apis/im/v1/images"
                        file_headers = {"Authorization": f"Bearer {token}"}
                        res_img = await http_client.post(
                            upload_img_url, headers=file_headers, 
                            data={"image_type": "message"}, 
                            files={"image": ("cover.jpg", thumbnail_bytes, "image/jpeg")}
                        )
                        image_key = res_img.json().get("data", {}).get("image_key")
                        if image_key:
                            post_content.append([{"tag": "img", "image_key": image_key}])

        if not post_content:
            return

        # Build the final Webhook Payload
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": "",
                        "content": post_content
                    }
                }
            }
        }
        
        # Dispatch the Webhook
        res = await http_client.post(FEISHU_WEBHOOK_URL, json=payload)
        if res.status_code != 200 or res.json().get("code") != 0:
            logging.error(f"Feishu Webhook failed: {res.text}")

    except Exception as e:
        logging.error(f"Feishu API error: {e}")


# --- Dispatcher ---

async def dispatch_message(text: str | None, photo_urls: list[str], video_urls: list[str], urls: dict[str, str]):
    tasks = [
        send_to_qq(text, photo_urls, video_urls, urls),
        send_to_discord(text, photo_urls, video_urls, urls),
        send_to_feishu(text, photo_urls, video_urls, urls)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# --- Telegram Utility Functions ---

async def get_url_dict_from_message_entities(text: str | None, entities: Sequence[MessageEntity] | None) -> dict[str, str]:
    if text is None or entities is None:
        return {}
    urls: dict[str, str] = {}
    for entity in entities:
        if entity.type == "text_link":
            assert entity.url is not None
            urls[text[entity.offset : entity.offset + entity.length]] = entity.url
    return urls

def get_button_urls(message: Message) -> dict[str, str]:
    """Extracts URLs from Telegram inline keyboard buttons."""
    urls: dict[str, str] = {}
    if message.reply_markup and hasattr(message.reply_markup, 'inline_keyboard'):
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.url:
                    # Adds a neat little link emoji to distinguish it as a button
                    urls[f"🔗 {button.text}"] = button.url
    return urls


async def get_msg_photo_url(message: Message) -> str | None:
    if message.photo:
        photo = message.photo[-1]
        file = await photo.get_file()
        assert file.file_path is not None
        return file.file_path
    return None


async def get_msg_video_url(message: Message) -> str | None:
    if message.video:
        # Telegram Bot API standard limit is 20MB for direct file downloads.
        if message.video.file_size and message.video.file_size > 20 * 1024 * 1024:
            logging.warning(f"Video size ({message.video.file_size} bytes) exceeds the 20MB Telegram Bot API download limit. Skipping.")
            return None
        file = await message.video.get_file()
        assert file.file_path is not None
        return file.file_path
    return None


# --- Telegram Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    await update.message.reply_text("Forwarder Bot is running.")


async def process_media_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    if media_group_id not in media_group_messages:
        return

    messages = media_group_messages.pop(media_group_id)
    logging.info(f"Processing media group {media_group_id} ({len(messages)} messages)")

    caption_message = next((msg for msg in messages if msg.caption), None)
    caption = caption_message.caption if caption_message else None
    caption_entities = caption_message.caption_entities if caption_message else None

    photo_urls: list[str] = []
    video_urls: list[str] = []
    
    for msg in messages:
        if msg.photo:
            photo_url = await get_msg_photo_url(msg)
            if photo_url:
                photo_urls.append(photo_url)
        elif msg.video:
            video_url = await get_msg_video_url(msg)
            if video_url:
                video_urls.append(video_url)

    text_urls = await get_url_dict_from_message_entities(caption, caption_entities)
    
    # NEW: Extract button URLs if the caption message has them
    if caption_message:
        text_urls.update(get_button_urls(caption_message))
        
    await dispatch_message(text=caption, photo_urls=photo_urls, video_urls=video_urls, urls=text_urls)


async def schedule_media_group_processing(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(MEDIA_GROUP_TIMEOUT)
    await process_media_group(media_group_id, context)


async def channel_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.channel_post is not None
    message = update.channel_post
    if message.chat.id not in channel_ids:
        return

    if message.media_group_id:
        if message.media_group_id not in media_group_messages:
            media_group_messages[message.media_group_id] = []
            asyncio.create_task(schedule_media_group_processing(message.media_group_id, context))
        media_group_messages[message.media_group_id].append(message)
        return

    if message.text:
        urls = await get_url_dict_from_message_entities(message.text, message.entities)
        
        # NEW: Merge button URLs
        urls.update(get_button_urls(message))
        
        await dispatch_message(text=message.text, photo_urls=[], video_urls=[], urls=urls)
        
    elif message.photo or message.video:
        photo_urls = []
        video_urls = []
        
        if message.photo:
            url = await get_msg_photo_url(message)
            if url: photo_urls.append(url)
        if message.video:
            url = await get_msg_video_url(message)
            if url: video_urls.append(url)
            
        text_urls = await get_url_dict_from_message_entities(message.caption, message.caption_entities)
        
        # NEW: Merge button URLs
        text_urls.update(get_button_urls(message))
        
        await dispatch_message(text=message.caption, photo_urls=photo_urls, video_urls=video_urls, urls=text_urls)


if __name__ == "__main__":
    app = ApplicationBuilder().token(bot_token).build()
    app.add_handlers([
        CommandHandler("start", start),
        MessageHandler(filters.ChatType.CHANNEL, channel_message_handler, block=True),
    ])
    logging.info("Starting polling...")
    app.run_polling()
