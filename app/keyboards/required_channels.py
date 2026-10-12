from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_required_channels_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for channel in channels:
        username = channel.get("username")
        title = channel.get("title") or "القناة المطلوبة"
        if username:
            username = str(username).lstrip("@")
            builder.row(
                InlineKeyboardButton(
                    text=f"الاشتراك في {title}",
                    url=f"https://t.me/{username}",
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="تحقق من الاشتراك",
            callback_data="required_channels:check",
        )
    )
    return builder.as_markup()


def format_required_channels(channels: list[dict]) -> str:
    lines = ["يجب الاشتراك في القنوات التالية:"]
    for channel in channels:
        title = channel.get("title") or "القناة المطلوبة"
        username = channel.get("username")
        if username:
            lines.append(f"• {title} (@{str(username).lstrip('@')})")
        else:
            # No invite link is generated for channels without a username.
            lines.append(f"• {title} (chat_id: {channel.get('chat_id')})")
    return "\n".join(lines)
