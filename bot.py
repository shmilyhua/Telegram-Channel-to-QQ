import os
import re
import time
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
    Application,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Configuration & Validation ---
channel_ids_env = os.getenv("CHANNEL_IDS", "-100_00000_00000")
channel_ids = [int(cid.strip()) for cid in channel_ids_env.split(",") if cid.strip()]

bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise ValueError("BOT_TOKEN environment variable is required")

# QQ / Napcat Config
NAPCAT_URL = os.getenv("NAPCAT_HTTP_URL")
qq_group_id_env = os.getenv("QQ_GROUP_ID", "")
QQ_GROUP_ID = int(qq_group_id_env) if qq_group_id_env else 0

qq_id_env = os.getenv("QQ_ID", "")
QQ_ID = int(qq_id_env) if qq_id_env else 10000

QQ_NICKNAME = os.getenv("QQ_NICKNAME", "QQ")

if bool(NAPCAT_URL) != bool(QQ_GROUP_ID):
    logging.warning("Napcat configuration is incomplete. QQ forwarding will not function.")

# Discord Config
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

if bool(DISCORD_BOT_TOKEN) != bool(DISCORD_CHANNEL_ID):
    logging.warning("Discord configuration is incomplete. Discord forwarding will not function.")

# Feishu Config
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_RECEIVE_ID = os.getenv("FEISHU_RECEIVE_ID")
FEISHU_RECEIVE_ID_TYPE = os.getenv("FEISHU_RECEIVE_ID_TYPE", "chat_id")
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

if not all([FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_WEBHOOK_URL]):
    logging.warning("Feishu configuration is incomplete. Feishu forwarding will not function.")

# State
media_group_messages: dict[str, list[Message]] = {}
MEDIA_GROUP_TIMEOUT = 5

# --- Lifecycle Hooks ---

async def post_init(application: Application):
    application.bot_data["http_client"] = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
    )

async def post_stop(application: Application):
    http_client: httpx.AsyncClient | None = application.bot_data.get("http_client")
    if http_client:
        await http_client.aclose()


# --- Utility Functions ---

def escape_discord_markdown(text: str | None) -> str:
    if not text:
        return ""
    
    parts = re.split(r'(https?://\S+)', text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'([_*~`|])', r'\\\1', parts[i])
        
    return "".join(parts)


async def extract_thumbnail_from_url(video_url: str) -> bytes | None:
    fd_out, out_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd_out)
    
    try:
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', video_url,
            '-ss', '00:00:00.000', '-vframes', '1',
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if process.returncode == 0 and os.path.exists(out_path):
            with open(out_path, 'rb') as f:
                return f.read()
    except Exception:
        logging.exception("Thumbnail extraction error")
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)
        
    return None


# --- Platform Sender Implementations ---

async def send_to_qq(context: ContextTypes.DEFAULT_TYPE, text: str | None, photo_urls: list[str], video_urls: list[str], urls: list[tuple[str, str]]):
    if not NAPCAT_URL or not QQ_GROUP_ID:
        return

    http_client: httpx.AsyncClient = context.bot_data["http_client"]

    try:
        content_list: list[dict[str, Any]] = []
        
        for video_url in video_urls:
            content_list.append({"type": "video", "data": {"file": video_url}})
            
        for photo_url in photo_urls:
            content_list.append({"type": "image", "data": {"file": photo_url}})
            
        if text:
            content_list.append({"type": "text", "data": {"text": text}})
            
        for link_text, url in urls:
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
    except Exception:
        logging.exception("QQ Sender failed")


