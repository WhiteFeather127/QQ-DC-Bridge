# QQ-DC-Bridge User Guide

## Overview

QQ-DC-Bridge is a cross-platform message bridge bot that provides bi-directional real-time message forwarding between **QQ groups** (via the OneBot/NapCat protocol) and **Discord channels** (via the Discord Bot API), with built-in automatic translation.

---

## 1. Supported Message Types

| Type | Description | Cross-Platform Behavior |
|---|---|---|
| **Text** | Plain text messages | Forwarded with automatic translation |
| **Image** | Single or multiple images | Bi-directional forwarding QQ ↔ Discord |
| **@Mention** | @group member / @specific user | Automatically converted to the target platform's @ format (requires account binding) |
| **@Everyone** | @everyone | Degraded to a text hint |
| **Reply/Quote** | Reply to a specific message | Quote relationship preserved, forwarded as reply format on the target platform |
| **Emoji** | QQ native emoji / Unicode Emoji | QQ emoji IDs automatically mapped to standard Unicode Emoji |
| **Discord Sticker** | Discord Sticker | Automatically converted to image for forwarding |
| **Unsupported types** | Unrecognized message segments | Degraded to a placeholder text hint |

> **Note**: When forwarding images, the QQ side relies on local file cache or CDN direct links. In some scenarios, NapCat may need proper image access configuration.

---

## 2. Account Binding (`/bind`)

Binding creates a one-to-one mapping between a QQ account and a Discord account, primarily used for:
- **Display name replacement**: Shows the nickname from the counterpart platform when forwarding messages
- **Cross-platform @ resolution**: Automatically converts @mentions to the format recognized by the target platform
- **Text @ recognition**: Writing `@nickname` in a message automatically resolves to a bound user's @ format

### Binding Process

Binding is done via Bot direct messages. **Currently only supported from the Discord side**:

1. Send `/bind <QQ_number>` to the Bot via Discord DM (e.g., `/bind 123456789`)
2. After verifying neither account is already bound, the Bot generates a 6-digit verification code
3. The verification code is sent to the target QQ user via **QQ DM**
4. The target QQ user replies to the Bot DM with the 6-digit code
5. The Bot verifies the code and the binding is complete

### Binding-Related Commands

| Command | Description | Available Platform |
|---|---|---|
| `/bind <QQ_number>` | Initiate account binding | Discord |
| `/unbind` | Unlink the current account binding | Discord / QQ |

### Notes

- Verification code expires in **5 minutes**
- Maximum **5 incorrect attempts**; after exceeding the limit, re-initiate the binding process
- QQ and Discord accounts are **one-to-one bound**; binding cannot proceed if either side is already bound

---

## 3. Trigger Behavior

### QQ Side: Must @Bot or Reply to Bot Message

Not all messages in the QQ group are forwarded. Only messages meeting one of the following criteria are bridged to Discord:

- **The message @mentions the Bot** (contains @Bot)
- **The message is a reply** to a message sent by the Bot

QQ group messages that do not meet the above conditions are ignored.

> **Reason**: QQ groups tend to have high message volume; forwarding everything would generate excessive noise. @Bot or replying to the Bot is a clear signal that the user intends to interact with the bridge.

### Discord Side: Automatic Trigger

**All messages** in the Discord channel are automatically forwarded to the QQ group (except messages sent by the Bot itself).

> **Reason**: The Discord channel is typically the initiating end of the bridge, and its message volume is generally more manageable, so full forwarding is applied.

### Reconnection Catch-up (Discord Only)

The Discord side supports message catch-up after reconnection:
- Automatically tracks the last processed message ID
- Fetches missed messages after reconnecting
- Catches up to a maximum of **30 minutes** of missed messages
- The QQ side does not support catch-up; messages during disconnection are lost

---

## 4. Automatic Translation

- Translation engine: **DeepSeek API** (OpenAI-compatible format)
- Translation direction:
  - QQ → Discord: Translated to **English**
  - Discord → QQ: Translated to **Chinese**
- Pure link/image messages **skip translation**
- Automatically skips when the translated text is identical to the original (avoids无效 translation)
- Falls back to **original text forwarding** on translation failure, without affecting message delivery

### Translation Control

Add `/distrans` at the beginning of a message to **skip translation** and send the original text:

```
/distrans This is a Chinese message, please do not translate
```

> **Note**: `/distrans` must be placed at the very beginning of the message and only affects the current message.

---

## 5. Command Summary

| Command | Description | Send Location | Available Platform |
|---|---|---|---|
| `/bind <QQ_number>` | Initiate account binding | Bot DM | Discord |
| `/unbind` | Unlink account binding | Bot DM | Discord / QQ |
| `<6-digit code>` | Complete binding step 2 | Bot DM | QQ |
| `/distrans <message>` | Skip translation, send original | Group/Channel | Discord / QQ |

---

## 6. Known Limitations

- **QQ side does not support edited message forwarding**: OneBot protocol has no edit event notification
- **QQ side does not support disconnection catch-up**: Messages during disconnection are lost
- **Binding only supported from Discord**: The QQ-side binding initiation is disabled
- **Image forwarding may be limited**: Depends on NapCat's image access configuration
- **Rate limiting**: QQ side defaults to 1 message/second (burst of 3); excess messages are dropped
