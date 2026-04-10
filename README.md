[![Fork](https://img.shields.io/badge/Forked_From-Original_Project-lightgrey.svg)](https://github.com/NahidaBuer/Telegram-Channel-to-QQ)
[![Modified by AI](https://img.shields.io/badge/Modified_By-Gemini-blue.svg)](https://gemini.google.com)
> **Note:** This repository is a modified fork of [Telegram-Channel-to-QQ](https://github.com/NahidaBuer/Telegram-Channel-to-QQ). The modifications, refactoring, and new features introduced in this fork were primarily implemented using Gemini.
# Telegram-Multi-Platform-Forwarder

一个基于 Python 的异步 Telegram 频道消息同步转发机器人。支持将 Telegram 频道的文本、链接、图片和视频自动同步至 QQ 群（基于 Napcat）、Discord 和飞书。

## 功能特点

* **多平台同步转发**：支持并发推送到 QQ 群、Discord 频道和飞书（Webhook）。
* **全媒体类型支持**：支持转发纯文本、带链接的文本、单张图片/视频以及媒体组（相册）。
* **自动合并媒体组**：原生支持 Telegram 的 Media Group，自动缓存并合并同一相册内的图片和视频后统一发送。
* **内联按钮链接提取**：自动抓取 Telegram 消息下方 Inline Keyboard（内联键盘）的按钮链接，并在正文后追加。
* **智能视频处理**：
  * **Discord 适配**：针对 Discord 严格的 8MB 限制，自动调用 FFmpeg 进行动态码率压缩；若压缩后仍超限，则优雅降级仅发送图文。
  * **飞书适配**：由于飞书 Webhook 不支持直接上传视频，系统会自动调用 FFmpeg 提取视频首帧作为封面图发送，并附带视频接收提示。

## 系统要求

* Python 3.13 或更高版本
* [uv](https://github.com/astral-sh/uv) 现代 Python 包管理工具
* **FFmpeg**：**必须全局安装**，用于处理视频压缩和封面提取。
* 已正确配置并运行的 Napcat 正向 HTTP 服务器（用于 QQ 转发）。

## 安装

### 1. 克隆项目仓库

```shell
git clone https://github.com/shmilyhua/Telegram-Channel-to-QQ.git
cd Telegram-Channel-to-QQ
```

### 2. 使用 uv 创建虚拟环境并安装依赖

```shell
uv venv --python 3.13
uv sync
```

## 配置

在项目根目录创建 `.env` 文件，并设置相关环境变量。

```shell
cp .env.example .env
```

### 核心环境变量说明

| 变量名 | 说明 |
| :--- | :--- |
| `BOT_TOKEN` | Telegram Bot Token |
| `CHANNEL_IDS` | 需要监听的 Telegram 频道 ID 列表（逗号分隔） |
| `NAPCAT_HTTP_URL` | Napcat 正向 HTTP 服务器地址 |
| `QQ_GROUP_ID` | 目标转发 QQ 群号 |
| `DISCORD_BOT_TOKEN` | Discord Bot 凭证 |
| `DISCORD_CHANNEL_ID` | 目标 Discord 频道 ID |
| `FEISHU_APP_ID` / `SECRET` | 飞书自建应用的凭证（用于获取 Token 上传图片） |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人的 Webhook 地址 |

*获取 Telegram 频道 ID 提示：若客户端不显示，可将 Bot 加入频道并发送一条消息，访问 `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` 查看 `chat.id`。*

## 运行

确保已完成上述配置，并且系统环境已安装 FFmpeg，然后执行：

```shell
uv run bot.py
```

## 问题排查

* **视频处理失败/不发送**：检查服务器是否正确安装了 `ffmpeg` 并配置在了系统环境变量 (PATH) 中。
* **飞书图片未显示**：飞书 Webhook 需要通过 Tenant Access Token 上传图片。请确保 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 正确无误。
* **环境变量未生效**：更新 `.env` 文件后，必须重启机器人进程（`load_dotenv()` 仅在启动时加载）。
* **Discord Payload Too Large**：若源视频体积过大，即使经过 FFmpeg 压缩也可能超过 8MB。此时程序会自动降级为发送文本与图片并忽略视频。

## 许可证

本项目使用 **GPLv3** 协议，并附加 **Commons Clause** 限制：不允许出售本软件，也不允许将其作为付费托管服务提供，除非取得版权所有者明示同意。
