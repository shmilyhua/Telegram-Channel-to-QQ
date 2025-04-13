from typing import Any, Sequence
from telegram import Message, MessageEntity, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    CommandHandler,
    ContextTypes,
)
import logging
import httpx
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Bot 配置
channel_ids = [
    int(channel_id.strip())
    for channel_id in os.getenv("CHANNEL_IDS", "-100_00000_00000").split(",")
]
bot_token = os.getenv("BOT_TOKEN")
if not bot_token:
    raise ValueError("BOT_TOKEN environment variable is required")

# Napcat 配置
NAPCAT_URL = os.getenv("NAPCAT_HTTP_URL")
QQ_GROUP_ID = os.getenv("QQ_GROUP_ID")
logging.info(f"\n\nQQ_GROUP_ID: {QQ_GROUP_ID}\n\n")
if not QQ_GROUP_ID:
    raise ValueError("QQ_GROUP_ID environment variable is required")
QQ_ID = os.getenv("QQ_ID", 10000)
QQ_NICKNAME = os.getenv("QQ_NICKNAME", "QQ")


# 存储媒体组消息的字典
media_group_messages: dict[str, list[Message]] = {}
# 设置媒体组超时时间（秒）
MEDIA_GROUP_TIMEOUT = 5

app = ApplicationBuilder().token(bot_token).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    await update.message.reply_text("Hello!")


async def process_media_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    """处理收集到的媒体组消息"""
    if media_group_id not in media_group_messages:
        return

    messages = media_group_messages.pop(media_group_id)
    logging.info(f"处理媒体组 {media_group_id}，共 {len(messages)} 条消息")

    # 找出带有 caption 的消息
    caption_message = next((msg for msg in messages if msg.caption), None)
    caption = caption_message.caption if caption_message else None
    caption_entities = caption_message.caption_entities if caption_message else None

    # 收集所有图片 URL
    photo_urls: list[str] = []
    for msg in messages:
        if msg.photo:
            photo_url = await get_msg_photo_url(msg)
            assert photo_url is not None
            photo_urls.append(photo_url)

    text_urls = await get_url_dict_from_message_entities(caption, caption_entities)
    await qq_send_media_group_in_group(photo_urls, caption, text_urls)


async def channel_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot 主要功能入口，处理频道消息，按消息类型 call 不同的函数"""
    assert update.channel_post is not None
    message = update.channel_post
    if message.chat.id not in channel_ids:
        logging.info(
            f"Received update from channel {message.chat.id}, but not in {channel_ids}"
        )
        return
    logging.info(f"Received update: {message}")

    # 处理媒体组消息
    if message.media_group_id:
        if message.media_group_id not in media_group_messages:
            media_group_messages[message.media_group_id] = []

            # 创建一个定时任务，等待一段时间后处理媒体组
            asyncio.create_task(
                schedule_media_group_processing(message.media_group_id, context)
            )

        media_group_messages[message.media_group_id].append(message)
        return

    # 处理普通消息
    if message.text:
        urls = await get_url_dict_from_message_entities(message.text, message.entities)
        await qq_send_text_in_group(message.text, urls)
    elif message.photo:
        photo_url = await get_msg_photo_url(message)
        text_urls = await get_url_dict_from_message_entities(
            message.caption, message.caption_entities
        )
        await qq_send_media_group_in_group(
            [photo_url] if photo_url else [], message.caption or None, text_urls
        )
    else:
        logging.info("\n\nReceived update with other content!\n\n")


async def schedule_media_group_processing(
    media_group_id: str, context: ContextTypes.DEFAULT_TYPE
):
    """设置定时任务处理媒体组"""
    await asyncio.sleep(MEDIA_GROUP_TIMEOUT)
    await process_media_group(media_group_id, context)


async def get_url_dict_from_message_entities(
    text: str | None, entities: Sequence[MessageEntity] | None
) -> dict[str, str]:
    """从消息实体中提取 URL 字典"""
    if text is None or entities is None:
        return {}
    urls: dict[str, str] = {}
    for entity in entities:
        if entity.type == "text_link":
            assert entity.url is not None
            urls[text[entity.offset : entity.offset + entity.length]] = entity.url
    return urls


async def get_msg_photo_url(message: Message) -> str | None:
    """获取消息中的图片 URL"""
    if message.photo:
        photo = message.photo[-1]
        logging.info(f"Downloading photo: {photo}")
        file = await photo.get_file()
        assert file.file_path is not None
        logging.info(f"\n\nLink to photo: {file.file_path}\n\n")
        return file.file_path
    return None


async def qq_send_text_in_group(text: str, urls: dict[str, str]):
    """发送文本消息到群组"""
    json_data = {}
    if len(text) <= 200 and not urls:
        post_url = f"{NAPCAT_URL}/send_group_msg"
        json_data = {
            "group_id": QQ_GROUP_ID,
            "message": [{"type": "text", "data": {"text": text}}],
        }
    else:
        content_list = [{"type": "text", "data": {"text": text}}]
        for text, url in urls.items():
            content_list.append({"type": "text", "data": {"text": text + "\n" + url}})
        post_url = f"{NAPCAT_URL}/send_group_forward_msg"
        messages_list: list[Any] = [
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
        json_data = {
            "group_id": QQ_GROUP_ID,
            "messages": messages_list,
        }
        logging.info(f"\n\nSending long text as forward msg: {json_data}\n\n")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            post_url,
            json=json_data,
        )
        logging.info(f"\n\nResponse: {response.text}\n\n")


async def qq_send_media_group_in_group(
    photos: Sequence[str], caption: str | None, urls: dict[str, str]
):
    """发送媒体组消息到群组"""
    if len(photos) == 1 and caption is None:
        return await qq_send_photo_in_group(photos[0])

    content_list = [{"type": "image", "data": {"file": photo}} for photo in photos]
    if caption:
        content_list.append({"type": "text", "data": {"text": caption}})
    for text, url in urls.items():
        content_list.append({"type": "text", "data": {"text": text + "\n" + url}})

    messages_list: list[Any] = [
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
    json_data = {
        "group_id": QQ_GROUP_ID,
        "messages": messages_list,
    }
    logging.info(f"\n\nSending group forward msg: {json_data}\n\n")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{NAPCAT_URL}/send_group_forward_msg",
            json=json_data,
        )


async def qq_send_photo_in_group(photo: str):
    """发送单张图片到群组"""
    logging.info(f"\n\nSending photo: {photo}\n\n")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{NAPCAT_URL}/send_group_msg",
            json={
                "group_id": QQ_GROUP_ID,
                "message": [{"type": "image", "data": {"file": photo}}],
            },
        )


app.add_handlers(
    [
        CommandHandler("start", start),
        MessageHandler(filters.ChatType.CHANNEL, channel_message_handler, block=True),
    ]
)

app.run_polling()
