from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from typing import Awaitable, Callable

from app.services.auth_service import AuthService, EmailAlreadyUsedError, InvalidEmailError


class LoginStates(StatesGroup):
    waiting_for_email = State()


def create_auth_router(
    auth_service: AuthService,
    on_authenticated: Callable[[Message, int], Awaitable[None]] | None = None,
) -> Router:
    router = Router(name="auth")

    @router.message(CommandStart())
    async def start_handler(message: Message, state: FSMContext) -> None:
        telegram_id = message.from_user.id
        user = auth_service.get_user_by_telegram_id(telegram_id)

        if user is not None:
            await state.clear()
            if on_authenticated is not None:
                await on_authenticated(message, telegram_id)
            else:
                await message.answer("تم تسجيل الدخول باستخدام حسابك الحالي.")
            return

        await state.set_state(LoginStates.waiting_for_email)
        await message.answer("مرحبًا. أرسل بريدك الإلكتروني للمتابعة.")

    @router.message(LoginStates.waiting_for_email)
    async def email_handler(message: Message, state: FSMContext) -> None:
        telegram_id = message.from_user.id
        email = message.text or ""

        # Re-check Telegram ID before registration so an already-created account
        # is never overwritten or duplicated.
        existing_user = auth_service.get_user_by_telegram_id(telegram_id)
        if existing_user is not None:
            await state.clear()
            await message.answer("تم تسجيل الدخول باستخدام حسابك الحالي.")
            return

        try:
            auth_service.register_with_email(telegram_id, email)
        except InvalidEmailError:
            await message.answer("البريد الإلكتروني غير صحيح. أرسله مرة أخرى.")
            return
        except EmailAlreadyUsedError:
            await state.clear()
            await message.answer("هذا البريد الإلكتروني مرتبط بحساب Telegram آخر.")
            return

        await state.clear()
        if on_authenticated is not None:
            await on_authenticated(message, telegram_id)
        else:
            await message.answer("تم إنشاء حسابك وتسجيل الدخول بنجاح.")

    return router
