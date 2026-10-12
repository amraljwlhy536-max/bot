from __future__ import annotations

import logging



from typing import Any
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from django.db import router
from app.config import Settings

from app.handlers.required_channels import _topup_payment_methods_keyboard
from app.integrations.sam_topup.errors import SamTopupError
from app.services import auth_service
from app.services.auth_service import AuthService
from app.services.points_service import PointsService, UserNotFoundError
from app.services.purchase_service import (
    InsufficientPointsForPurchaseError,
    InvalidQuantityError,
    ProductDisabledError,
    ProductNotFoundError,
    ProductSalePriceMissingError,
    PurchaseService,
    PurchaseSupplierError,
    PurchaseSupplierTimeoutError,
    RequiredChannelsIncompleteError,
    UserBannedError,
    UserNotFoundForPurchaseError,
)
from app.services.json_storage import JsonStorage
from app.services.required_channel_service import RequiredChannelsCheckResult, RequiredChannelService


logger = logging.getLogger(__name__)

class UserStates(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_topup_amount = State()


def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 المنتجات", callback_data="user:products")
    builder.button(text="💰 رصيدي", callback_data="user:balance")
    builder.button(text="📦 طلباتي", callback_data="user:orders")
    builder.button(text="👤 حسابي", callback_data="user:account")
    builder.button(text="🎧 الدعم الفني", callback_data="support")

    builder.adjust(1,2,2)
    return builder.as_markup()


def _back_to_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ القائمة الرئيسية", callback_data="user:home")
    return builder.as_markup()


def _cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ إلغاء", callback_data="user:home")
    return builder.as_markup()


def _product_list_keyboard(products: list[dict[str, Any]]):
    builder = InlineKeyboardBuilder()
    for product in products:
        builder.button(
            text=str(product.get("name") or product.get("id")),
            callback_data=f"user:product:{product.get('id')}",
        )
    builder.button(text="⬅️ القائمة الرئيسية", callback_data="user:home")
    builder.adjust(1)
    return builder.as_markup()




def _product_text(product: dict[str, Any]) -> str:
    name = escape(str(product.get("name") or "منتج"))
    price = escape(str(product.get("sale_price_points", 0)))
    stock = escape(str(product.get("inStock", 0)))

    description = str(
        product.get("description") or "لا توجد تفاصيل متاحة."
    ).strip()

    paragraphs = [
        line.strip()
        for line in description.splitlines()
        if line.strip()
    ]

    details = []
    warnings = []

    warning_keywords = (
        "استخدم الرابط",
        "بعد الشراء",
        "لا يوجد ضمان",
        "لا يوجد استبدال",
        "لن يكون هناك استبدال",
        "تُفحص الروابط",
        "الضمان",
        "يجب",
        "ممنوع",
        "تنبيه",
    )

    for paragraph in paragraphs:
        # إزالة رموز التعداد الموجودة مسبقًا
        clean_paragraph = paragraph
        if clean_paragraph.startswith(("- ", "• ", "* ", "▪️ ")):
            clean_paragraph = clean_paragraph[2:].strip()

        # فصل الشروط عن المميزات والوصف
        if any(
            keyword in clean_paragraph
            for keyword in warning_keywords
        ):
            warnings.append(clean_paragraph)
        else:
            details.append(clean_paragraph)

    separator = "━━━━━━━━━━━━━━━━━━"

    details_text = "\n\n".join(
        f"▫️ {escape(item)}" for item in details
    ) or "لا توجد تفاصيل إضافية."

    warnings_text = "\n".join(
        f"▪️ {escape(item)}" for item in warnings
    ) or "▪️ يرجى مراجعة شروط المنتج قبل الشراء."

    return (
        f"✨ <b>{name}</b>\n"
        f"{separator}\n\n"

        f"💰 <b>السعر:</b> {price} نقطة\n"
        f"📦 <b>المخزون:</b> {stock} وحدة\n\n"

        f"{separator}\n"
        f"📋 <b>تفاصيل المنتج</b>\n\n"
        f"{details_text}\n\n"

        f"{separator}\n"
        f"⚠️ <b>الشروط والضمان</b>\n\n"
        f"{warnings_text}\n\n"

        f"{separator}\n"
        f"🛒 <b>إتمام الطلب</b>\n"
        f"أرسل الكمية كرقم صحيح موجب "
        f"(الحد الأقصى 50).\n"
        f"{separator}"
    )

def _confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأكيد الشراء", callback_data="user:purchase:confirm")
    builder.button(text="❌ إلغاء", callback_data="user:home")
    builder.adjust(1)
    return builder.as_markup()


def _is_member_complete(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    return bool(getattr(result, "is_complete", False))


def create_user_router(
    storage: JsonStorage,
    auth_service: AuthService,
    points_service: PointsService,
    purchase_service: PurchaseService,
    required_channel_service: RequiredChannelService,
    bot: Any,
) -> Router:
    router = Router(name="user")

    async def check_access(telegram_id: int) -> tuple[dict[str, Any] | None, RequiredChannelsCheckResult | None]:
        user = auth_service.get_user_by_telegram_id(telegram_id)
        if user is None:
            return None, None
        result = await required_channel_service.check_all_required_channels(bot, telegram_id)
        return user, result

    async def show_required_channels(message: Message | CallbackQuery, result: RequiredChannelsCheckResult) -> None:
        from app.keyboards.required_channels import build_required_channels_keyboard, format_required_channels

        channels = result.missing_channels + [item.channel for item in result.errors]
        target = message.message if isinstance(message, CallbackQuery) else message
        await target.answer(
            format_required_channels(channels),
            reply_markup=build_required_channels_keyboard(channels),
        )
        if result.errors:
            await target.answer("تعذر التحقق من بعض القنوات حاليًا. أعد المحاولة بعد قليل.")

 
    async def show_home(target: Message | CallbackQuery, telegram_id: int) -> None:
        user, result = await check_access(telegram_id)

        if user is None:
            if isinstance(target, CallbackQuery):
                await target.message.answer("يرجى استخدام /start لتسجيل الدخول.")
            else:
                await target.answer("يرجى استخدام /start لتسجيل الدخول.")
            return

        if user.get("is_banned") is True:
            text = "🚫 حسابك محظور حاليًا ولا يمكنك استخدام الخدمات."
            if isinstance(target, CallbackQuery):
                await target.message.edit_text(text)
            else:
                await target.answer(text)
            return

        if result is not None and not result.is_complete:
            await show_required_channels(target, result)
            return

        # جلب المعلومات الأساسية للعميل
        first_name = user.get("first_name") or "عميلنا العزيز"
        username = user.get("username") or "غير متاح"

        try:
            balance = points_service.get_balance(user["id"])
        except UserNotFoundError:
            balance = 0

        # حساب عدد الطلبات الخاصة بالعميل
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

        text = (
            f"🚀 مرحبًا بك في NexTech!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 أهلًا بك، {first_name}\n"
            f"🆔 معرف تيليجرام: {telegram_id}\n"
            f"👤 اسم المستخدم: {username}\n\n"
            f"💰 رصيدك الحالي: {balance} نقطة\n"
            f"📦 إجمالي طلباتك: {orders_count}\n\n"
            f"🛍️ اكتشف منتجاتنا وخدماتنا الرقمية.\n"
            f"⚡ تجربة سهلة وسريعة لتلبية احتياجاتك.\n\n"
            f"👇 اختر الخدمة التي تريدها من القائمة:"
        )

        if isinstance(target, CallbackQuery):
            await target.message.edit_text(
                text,
                reply_markup=main_menu_keyboard(),
            )
        else:
            await target.answer(
                text,
                reply_markup=main_menu_keyboard(),
            )

  
    @router.callback_query(F.data == "user:home")
    async def home_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await show_home(callback, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data == "user:products")
    async def products_callback(callback: CallbackQuery, state: FSMContext) -> None:
        user, result = await check_access(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.answer("حسابك محظور.", show_alert=True)
            return
        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        products = storage.read("products.json")["products"]
        visible = [
            product for product in products
            if product.get("enabled") is True
            and isinstance(product.get("sale_price_points"), int)
            and not isinstance(product.get("sale_price_points"), bool)
            and product.get("sale_price_points") > 0
        ]
        
        if not visible:
            await callback.message.edit_text(
                "🛍️ متجر NexTech | نيكس تك\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "📭 لا توجد منتجات متاحة للشراء حاليًا.\n\n"
                "🔄 نعمل على توفير المزيد من المنتجات الرقمية.\n"
                "يرجى العودة لاحقًا للاطلاع على المنتجات الجديدة.",
                reply_markup=_back_to_main_keyboard(),
            )
        else:
            text = (
                "🛍️ متجر NexTech | نيكس تك\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "✨ اكتشف مجموعتنا من المنتجات والخدمات الرقمية.\n\n"
                "💎 اختر المنتج الذي يناسبك للاطلاع على تفاصيله "
                "وسعره ومعلومات التوفر.\n\n"
                "⚡ بعد اختيار المنتج، يمكنك تحديد الكمية "
                "وتأكيد طلبك بسهولة.\n\n"
                "👇 اختر أحد المنتجات أدناه للمتابعة:"
            )

            await callback.message.edit_text(
                text,
                reply_markup=_product_list_keyboard(visible),
            )

        await callback.answer()


    @router.callback_query(F.data.startswith("user:product:"))
    async def product_details_callback(callback: CallbackQuery, state: FSMContext) -> None:
        user, result = await check_access(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.answer("حسابك محظور.", show_alert=True)
            return
        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        product_id = (callback.data or "").split(":", 2)[2]
        product = storage.find_by_id("products.json", product_id)
        if not product or product.get("enabled") is not True:
            await callback.answer("المنتج غير متاح.", show_alert=True)
            return
        price = product.get("sale_price_points")
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            await callback.answer("المنتج غير متاح للشراء حاليًا.", show_alert=True)
            return

        await state.clear()
        await state.update_data(product_id=product_id)
        await state.set_state(UserStates.waiting_for_quantity)
        await callback.message.edit_text(
        _product_text(product)
        + "\n\n📦 أرسل الكمية كرقم صحيح موجب (الحد الأقصى 50).",
        reply_markup=_cancel_keyboard(),
        parse_mode="HTML",
    )
        await callback.answer()

    @router.message(UserStates.waiting_for_quantity)
    async def quantity_message(message: Message, state: FSMContext) -> None:
        user = auth_service.get_user_by_telegram_id(message.from_user.id)
        if user is None:
            await state.clear()
            await message.answer("يرجى استخدام /start لتسجيل الدخول.")
            return
        if user.get("is_banned") is True:
            await state.clear()
            await message.answer("حسابك محظور.")
            return

        raw = (message.text or "").strip()
        try:
            quantity = int(raw)
        except ValueError:
            await message.answer("الكمية يجب أن تكون رقمًا صحيحًا موجبًا ولا تتجاوز 50.")
            return
        if quantity <= 0 or quantity > PurchaseService.MAX_QUANTITY:
            await message.answer("الكمية يجب أن تكون رقمًا صحيحًا موجبًا ولا تتجاوز 50.")
            return

        data = await state.get_data()
        product_id = data.get("product_id")
        product = storage.find_by_id("products.json", product_id)
        if not product or product.get("enabled") is not True:
            await state.clear()
            await message.answer("المنتج غير متاح.", reply_markup=_back_to_main_keyboard())
            return
        price = product.get("sale_price_points")
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            await state.clear()
            await message.answer("المنتج غير متاح للشراء حاليًا.", reply_markup=_back_to_main_keyboard())
            return

        total = price * quantity
        await state.update_data(product_id=product_id, quantity=quantity)
        builder = _confirmation_keyboard()
        await message.answer(
            f"تأكيد الشراء\n\nالمنتج: {product.get('name')}\nالكمية: {quantity}\nالسعر: {price} نقطة\nالإجمالي: {total} نقطة",
            reply_markup=builder,
        )

    @router.callback_query(F.data == "user:purchase:confirm")
    async def confirm_purchase_callback(callback: CallbackQuery, state: FSMContext) -> None:
        user, result = await check_access(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.answer("حسابك محظور.", show_alert=True)
            return
        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        data = await state.get_data()
        product_id = data.get("product_id")
        quantity = data.get("quantity")
        if not isinstance(product_id, str) or not isinstance(quantity, int):
            await state.clear()
            await callback.message.edit_text("انتهت صلاحية عملية الشراء. ابدأ من جديد.", reply_markup=main_menu_keyboard())
            await callback.answer()
            return

        await callback.message.edit_text("جارٍ تنفيذ الطلب...")
        try:
            order = await purchase_service.purchase(callback.from_user.id, product_id, quantity)
        except PurchaseSupplierTimeoutError:
            await state.clear()
            await callback.message.edit_text(
                "حدثت مهلة أثناء تنفيذ الطلب لدى المورد. حالة الطلب غير محسومة، ولم يتم خصم نقاطك. لا تعِد إرسال الطلب تلقائيًا.",
                reply_markup=main_menu_keyboard(),
            )
        except InsufficientPointsForPurchaseError:
            await state.clear()
            await callback.message.edit_text("رصيد النقاط غير كافٍ لهذا الطلب.", reply_markup=main_menu_keyboard())
        except ProductNotFoundError:
            await state.clear()
            await callback.message.edit_text("المنتج غير موجود.", reply_markup=main_menu_keyboard())
        except ProductDisabledError:
            await state.clear()
            await callback.message.edit_text("المنتج غير متاح حاليًا.", reply_markup=main_menu_keyboard())
        except ProductSalePriceMissingError:
            await state.clear()
            await callback.message.edit_text("هذا المنتج غير متاح للشراء حاليًا.", reply_markup=main_menu_keyboard())
        except InvalidQuantityError:
            await state.clear()
            await callback.message.edit_text("الكمية غير صحيحة. يجب أن تكون بين 1 و50.", reply_markup=main_menu_keyboard())
        except RequiredChannelsIncompleteError:
            await state.clear()
            await callback.message.edit_text("يجب إكمال الاشتراك في القنوات المطلوبة أولًا.", reply_markup=main_menu_keyboard())
        except UserBannedError:
            await state.clear()
            await callback.message.edit_text("حسابك محظور.")
        except UserNotFoundForPurchaseError:
            await state.clear()
            await callback.message.edit_text("يرجى تسجيل الدخول أولًا.")
        except PurchaseSupplierError as exc:
            await state.clear()
            await callback.message.edit_text(_supplier_error_message(exc.error), reply_markup=main_menu_keyboard())
        except Exception:
            logger.exception(
                "Unexpected purchase UI failure actor_id=%s",
                callback.from_user.id,
            )
            await state.clear()
            await callback.message.edit_text("تعذر تنفيذ الطلب حاليًا. حاول مرة أخرى لاحقًا.", reply_markup=main_menu_keyboard())
        else:
            await state.clear()
            await callback.message.edit_text(
                _success_text(order),
                reply_markup=main_menu_keyboard(),
            )
        await callback.answer()

    @router.callback_query(F.data == "user:balance")
    async def balance_callback(callback: CallbackQuery) -> None:
        user, result = await check_access(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.answer("حسابك محظور.", show_alert=True)
            return
        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return
            
        try:
            balance = points_service.get_balance(user["id"])
        except UserNotFoundError:
            await callback.answer("الحساب غير موجود.", show_alert=True)
            return

        builder = InlineKeyboardBuilder()

        builder.button(
            text="➕ طلب شحن الرصيد",
            callback_data="user:topup",
        )
        builder.button(
            text="⬅️ القائمة الرئيسية",
            callback_data="user:home",
        )
        builder.adjust(1)

        await callback.message.edit_text(
            f"💰 محفظتك في NexTech\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"💎 رصيدك الحالي: {balance} نقطة\n\n"
            f"يمكنك طلب شحن رصيدك لمتابعة شراء "
            f"المنتجات والخدمات الرقمية.\n\n"
            f"👇 اضغط أدناه لبدء طلب الشحن.",
            reply_markup=builder.as_markup(),
        )

        await callback.answer()
    @router.callback_query(F.data == "user:topup")
    async def topup_start_callback(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        user, result = await check_access(callback.from_user.id)

        if user is None:
            await callback.answer(
                "يرجى تسجيل الدخول أولًا.",
                show_alert=True,
            )
            return

        if user.get("is_banned") is True:
            await callback.answer(
                "حسابك محظور.",
                show_alert=True,
            )
            return

        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        await state.clear()

        await callback.message.edit_text(
            "💳 طلب شحن الرصيد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "اختر طريقة الدفع المناسبة لك:",
            reply_markup=_topup_payment_methods_keyboard(),
        )

        await callback.answer()


    @router.callback_query(F.data.startswith("user:topup:method:"))
    async def topup_method_callback(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        user, result = await check_access(callback.from_user.id)

        if user is None:
            await callback.answer(
                "يرجى تسجيل الدخول أولًا.",
                show_alert=True,
            )
            return

        if user.get("is_banned") is True:
            await callback.answer(
                "حسابك محظور.",
                show_alert=True,
            )
            return

        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        method = (callback.data or "").rsplit(":", 1)[-1]

        payment_methods = {
            "bank": "تحويل بنكي",
            "wallet": "محفظة إلكترونية",
            "other": "طريقة دفع أخرى",
        }

        if method not in payment_methods:
            await callback.answer(
                "طريقة الدفع غير صالحة.",
                show_alert=True,
            )
            return

        await state.clear()
        await state.update_data(
            topup_payment_method=method,
        )
        await state.set_state(UserStates.waiting_for_topup_amount)

        await callback.message.edit_text(
            "💰 تحديد مبلغ الشحن\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"طريقة الدفع: {payment_methods[method]}\n\n"
            "أرسل الآن مبلغ الشحن الذي تريد طلبه "
            "كعدد صحيح موجب.\n\n"
            "مثال: 500",
            reply_markup=_cancel_keyboard(),
        )

        await callback.answer()    
    @router.callback_query(F.data == "user:orders")
    async def orders_callback(callback: CallbackQuery) -> None:
        user, result = await check_access(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.answer("حسابك محظور.", show_alert=True)
            return
        if result is not None and not result.is_complete:
            await show_required_channels(callback, result)
            await callback.answer()
            return

        transactions = storage.read("balance_transactions.json")["transactions"]
        order_ids = {
            tx.get("order_id")
            for tx in transactions
            if tx.get("type") == "PURCHASE" and tx.get("user_id") == user.get("id") and tx.get("order_id")
        }
        orders = [
            order for order in storage.read("orders.json")["orders"]
            if order.get("supplier_order_id") in order_ids
        ]
        if not orders:
            text = "لا توجد طلبات لك حتى الآن."
        else:
            lines = ["طلباتك:"]
            for order in orders:
                lines.append(
                    f"\n{order.get('product_name')}\nالكمية: {order.get('quantity')}\n"
                    f"النقاط: {order.get('total_points')}\nالحالة: {order.get('status')}\n"
                    f"رقم الطلب: {order.get('supplier_order_id')}"
                )
            text = "".join(lines)
        await callback.message.edit_text(text, reply_markup=_back_to_main_keyboard())
        await callback.answer()

    @router.callback_query(F.data == "user:account")
    async def account_callback(callback: CallbackQuery) -> None:
        user = auth_service.get_user_by_telegram_id(callback.from_user.id)
        if user is None:
            await callback.answer("يرجى تسجيل الدخول أولًا.", show_alert=True)
            return
        if user.get("is_banned") is True:
            await callback.message.edit_text("حسابك محظور.", reply_markup=_back_to_main_keyboard())
            await callback.answer()
            return
        balance = points_service.get_balance(user["id"])
        username = user.get("username") or "غير متاح"
        await callback.message.edit_text(
            f"حسابي\n\nEmail: {user.get('email')}\nTelegram ID: {user.get('telegram_id')}\nUsername: {username}\nالرصيد: {balance} نقطة",
            reply_markup=_back_to_main_keyboard(),
        )
        await callback.answer()

   
    @router.message(UserStates.waiting_for_topup_amount)
    async def topup_amount_message(
        message: Message,
        state: FSMContext,
    ) -> None:
        settings = Settings()
        user =  auth_service.get_user_by_telegram_id(
            message.from_user.id
        )

        if user is None:
            await state.clear()
            await message.answer(
                "يرجى استخدام /start لتسجيل الدخول."
            )
            return

        if user.get("is_banned") is True:
            await state.clear()
            await message.answer("حسابك محظور.")
            return

        raw_amount = (message.text or "").strip()

        try:
            amount = int(raw_amount)
        except ValueError:
            await message.answer(
                "❌ المبلغ غير صحيح.\n"
                "أرسل مبلغًا رقميًا صحيحًا، مثل: 500"
            )
            return

        if amount <= 0 or amount > 1_000_000:
            await message.answer(
                "❌ يجب أن يكون المبلغ بين 1 و1,000,000."
            )
            return

        data = await state.get_data()
        method = data.get("topup_payment_method")

        payment_methods = {
            "bank": "تحويل بنكي",
            "wallet": "محفظة إلكترونية",
            "other": "طريقة دفع أخرى",
        }

        if method not in payment_methods:
            await state.clear()
            await message.answer(
                "انتهت صلاحية طلب الشحن. ابدأ من جديد.",
                reply_markup=_back_to_main_keyboard(),
            )
            return

        username = message.from_user.username
        username_text = (
            f"@{username}" if username else "غير متاح"
        )

        admin_message = (
            "💳 طلب شحن رصيد جديد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 الاسم: {message.from_user.full_name}\n"
            f"🆔 Telegram ID: {message.from_user.id}\n"
            f"🔗 Username: {username_text}\n"
            f"💰 مبلغ الشحن: {amount}\n"
            f"💳 طريقة الدفع: {payment_methods[method]}\n\n"
            "📌 الحالة: بانتظار مراجعة الإدارة\n"
            "يرجى التواصل مع العميل والتحقق من الدفع "
            "قبل إضافة الرصيد."
        )

        try:
            for admin_id in settings.admin_ids:
                await bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                )
        except Exception:
            logger.exception(
                "Failed to notify admins about topup request"
            )
            await message.answer(
                "تعذر إرسال طلب الشحن إلى الإدارة حاليًا. "
                "حاول مرة أخرى لاحقًا."
            )
            return

        await state.clear()

        await message.answer(
            "✅ تم إرسال طلب شحن الرصيد إلى الإدارة.\n\n"
            f"💰 المبلغ: {amount}\n"
            f"💳 طريقة الدفع: {payment_methods[method]}\n\n"
            "⏳ طلبك بانتظار المراجعة. "
            "لن تتم إضافة الرصيد حتى يتم التحقق من الدفع.",
            reply_markup=_back_to_main_keyboard(),
        )
    return router
def _supplier_error_message(error: SamTopupError) -> str:
    messages = {
        "INVALID_PRODUCT_ID": "المنتج غير صالح لدى المورد.",
        "INVALID_QUANTITY": "الكمية غير صالحة.",
        "QUANTITY_TOO_LARGE": "الكمية أكبر من الحد المسموح.",
        "PRODUCT_NOT_FOUND": "المنتج غير موجود لدى المورد.",
        "INSUFFICIENT_BALANCE": "تعذر تنفيذ الطلب لدى المورد حاليًا.",
        "OUT_OF_STOCK": "المنتج غير متوفر في المخزون حاليًا.",
        "RATE_LIMITED": "تم تجاوز حد الطلبات لدى المورد. حاول لاحقًا.",
        "INTERNAL_ERROR": "حدث خطأ لدى المورد. حاول لاحقًا.",
    }
    return messages.get(error.code, "تعذر تنفيذ الطلب حاليًا. حاول لاحقًا.")


def _success_text(order: dict[str, Any]) -> str:
    delivered = order.get("delivered_data")
    return (
        "تم تنفيذ الطلب بنجاح.\n\n"
        f"المنتج: {order.get('product_name')}\n"
        f"الكمية: {order.get('quantity')}\n"
        f"إجمالي النقاط: {order.get('total_points')}\n"
        f"رقم طلب المورد: {order.get('supplier_order_id')}\n\n"
        f"Delivered Data:\n{delivered}"
    )
