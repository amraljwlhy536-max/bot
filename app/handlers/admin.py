from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.admin_service import AdminAuthorizationError, AdminService, AdminValidationError
from app.services.points_service import PointsError
from app.integrations.sam_topup.errors import SamTopupError


logger = logging.getLogger(__name__)


class AdminStates(StatesGroup):
    search_user = State()
    points = State()
    ban_user = State()
    search_order = State()
    sale_price = State()
    add_channel = State()


def _admin_keyboard():
    b = InlineKeyboardBuilder()
    for text, data in [
        ("Users", "admin:users"), ("Products", "admin:products"),
        ("Channels", "admin:channels"), ("Orders", "admin:orders"),
        ("Supplier", "admin:supplier"), ("Statistics", "admin:statistics"),
    ]:
        b.button(text=text, callback_data=data)
    b.adjust(2)
    return b.as_markup()


def _back():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Admin", callback_data="admin:home")
    return b.as_markup()


def _user_actions(user_id: str):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add points", callback_data=f"admin:user:credit:{user_id}")
    b.button(text="➖ Debit points", callback_data=f"admin:user:debit:{user_id}")
    b.button(text="Ban", callback_data=f"admin:user:ban:{user_id}")
    b.button(text="Unban", callback_data=f"admin:user:unban:{user_id}")
    b.button(text="⬅️ Users", callback_data="admin:users")
    b.adjust(2, 2, 1)
    return b.as_markup()


