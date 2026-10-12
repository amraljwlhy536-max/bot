from __future__ import annotations

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest


class _State:
    def __init__(self, name=None):
        self.name = name


class _StatesGroupMeta(type):
    def __new__(mcls, name, bases, ns):
        cls = super().__new__(mcls, name, bases, ns)
        for key, value in ns.items():
            if isinstance(value, _State):
                value.name = f"{name}:{key}"
        return cls


class _StatesGroup(metaclass=_StatesGroupMeta):
    pass


class _Router:
    def __init__(self, name=None):
        self.name = name
        self.handlers = []

    def message(self, *args, **kwargs):
        def decorator(fn):
            self.handlers.append(("message", fn.__name__, fn))
            return fn
        return decorator

    def callback_query(self, *args, **kwargs):
        def decorator(fn):
            self.handlers.append(("callback", fn.__name__, fn))
            return fn
        return decorator


class _F:
    @property
    def data(self):
        return self

    @property
    def text(self):
        return self

    def __eq__(self, other):
        return self

    def startswith(self, other):
        return self


class _Builder:
    def __init__(self):
        self.buttons = []

    def button(self, **kwargs):
        self.buttons.append(kwargs)

    def row(self, *buttons):
        for button in buttons:
            self.buttons.append(getattr(button, "__dict__", button))

    def adjust(self, *args):
        return None

    def as_markup(self):
        return {"buttons": list(self.buttons)}


class _InlineKeyboardBuilder(_Builder):
    pass


class _InlineKeyboardButton:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _InlineKeyboardMarkup:
    pass


class _FSMContext:
    async def clear(self):
        self.data = {}
        self.state = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.state = state


class _MessageType:
    pass


class _CallbackQueryType:
    pass


class _CommandStart:
    pass


def _install_aiogram_stubs():
    aiogram = types.ModuleType("aiogram")
    aiogram.Router = _Router
    aiogram.F = _F()
    aiogram.Bot = object

    filters = types.ModuleType("aiogram.filters")
    filters.CommandStart = _CommandStart

    fsm = types.ModuleType("aiogram.fsm")
    fsm_context = types.ModuleType("aiogram.fsm.context")
    fsm_context.FSMContext = _FSMContext
    fsm_state = types.ModuleType("aiogram.fsm.state")
    fsm_state.State = _State
    fsm_state.StatesGroup = _StatesGroup

    types_mod = types.ModuleType("aiogram.types")
    types_mod.Message = _MessageType
    types_mod.CallbackQuery = _CallbackQueryType
    types_mod.InlineKeyboardButton = _InlineKeyboardButton
    types_mod.InlineKeyboardMarkup = _InlineKeyboardMarkup

    enums = types.ModuleType("aiogram.enums")
    class _ChatMemberStatus:
        CREATOR = "creator"
        ADMINISTRATOR = "administrator"
        MEMBER = "member"
        RESTRICTED = "restricted"
    enums.ChatMemberStatus = _ChatMemberStatus

    exceptions = types.ModuleType("aiogram.exceptions")
    class _TelegramAPIError(Exception):
        pass
    exceptions.TelegramAPIError = _TelegramAPIError

    utils = types.ModuleType("aiogram.utils")
    keyboard = types.ModuleType("aiogram.utils.keyboard")
    keyboard.InlineKeyboardBuilder = _InlineKeyboardBuilder

    sys.modules.update({
        "aiogram": aiogram,
        "aiogram.filters": filters,
        "aiogram.fsm": fsm,
        "aiogram.fsm.context": fsm_context,
        "aiogram.fsm.state": fsm_state,
        "aiogram.types": types_mod,
        "aiogram.enums": enums,
        "aiogram.exceptions": exceptions,
        "aiogram.utils": utils,
        "aiogram.utils.keyboard": keyboard,
    })


_install_aiogram_stubs()
user_module = importlib.import_module("app.handlers.user")
auth_module = importlib.import_module("app.handlers.auth")


@dataclass
class User:
    id: int


class FakeMessage(_MessageType):
    def __init__(self, user_id=1001, text=None):
        self.from_user = User(user_id)
        self.text = text
        self.answers = []
        self.edits = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None):
        self.edits.append((None, reply_markup))


class FakeCallback(_CallbackQueryType):
    def __init__(self, user_id=1001, data=""):
        self.from_user = User(user_id)
        self.data = data
        self.message = FakeMessage(user_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert, kwargs))


class FakeState(_FSMContext):
    def __init__(self):
        self.data = {}
        self.state = None


class FakeChannelsResult:
    def __init__(self, complete=True):
        self.is_complete = complete
        self.missing_channels = []
        self.errors = []


class FakeChannelsService:
    def __init__(self, complete=True):
        self.complete = complete

    async def check_all_required_channels(self, bot, telegram_id):
        return FakeChannelsResult(self.complete)


class FakePurchaseService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def purchase(self, telegram_id, product_id, quantity):
        self.calls.append((telegram_id, product_id, quantity))
        if self.error:
            raise self.error
        return self.result


class FakePointsService:
    def __init__(self, storage):
        self.storage = storage

    def get_balance(self, user_id):
        user = self.storage.find_by_id("users.json", user_id)
        return user["points_balance"]


def _handlers(router, kind):
    return {name: fn for k, name, fn in router.handlers if k == kind}


