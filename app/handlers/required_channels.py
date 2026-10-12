from __future__ import annotations
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F, Router
from aiogram.types import CallbackQuery
from typing import Awaitable, Callable

from app.keyboards.required_channels import (
    build_required_channels_keyboard,
    format_required_channels,
)
from app.services.required_channel_service import RequiredChannelService


def create_required_channels_router(
    service: RequiredChannelService,
    bot,
    on_complete: Callable[[CallbackQuery], Awaitable[None]] | None = None,
) -> Router:
    router = Router(name="required_channels")

    @router.callback_query(F.data == "required_channels:check")
    async def check_subscription_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.message is None:
            await callback.answer()
            return

        result = await service.check_all_required_channels(bot, callback.from_user.id)
        if result.is_complete:
            if on_complete is not None:
                await on_complete(callback)
            else:
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer("تم التحقق من اشتراكك في جميع القنوات المطلوبة.")
        else:
            channels_to_show = result.missing_channels + [
                item.channel for item in result.errors
            ]
            await callback.message.edit_reply_markup(
                reply_markup=build_required_channels_keyboard(channels_to_show)
            )
            await callback.message.answer(format_required_channels(channels_to_show))
            if result.errors:
                await callback.message.answer(
                    "تعذر التحقق من بعض القنوات حاليًا. أعد المحاولة بعد قليل."
                )

        await callback.answer()

    return router


def _topup_payment_methods_keyboard():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🏦 تحويل بنكي",
        callback_data="user:topup:method:bank",
    )
    builder.button(
        text="📱 محفظة إلكترونية",
        callback_data="user:topup:method:wallet",
    )
    builder.button(
        text="💵 طريقة دفع أخرى",
        callback_data="user:topup:method:other",
    )
    builder.button(
        text="⬅️ القائمة الرئيسية",
        callback_data="user:home",
    )

    builder.adjust(1)
    return builder.as_markup()