async def send_to_discord(context: ContextTypes.DEFAULT_TYPE, text: str | None, photo_urls: list[str], video_urls: list[str], urls: list[tuple[str, str]]):
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        return

    http_client: httpx.AsyncClient = context.bot_data["http_client"]
    api_url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    
    content = escape_discord_markdown(text)
    for link_text, url in urls:
        content += f"\n{escape_discord_markdown(link_text)}: {url}"
        
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

        files = []
        for i, video_url in enumerate(video_urls):
            thumbnail_bytes = await extract_thumbnail_from_url(video_url)
            if thumbnail_bytes:
                files.append((f"files[{i}]", (f"video_cover_{i}.jpg", thumbnail_bytes, "image/jpeg")))
                
        if video_urls:
            payload["content"] += "\n\n🎬 [Video Received - View in Telegram/QQ]"

        data = {"payload_json": json.dumps(payload)}

        if not files:
            headers["Content-Type"] = "application/json"
            response = await http_client.post(api_url, headers=headers, json=payload)
        else:
            response = await http_client.post(api_url, headers=headers, data=data, files=files)
            
        response.raise_for_status()

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 413:
            logging.error("Discord Payload Too Large (413).")
        else:
            logging.error(f"Discord API HTTP error: {e}")
    except Exception:
        logging.exception("Discord API error")


