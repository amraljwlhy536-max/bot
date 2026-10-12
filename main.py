import asyncio
from contextlib import suppress
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from fastapi import FastAPI

from app.config import get_settings
from app.handlers.auth import create_auth_router
from app.handlers.required_channels import create_required_channels_router
from app.handlers.admin import create_admin_router
from app.handlers.user import create_user_router
from app.integrations.sam_topup.client import SamTopupClient
from app.repositories.required_channels_repository import RequiredChannelsRepository
from app.services.required_channel_service import RequiredChannelService
from app.services.points_service import PointsService
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.json_storage import JsonStorage


settings = get_settings()
bot = Bot(token=settings.bot_token)
dp = Dispatcher()
api = FastAPI()

storage = JsonStorage(Path(__file__).resolve().parent.parent / "data")
auth_service = AuthService(storage)
required_channels_repository = RequiredChannelsRepository(storage)
required_channel_service = RequiredChannelService(required_channels_repository)
points_service = PointsService(storage, settings.admin_ids)
sam_topup_client = SamTopupClient(settings.sam_api_base, settings.sam_api_key)
admin_service = AdminService(storage, settings.admin_ids, points_service, sam_topup_client)


async def required_channels_check(telegram_id: int):
    return await required_channel_service.check_all_required_channels(bot, telegram_id)


async def show_user_entry(target, telegram_id: int) -> None:
    from app.handlers.user import main_menu_keyboard

    user = auth_service.get_user_by_telegram_id(telegram_id)

    if user is None:
        await target.answer("يرجى استخدام /start لتسجيل الدخول.")
        return

    if user.get("is_banned") is True:
        await target.answer(
            "🚫 حسابك محظور حاليًا ولا يمكنك استخدام الخدمات."
        )
        return

    result = await required_channel_service.check_all_required_channels(
        bot, telegram_id
    )

    if not result.is_complete:
        from app.keyboards.required_channels import (
            build_required_channels_keyboard,
            format_required_channels,
        )

        channels = result.missing_channels + [
            item.channel for item in result.errors
        ]

        await target.answer(
            format_required_channels(channels),
            reply_markup=build_required_channels_keyboard(channels),
        )

        if result.errors:
            await target.answer(
                "تعذر التحقق من بعض القنوات حاليًا. أعد المحاولة بعد قليل."
            )
        return

    # جلب الرصيد الحالي
    try:
        balance = points_service.get_balance(user["id"])
    except Exception:
        balance = 0

    # حساب طلبات العميل من سجلات الشراء
    transactions = storage.read("balance_transactions.json")["transactions"]

    order_ids = {
        tx.get("order_id")
        for tx in transactions
        if tx.get("type") == "PURCHASE"
        and tx.get("user_id") == user.get("id")
        and tx.get("order_id")
    }

    orders = storage.read("orders.json")["orders"]

    orders_count = sum(
        1
        for order in orders
        if order.get("supplier_order_id") in order_ids
    )

    first_name = user.get("first_name") or "عميلنا العزيز"
    username = user.get("username") or "غير متاح"

    text = (
        "🚀 مرحبًا بك في NexTech!\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 أهلًا بك، {first_name}\n"
        f"🆔 معرف تيليجرام: {telegram_id}\n"
        f"👤 اسم المستخدم: {username}\n\n"
        f"💰 رصيدك الحالي: {balance} نقطة\n"
        f"📦 إجمالي طلباتك: {orders_count}\n\n"
        "🛍️ اكتشف منتجاتنا وخدماتنا الرقمية.\n"
        "⚡ جميع خدماتك في مكان واحد.\n\n"
        "👇 اختر الخدمة التي تريدها:"
    )

    await target.answer(
        text,
        reply_markup=main_menu_keyboard(),
    )

async def complete_required_channels(callback) -> None:
    await callback.message.edit_text("تم التحقق من الاشتراك.")
    await show_user_entry(callback.message, callback.from_user.id)

from app.services.purchase_service import PurchaseService

purchase_service = PurchaseService(storage, sam_topup_client, required_channels_check)

dp.include_router(create_auth_router(auth_service, on_authenticated=show_user_entry))
dp.include_router(create_user_router(storage, auth_service, points_service, purchase_service, required_channel_service, bot))
dp.include_router(create_required_channels_router(required_channel_service, bot, on_complete=complete_required_channels))
dp.include_router(create_admin_router(admin_service))


async def run_bot() -> None:
    await dp.start_polling(bot)


async def run_api() -> None:
    config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    try:
        await asyncio.gather(run_bot(), run_api())
    finally:
        await sam_topup_client.close()
        await bot.session.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
