# Phobos Bot — QQ & Discord 跨平台消息桥接

一个连接 QQ 群和 Discord 频道的双向消息桥接 Bot，支持自动翻译、跨平台 @用户、消息段保真转换。

## 功能特性

- **双向消息桥接**：QQ 群消息 ↔ Discord 频道消息实时互传
- **消息段保真转换**：图片、表情、@提及、回复引用等类型在跨平台时保留语义
- **自动翻译**：借助 DeepSeek API，非同一语言的消息自动翻译并追加译文
- **跨平台 @匹配**：QQ @某人 转换为 Discord @提及（基于昵称模糊匹配）
- **死循环防护**：自动过滤 Bot 自身消息，避免消息无限循环
- **速率控制**：针对 QQ 风控的令牌桶限速，可配置发送间隔
- **可观测性**：结构化 JSON 日志，支持按消息 ID 追踪全链路

## 架构概览

```
QQ 用户 ↔ NapCat (QQ 协议层) ↔ Phobos Bot ↔ Discord API ↔ Discord 用户
                 ↕                              ↕
              WebSocket                    WebSocket + REST
           (OneBot v11)                  (discord.py Gateway)
```

项目结构：

```
src/
├── main.py                  # 入口：启动适配器、注册事件
├── config.py                # 配置加载（YAML）
├── bridge/                  # 核心桥接逻辑
│   ├── orchestrator.py      # 消息编排器：编排接收→处理→发送全流程
│   ├── translator.py        # 翻译引擎（DeepSeek API）
│   ├── matcher.py           # 跨平台用户匹配器
│   ├── message_store.py     # 消息存储
│   └── segment/             # 消息段模型与转换器
│       ├── types.py         # 消息段类型定义
│       ├── converter.py     # 跨平台段转换器注册表
│       └── base.py          # 基类定义
├── adapters/                # 平台适配器
│   ├── discord/adapter.py   # Discord 消息收发
│   └── qq/adapter.py        # QQ（OneBot）消息收发
├── utils/                   # 工具
│   ├── logger.py            # 结构化日志
│   ├── ratelimit.py         # 令牌桶速率控制
│   └── emoji_map.py         # QQ face ID ↔ Unicode Emoji 映射
└── models/
    └── config_model.py      # typed config dataclasses
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 运行时 | Python 3.10+ |
| Discord | discord.py |
| QQ 协议 | NapCat (OneBot v11) + aiocqhttp / nonebot2 |
| 翻译 | DeepSeek API（httpx） |
| 配置 | PyYAML |

## 快速开始

### 前置条件

- Python 3.10+
- 一个 QQ 小号
- 一个 Discord Bot Token
- NapCat QQ 框架

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `data/config.yaml`：

```yaml
discord:
  token: "YOUR_DISCORD_BOT_TOKEN"
  channel_id: "YOUR_DISCORD_CHANNEL_ID"
  proxy: "http://127.0.0.1:7897"    # 可选

qq:
  bot_qq: 123456789                  # QQ 小号
  group_id: 630590659                # 目标 QQ 群号
  onebot_ws_url: "ws://127.0.0.1:8080"

deepseek:
  api_key: "sk-..."                  # DeepSeek API Key
  model: "deepseek-chat"

bridge:
  max_segments_per_msg: 20
  qq_rate_limit: 1.0                 # QQ 发送间隔（秒）
  translation_timeout: 10
```

### 3. 启动 NapCat

参考 [NapCat 官方文档](https://github.com/NapNeko/NapCatQQ) 部署并启动，确保 OneBot WebSocket 服务运行在 `ws://127.0.0.1:8080`。

### 4. 启动 Bot

```bash
python -m src.main
```

详细部署步骤见 [部署指南.md](./部署指南.md)。

## 配置说明

### 环境变量（可选，覆盖 config.yaml 中的敏感字段）

| 变量 | 说明 |
|------|------|
| `PHOBOS_DISCORD_TOKEN` | Discord Bot Token |
| `PHOBOS_DEEPSEEK_KEY` | DeepSeek API Key |

### 消息发送策略

| 方向 | 策略 |
|------|------|
| QQ → Discord | 先发原文，翻译完成后 edit 消息追加译文 |
| Discord → QQ | 等待翻译完成，原文 + 译文一次性发送 |

## 消息段转换支持

| QQ → Discord | Discord → QQ |
|-------------|-------------|
| 文字 → 文字 | 文字 → 文字 |
| 图片 → 图片 | 图片 → 图片 |
| 小表情 face → Emoji | @提及 → @某人 |
| @某人 → @提及 | 回复 → 回复引用 |
| 回复 → 文字引用 | 贴纸 → 图片 |

## 开发

```bash
# 运行测试
pytest

# 调试模式（输出到 stdout）
python -m src.main --debug
```

## License

MIT