def create_admin_router(service: AdminService) -> Router:
    router = Router(name="admin")

    async def authorized(event) -> bool:
        user = getattr(event, "from_user", None)
        if user is None or int(user.id) not in service.admin_ids:
            if isinstance(event, CallbackQuery):
                await event.answer("غير مصرح لك.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("غير مصرح لك.")
            return False
        return True

    @router.message(F.text == "/admin")
    async def admin_command(message: Message, state: FSMContext):
        if not await authorized(message):
            return
        await state.clear()
        await message.answer("لوحة الأدمن", reply_markup=_admin_keyboard())

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_callback(callback: CallbackQuery, state: FSMContext):
        if not await authorized(callback):
            return
        parts = (callback.data or "").split(":")
        answered = False
        try:
            if parts == ["admin", "home"]:
                await state.clear()
                await callback.message.edit_text("لوحة الأدمن", reply_markup=_admin_keyboard())
            elif parts == ["admin", "users"]:
                b = InlineKeyboardBuilder()
                b.button(text="🔎 Search user", callback_data="admin:user_search")
                await callback.message.edit_text("Users", reply_markup=b.as_markup())
            elif parts == ["admin", "user_search"]:
                await state.set_state(AdminStates.search_user)
                await callback.message.answer("أرسل ID المستخدم أو Telegram ID أو البريد أو username للبحث.")
            elif parts[1:3] == ["user", "view"]:
                user = service.get_user(callback.from_user.id, parts[3])
                if user is None:
                    raise AdminValidationError("User not found")
                await callback.message.answer(_user_line(user), reply_markup=_user_actions(parts[3]))
            elif parts[1:3] == ["user", "credit"]:
                await state.update_data(points_action="credit", target_user_id=parts[3])
                await state.set_state(AdminStates.points)
                await callback.message.answer("أرسل: amount | reason (reason اختياري).")
            elif parts[1:3] == ["user", "debit"]:
                await state.update_data(points_action="debit", target_user_id=parts[3])
                await state.set_state(AdminStates.points)
                await callback.message.answer("أرسل: amount | reason (reason اختياري).")
            elif parts[1:3] == ["user", "ban"]:
                service.ban_user(callback.from_user.id, parts[3])
                await callback.answer("تم الحظر")
                answered = True
                await callback.message.answer("تم حظر المستخدم.", reply_markup=_back())
            elif parts[1:3] == ["user", "unban"]:
                service.unban_user(callback.from_user.id, parts[3])
                await callback.answer("تم إلغاء الحظر")
                answered = True
                await callback.message.answer("تم إلغاء حظر المستخدم.", reply_markup=_back())
            elif parts == ["admin", "products"]:
                products = service.list_products(callback.from_user.id)
                text = "Products\n" + ("\n".join(_product_line(p) for p in products) if products else "لا توجد منتجات.")
                b = InlineKeyboardBuilder()
                b.button(text="🔄 Update from Sam Topup", callback_data="admin:products:sync")
                for p in products:
                    b.button(text=f"Enable/Disable {p.get('name', p.get('id'))}", callback_data=f"admin:product:toggle:{p.get('id')}")
                    b.button(text=f"Set price {p.get('name', p.get('id'))}", callback_data=f"admin:product:price:{p.get('id')}")
                b.button(text="⬅️ Admin", callback_data="admin:home")
                b.adjust(1, 2)
                await callback.message.edit_text(text, reply_markup=b.as_markup())
            elif parts == ["admin", "products", "sync"]:
                products = await service.sync_products(callback.from_user.id)
                await callback.answer("تم تحديث المنتجات")
                answered = True
                await callback.message.answer(f"تم تحديث المنتجات. العدد: {len(products)}", reply_markup=_back())
            elif parts[1:3] == ["product", "toggle"]:
                product = next((p for p in service.list_products(callback.from_user.id) if p.get("id") == parts[3]), None)
                if product is None:
                    raise AdminValidationError("Product not found")
                updated = service.set_product_enabled(callback.from_user.id, parts[3], product.get("enabled") is not True)
                await callback.answer("تم تحديث حالة المنتج")
                answered = True
                await callback.message.answer(f"{updated.get('name')} enabled={updated.get('enabled')}", reply_markup=_back())
            elif parts[1:3] == ["product", "price"]:
                await state.update_data(target_product_id=parts[3])
                await state.set_state(AdminStates.sale_price)
                await callback.message.answer("أرسل sale_price_points كرقم موجب.")
            elif parts == ["admin", "channels"]:
                channels = service.list_channels(callback.from_user.id)
                text = "Channels\n" + ("\n".join(_channel_line(c) for c in channels) if channels else "لا توجد قنوات.")
                b = InlineKeyboardBuilder()
                b.button(text="➕ Add channel", callback_data="admin:channel:add")
                for c in channels:
                    b.button(text=f"Activate/Deactivate {c.get('title')}", callback_data=f"admin:channel:toggle:{c.get('id')}")
                b.button(text="⬅️ Admin", callback_data="admin:home")
                b.adjust(1)
                await callback.message.edit_text(text, reply_markup=b.as_markup())
            elif parts == ["admin", "channel", "add"]:
                await state.set_state(AdminStates.add_channel)
                await callback.message.answer("أرسل: chat_id | username (أو -) | title | is_required (true/false).")
            elif parts[1:3] == ["channel", "toggle"]:
                channels = service.list_channels(callback.from_user.id)
                channel = next((c for c in channels if c.get("id") == parts[3]), None)
                if channel is None:
                    raise AdminValidationError("Channel not found")
                updated = service.set_channel_active(callback.from_user.id, parts[3], channel.get("is_active") is not True)
                await callback.answer("تم تحديث حالة القناة")
                answered = True
                await callback.message.answer(f"{updated.get('title')} active={updated.get('is_active')}", reply_markup=_back())
            elif parts == ["admin", "orders"]:
                orders = service.list_orders(callback.from_user.id)
                text = "Orders\n" + ("\n\n".join(_order_line(o) for o in orders) if orders else "لا توجد طلبات.")
                b = InlineKeyboardBuilder()
                b.button(text="🔎 Search order", callback_data="admin:order_search")
                b.button(text="⬅️ Admin", callback_data="admin:home")
                await callback.message.edit_text(text, reply_markup=b.as_markup())
            elif parts == ["admin", "order_search"]:
                await state.set_state(AdminStates.search_order)
                await callback.message.answer("أرسل supplier_order_id أو product_id أو product_name أو status.")
            elif parts == ["admin", "supplier"]:
                balance = await service.supplier_balance(callback.from_user.id)
                await callback.message.edit_text(f"Supplier Balance: {balance.get('balance')}", reply_markup=_back())
            elif parts == ["admin", "statistics"]:
                stats = service.statistics(callback.from_user.id)
                await callback.message.edit_text(
                    f"Users: {stats['users_count']}\nOrders: {stats['orders_count']}\nPurchases: {stats['purchase_count']}\nTotal points used: {stats['total_points_used']}",
                    reply_markup=_back(),
                )
            else:
                await callback.answer("عملية غير معروفة.", show_alert=True)
                answered = True
        except (AdminAuthorizationError, AdminValidationError, PointsError, SamTopupError) as exc:
            await callback.answer(str(exc), show_alert=True)
            answered = True
        except (IndexError, ValueError):
            await callback.answer("بيانات العملية غير صحيحة.", show_alert=True)
            answered = True
        except Exception:
            logger.exception(
                "Unexpected admin callback failure actor_id=%s action=%s",
                callback.from_user.id,
                (callback.data or "")[:100],
            )
            await callback.answer("تعذر تنفيذ العملية حاليًا. حاول مرة أخرى لاحقًا.", show_alert=True)
            answered = True
        finally:
            if not answered:
                await callback.answer()

    @router.message(AdminStates.search_user)
    async def search_user(message: Message, state: FSMContext):
        if not await authorized(message): return
        results = service.search_users(message.from_user.id, message.text or "")
        await state.clear()
        if not results:
            await message.answer("لم يتم العثور على مستخدم.", reply_markup=_back())
            return
        for user in results[:20]:
            b = InlineKeyboardBuilder()
            b.button(text="عرض / إدارة المستخدم", callback_data=f"admin:user:view:{user.get('id')}")
            await message.answer(_user_line(user), reply_markup=b.as_markup())

    @router.message(AdminStates.points)
    async def points_action(message: Message, state: FSMContext):
        if not await authorized(message): return
        data = await state.get_data()
        parts = [x.strip() for x in (message.text or "").split("|", 1)]
        try:
            amount = int(parts[0])
            reason = parts[1] if len(parts) == 2 and parts[1] else None
            if data.get("points_action") == "credit":
                tx = service.credit_points(message.from_user.id, data["target_user_id"], amount, reason)
            else:
                tx = service.debit_points(message.from_user.id, data["target_user_id"], amount, reason)
            await state.clear()
            await message.answer(f"تمت العملية. balance_after={tx['balance_after']}", reply_markup=_back())
        except (ValueError, PointsError, AdminAuthorizationError) as exc:
            await message.answer(str(exc))
        except Exception:
            logger.exception(
                "Unexpected admin points action failure actor_id=%s",
                message.from_user.id,
            )
            await message.answer("تعذر تنفيذ العملية حاليًا. حاول مرة أخرى لاحقًا.")

    @router.message(AdminStates.sale_price)
    async def sale_price(message: Message, state: FSMContext):
        if not await authorized(message): return
        data = await state.get_data()
        try:
            updated = service.set_sale_price_points(message.from_user.id, data["target_product_id"], int((message.text or "").strip()))
            await state.clear()
            await message.answer(f"تم تحديث السعر: {updated.get('sale_price_points')}", reply_markup=_back())
        except (ValueError, AdminValidationError, AdminAuthorizationError) as exc:
            await message.answer(str(exc))
        except Exception:
            logger.exception(
                "Unexpected admin product price failure actor_id=%s",
                message.from_user.id,
            )
            await message.answer("تعذر تنفيذ العملية حاليًا. حاول مرة أخرى لاحقًا.")

    @router.message(AdminStates.add_channel)
    async def add_channel(message: Message, state: FSMContext):
        if not await authorized(message): return
        parts = [x.strip() for x in (message.text or "").split("|")]
        try:
            if len(parts) != 4:
                raise AdminValidationError("Expected 4 values")
            is_required = parts[3].casefold()
            if is_required not in {"true", "false"}:
                raise AdminValidationError("is_required must be true or false")
            channel = service.add_channel(message.from_user.id, parts[0], None if parts[1] == "-" else parts[1], parts[2], is_required == "true")
            await state.clear()
            await message.answer(f"تمت إضافة القناة: {channel['title']}", reply_markup=_back())
        except (AdminValidationError, AdminAuthorizationError) as exc:
            await message.answer(str(exc))
        except Exception:
            logger.exception(
                "Unexpected admin channel action failure actor_id=%s",
                message.from_user.id,
            )
            await message.answer("تعذر تنفيذ العملية حاليًا. حاول مرة أخرى لاحقًا.")

    @router.message(AdminStates.search_order)
    async def search_order(message: Message, state: FSMContext):
        if not await authorized(message): return
        try:
            results = service.search_orders(message.from_user.id, message.text or "")
            await state.clear()
            if not results:
                await message.answer("لم يتم العثور على Order.", reply_markup=_back())
                return
            for order in results[:20]:
                await message.answer(_order_detail(order), reply_markup=_back())
        except Exception:
            logger.exception(
                "Unexpected admin order search failure actor_id=%s",
                message.from_user.id,
            )
            await message.answer("تعذر تنفيذ البحث حاليًا. حاول مرة أخرى لاحقًا.")

    return router


def _user_line(user: dict) -> str:
    return f"ID: {user.get('id')}\nTelegram: {user.get('telegram_id')}\nEmail: {user.get('email')}\nUsername: {user.get('username')}\nBalance: {user.get('points_balance')}\nBanned: {user.get('is_banned')}"


def _product_line(product: dict) -> str:
    return f"{product.get('id')} | {product.get('name')} | enabled={product.get('enabled')} | sale={product.get('sale_price_points')} | stock={product.get('inStock')}"


def _channel_line(channel: dict) -> str:
    return f"{channel.get('id')} | {channel.get('title')} | active={channel.get('is_active')} | required={channel.get('is_required')}"


def _order_line(order: dict) -> str:
    return f"{order.get('supplier_order_id')} | {order.get('product_name')} | qty={order.get('quantity')} | status={order.get('status')}"


def _order_detail(order: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in order.items())