def make_env(tmp_path: Path, complete=True, purchase_result=None, purchase_error=None):
    from app.services.auth_service import AuthService
    from app.services.json_storage import JsonStorage

    storage = JsonStorage(tmp_path / "data")
    auth = AuthService(storage)
    user = auth.register_with_email(1001, "user@example.com")
    storage.append("products.json", {
        "id": "p1", "supplier_product_id": "sp1", "name": "Product 1",
        "enabled": True, "sale_price_points": 700, "inStock": 10, "description": "Demo",
    })
    channels = FakeChannelsService(complete)
    purchase = FakePurchaseService(purchase_result, purchase_error)
    points = FakePointsService(storage)
    router = user_module.create_user_router(storage, auth, points, purchase, channels, object())
    return storage, auth, router, purchase


def test_user_flow_products_quantity_confirmation_purchase_orders_balance_account(tmp_path):
    order = {
        "supplier_order_id": "ord-1", "product_id": "p1", "product_name": "Product 1",
        "quantity": 2, "sale_price_points": 700, "total_points": 1400,
        "delivered_data": "DATA-123", "status": "SUCCESS",
    }
    storage, auth, router, purchase = make_env(tmp_path, purchase_result=order)
    user_id = auth.get_user_by_telegram_id(1001)["id"]
    storage.update("users.json", lambda u: u.get("id") == user_id, {"points_balance": 2000})
    hs = _handlers(router, "callback")
    hm = _handlers(router, "message")

    # Main menu.
    home = FakeCallback(1001, "user:home")
    asyncio.run(hs["home_callback"](home, FakeState()))
    assert "القائمة الرئيسية" in home.message.edits[-1][0]

    # Products -> product details -> quantity -> confirmation.
    products = FakeCallback(1001, "user:products")
    asyncio.run(hs["products_callback"](products, FakeState()))
    assert "Product 1" in products.message.edits[-1][1]["buttons"][0]["text"]

    details = FakeCallback(1001, "user:product:p1")
    state = FakeState()
    asyncio.run(hs["product_details_callback"](details, state))
    assert state.state.name.endswith("waiting_for_quantity")

    quantity = FakeMessage(1001, "2")
    asyncio.run(hm["quantity_message"](quantity, state))
    assert "الإجمالي: 1400 نقطة" in quantity.answers[-1][0]

    confirm = FakeCallback(1001, "user:purchase:confirm")
    confirm.message = FakeMessage(1001)
    asyncio.run(hs["confirm_purchase_callback"](confirm, state))
    assert "DATA-123" in confirm.message.edits[-1][0]
    assert purchase.calls == [(1001, "p1", 2)]

    # My orders only comes from this user's PURCHASE transaction ownership.
    tx = {
        "id": "tx1", "user_id": auth.get_user_by_telegram_id(1001)["id"],
        "type": "PURCHASE", "amount": 1400, "balance_before": 2000,
        "balance_after": 600, "reason": None, "admin_id": None,
        "order_id": "ord-1", "created_at": "now",
    }
    storage.append("balance_transactions.json", tx)
    storage.append("orders.json", order)
    orders = FakeCallback(1001, "user:orders")
    asyncio.run(hs["orders_callback"](orders))
    assert "Product 1" in orders.message.edits[-1][0]
    assert "DATA-123" not in orders.message.edits[-1][0]

    balance = FakeCallback(1001, "user:balance")
    asyncio.run(hs["balance_callback"](balance))
    assert "رصيدك" in balance.message.edits[-1][0]

    account = FakeCallback(1001, "user:account")
    asyncio.run(hs["account_callback"](account))
    assert "user@example.com" in account.message.edits[-1][0]


def test_required_channels_blocks_products(tmp_path):
    _, _, router, _ = make_env(tmp_path, complete=False)
    handler = _handlers(router, "callback")["products_callback"]
    callback = FakeCallback(1001, "user:products")
    asyncio.run(handler(callback, FakeState()))
    assert any("يرجى" in item[0] or "القنوات" in item[0] for item in callback.message.answers)


def test_invalid_quantity_does_not_reach_purchase(tmp_path):
    _, _, router, purchase = make_env(tmp_path)
    hs = _handlers(router, "callback")
    hm = _handlers(router, "message")
    state = FakeState()
    details = FakeCallback(1001, "user:product:p1")
    asyncio.run(hs["product_details_callback"](details, state))
    bad = FakeMessage(1001, "51")
    asyncio.run(hm["quantity_message"](bad, state))
    assert "50" in bad.answers[-1][0]
    assert purchase.calls == []


def test_supplier_failure_is_user_safe(tmp_path):
    from app.services.purchase_service import InsufficientPointsForPurchaseError

    _, _, router, _ = make_env(tmp_path, purchase_error=InsufficientPointsForPurchaseError("no"))
    hs = _handlers(router, "callback")
    state = FakeState()
    state.data = {"product_id": "p1", "quantity": 1}
    callback = FakeCallback(1001, "user:purchase:confirm")
    asyncio.run(hs["confirm_purchase_callback"](callback, state))
    assert "رصيد النقاط غير كاف" in callback.message.edits[-1][0]
    assert "api" not in callback.message.edits[-1][0].lower()


def test_auth_start_and_login_callback_show_entry(tmp_path):
    from app.services.auth_service import AuthService
    from app.services.json_storage import JsonStorage

    auth = AuthService(JsonStorage(tmp_path / "data"))
    shown = []

    async def on_authenticated(message, telegram_id):
        shown.append((telegram_id, message))

    router = auth_module.create_auth_router(auth, on_authenticated=on_authenticated)
    hs = _handlers(router, "message")
    state = FakeState()
    start = FakeMessage(1001, "/start")
    asyncio.run(hs["start_handler"](start, state))
    assert "بريدك" in start.answers[-1][0]

    email = FakeMessage(1001, "new@example.com")
    asyncio.run(hs["email_handler"](email, state))
    assert shown and shown[-1][0] == 1001