async def get_feishu_tenant_access_token(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    bot_data = context.bot_data
    current_time = time.time()
    
    cached_token = bot_data.get("feishu_token")
    token_expiry = bot_data.get("feishu_token_expiry", 0)
    
    # Return cached token if it is valid for at least another 5 minutes
    if cached_token and current_time < (token_expiry - 300):
        return cached_token

    http_client: httpx.AsyncClient = bot_data["http_client"]
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    
    try:
        response = await http_client.post(url, json=payload)
        response.raise_for_status()
        resp_json = response.json()
        
        token = resp_json.get("tenant_access_token")
        expire = resp_json.get("expire", 7200)
        
        if token:
            bot_data["feishu_token"] = token
            bot_data["feishu_token_expiry"] = current_time + expire
            
        return token
    except Exception:
        logging.exception("Failed to retrieve Feishu tenant access token")
        return None


async def upload_image_to_feishu(http_client: httpx.AsyncClient, token: str, photo_url: str) -> str | None:
    try:
        img_response = await http_client.get(photo_url)
        img_response.raise_for_status()
        
        upload_url = "https://open.feishu.cn/open-apis/im/v1/images"
        headers = {"Authorization": f"Bearer {token}"}
        data = {"image_type": "message"}
        files = {"image": ("image.jpg", img_response.content, "image/jpeg")}
        
        response = await http_client.post(upload_url, headers=headers, data=data, files=files)
        response.raise_for_status()
        
        resp_json = response.json()
        if resp_json.get("code") == 0:
            return resp_json.get("data", {}).get("image_key")
        else:
            logging.error(f"Feishu image upload error: {resp_json}")
            return None
    except Exception:
        logging.exception("Failed to upload image to Feishu")
        return None


async def send_to_feishu(context: ContextTypes.DEFAULT_TYPE, text: str | None, photo_urls: list[str], video_urls: list[str], urls: list[tuple[str, str]]):
    if not FEISHU_WEBHOOK_URL:
        return

    http_client: httpx.AsyncClient = context.bot_data["http_client"]

    try:
        token = await get_feishu_tenant_access_token(context)
        if not token:
            logging.error("Feishu Webhook warning: App ID/Secret missing or invalid. Images will fail to upload.")

        content = text or ""
        for link_text, url in urls:
            content += f"\n{link_text}: {url}"
            
        post_content = []
        
        if content:
            for line in content.split('\n'):
                post_content.append([{"tag": "text", "text": line}])

        for photo_url in photo_urls:
            if token:
                image_key = await upload_image_to_feishu(http_client, token, photo_url)
                if image_key:
                    post_content.append([{"tag": "img", "image_key": image_key}])

        for video_url in video_urls:
            post_content.append([{"tag": "text", "text": "🎬 [Video Received - View in Telegram/QQ/Discord]"}])
            
            if token:
                thumbnail_bytes = await extract_thumbnail_from_url(video_url)
                if thumbnail_bytes:
                    upload_img_url = "https://open.feishu.cn/open-apis/im/v1/images"
                    file_headers = {"Authorization": f"Bearer {token}"}
                    res_img = await http_client.post(
                        upload_img_url, headers=file_headers, 
                        data={"image_type": "message"}, 
                        files={"image": ("cover.jpg", thumbnail_bytes, "image/jpeg")}
                    )
                    res_img.raise_for_status()
                    image_key = res_img.json().get("data", {}).get("image_key")
                    if image_key:
                        post_content.append([{"tag": "img", "image_key": image_key}])

        if not post_content:
            return

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
        
        res = await http_client.post(FEISHU_WEBHOOK_URL, json=payload)
        res.raise_for_status()
        if res.json().get("code") != 0:
            logging.error(f"Feishu Webhook failed: {res.text}")

    except Exception:
        logging.exception("Feishu API error")


# --- Dispatcher ---

async def dispatch_message(context: ContextTypes.DEFAULT_TYPE, text: str | None, photo_urls: list[str], video_urls: list[str], urls: list[tuple[str, str]]):
    tasks = [
        send_to_qq(context, text, photo_urls, video_urls, urls),
        send_to_discord(context, text, photo_urls, video_urls, urls),
        send_to_feishu(context, text, photo_urls, video_urls, urls)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for res in results:
        if isinstance(res, Exception):
            logging.error(f"Dispatch task failed: {res}", exc_info=res)


# --- Telegram Utility Functions ---

def get_url_dict_from_message(message: Message, is_caption: bool = False) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    entities_dict = message.parse_caption_entities([MessageEntity.TEXT_LINK]) if is_caption else message.parse_entities([MessageEntity.TEXT_LINK])
    
    for entity, text in entities_dict.items():
        if entity.url:
            urls.append((text, entity.url))
    return urls


def get_button_urls(message: Message) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    if message.reply_markup and hasattr(message.reply_markup, 'inline_keyboard'):
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.url:
                    urls.append((f"🔗 {button.text}", button.url))
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


async def process_media_group_messages(messages: list[Message], context: ContextTypes.DEFAULT_TYPE):
    if not messages:
        return
        
    logging.info(f"Processing media group ({len(messages)} messages)")

    caption_message = next((msg for msg in messages if msg.caption), None)
    caption = caption_message.caption if caption_message else None

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

    text_urls: list[tuple[str, str]] = []
    if caption_message:
        text_urls.extend(get_url_dict_from_message(caption_message, is_caption=True))
        text_urls.extend(get_button_urls(caption_message))
        
    await dispatch_message(context=context, text=caption, photo_urls=photo_urls, video_urls=video_urls, urls=text_urls)


async def schedule_media_group_processing(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(MEDIA_GROUP_TIMEOUT)
    messages = media_group_messages.pop(media_group_id, None)
    if messages:
        await process_media_group_messages(messages, context)


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
        urls = get_url_dict_from_message(message, is_caption=False)
        urls.extend(get_button_urls(message))
        await dispatch_message(context=context, text=message.text, photo_urls=[], video_urls=[], urls=urls)
        
    elif message.photo or message.video:
        photo_urls = []
        video_urls = []
        
        if message.photo:
            url = await get_msg_photo_url(message)
            if url: photo_urls.append(url)
        if message.video:
            url = await get_msg_video_url(message)
            if url: video_urls.append(url)
            
        text_urls = get_url_dict_from_message(message, is_caption=True)
        text_urls.extend(get_button_urls(message))
        
        await dispatch_message(context=context, text=message.caption, photo_urls=photo_urls, video_urls=video_urls, urls=text_urls)


if __name__ == "__main__":
    app = ApplicationBuilder().token(bot_token).post_init(post_init).post_stop(post_stop).build()
    
    app.add_handlers([
        CommandHandler("start", start),
        MessageHandler(filters.ChatType.CHANNEL, channel_message_handler, block=True),
    ])
    logging.info("Starting polling...")
    app.run_polling()
