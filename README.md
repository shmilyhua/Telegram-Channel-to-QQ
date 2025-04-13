# Telegram-Channel-to-QQ 项目文档

Telegram-Channel-to-QQ 是一个 Telegram 频道消息同步转发到 QQ 群的机器人，支持文本、链接和图片媒体消息的转发。

## 功能特点

* 转发 Telegram 频道文本消息到 QQ 群
* 支持转发带链接的消息
* 支持单张图片及图片组转发
* 自动合并媒体组（多张图片）消息

## 系统要求

* Python 3.13+
* uv 包管理工具
* 已正确配置 napcat 的正向 http 服务器连接

## 安装

### 1. 克隆项目仓库


```shell
git clone git@github.com:NahidaBuer/Telegram-Channel-to-QQ.git
```

### 2. 使用 uv 创建虚拟环境并安装依赖

```shell
uv venv --python 3.13
uv sync
```

## 配置

在项目根目录创建 .env 文件，并设置环境变量。

```shell
cp .env.example .env
```

### 获取 Telegram 频道 ID

如果你的 telegram 客户端不支持显示 channel id 的话，可以这样获取：

1. 将机器人添加到目标频道
2. 在频道中发送一条消息
3. 访问 `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` 查看 update 中的 chat.id

### 运行

```shell
uv run bot.py
```

## 开发指南

### 依赖说明

主要依赖包括：

* python-telegram-bot: Telegram Bot API 的 Python 封装
* httpx: 异步 HTTP 客户端（由 telegram 库引入）
* python-dotenv: 环境变量管理

### 媒体组处理流程

1. 接收带有 media_group_id 的消息
2. 将消息存储到临时字典中
3. 设置定时任务等待所有媒体消息到达
4. 超时后将所有图片和文字一次性发送到 QQ 群

## 问题排查

### 环境变量无法更新

如果更新 .env 文件后环境变量未生效：

1. 确保正确调用了 load_dotenv()
2. 重启程序以加载新的环境变量值
3. 检查 .env 文件格式是否正确
4. 验证文件路径是否正确
5. 检查系统环境变量是否覆盖了 .env 中的设置

### QQ 消息未正确发送

1. 确认 NAPCAT_HTTP_URL 设置正确
2. 检查 QQ 机器人服务是否正常运行
3. 验证 GROUP_ID 是否正确设置

## 更新日志

### v1.0.0

* 初始版本发布
* 支持文本和图片消息转发
* 支持媒体组消息处理

## 贡献

欢迎提交 Issues 或 Pull Requests 来改进本项目。

## 许可证

GPLv3, 但是不允许售卖或提供付费托管服务，除非取得所有者另行同意。
