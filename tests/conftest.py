"""Minimal aiogram test doubles so the suite can run without network/package install."""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "9001")
os.environ.setdefault("SAM_API_BASE", "http://test.invalid")
os.environ.setdefault("SAM_API_KEY", "test-api-key")


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


class _Session:
    async def close(self):
        return None


class _Bot:
    def __init__(self, token):
        self.token = token
        self.session = _Session()


class _Dispatcher:
    def __init__(self):
        self.routers = []

    def include_router(self, router):
        self.routers.append(router)

    async def start_polling(self, bot):
        return None


class _DuckTypeMeta(type):
    def __instancecheck__(cls, instance):
        return instance.__class__.__name__ in {cls.__name__, "FakeMessage", "FakeCallback"}


class _Message(metaclass=_DuckTypeMeta):
    pass


class _CallbackQuery(metaclass=_DuckTypeMeta):
    pass


class _InlineKeyboardButton:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _InlineKeyboardMarkup:
    pass


class _CommandStart:
    pass


class _ChatMemberStatus:
    CREATOR = "creator"
    ADMINISTRATOR = "administrator"
    MEMBER = "member"
    RESTRICTED = "restricted"
    LEFT = "left"
    KICKED = "kicked"


class _TelegramAPIError(Exception):
    pass


# Do not override the richer stubs installed by test_user_ui if it has already
# installed them during collection. Normally this runs before test modules.
aiogram = types.ModuleType("aiogram")
aiogram.Router = _Router
aiogram.F = _F()
aiogram.Bot = _Bot
aiogram.Dispatcher = _Dispatcher

filters = types.ModuleType("aiogram.filters")
filters.CommandStart = _CommandStart

fsm = types.ModuleType("aiogram.fsm")
fsm_context = types.ModuleType("aiogram.fsm.context")
fsm_context.FSMContext = object
fsm_state = types.ModuleType("aiogram.fsm.state")
fsm_state.State = _State
fsm_state.StatesGroup = _StatesGroup

types_mod = types.ModuleType("aiogram.types")
types_mod.Message = _Message
types_mod.CallbackQuery = _CallbackQuery
types_mod.InlineKeyboardButton = _InlineKeyboardButton
types_mod.InlineKeyboardMarkup = _InlineKeyboardMarkup

enums = types.ModuleType("aiogram.enums")
enums.ChatMemberStatus = _ChatMemberStatus

exceptions = types.ModuleType("aiogram.exceptions")
exceptions.TelegramAPIError = _TelegramAPIError

utils = types.ModuleType("aiogram.utils")
keyboard = types.ModuleType("aiogram.utils.keyboard")
keyboard.InlineKeyboardBuilder = _Builder

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
