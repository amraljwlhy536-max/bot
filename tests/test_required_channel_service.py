from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus

from app.services.required_channel_service import RequiredChannelService


class FakeRepository:
    def __init__(self, channels):
        self.channels = channels

    def get_active_required_channels(self):
        return [c for c in self.channels if c.get("is_required") is True and c.get("is_active") is True]


def channel(channel_id="1", username="example", active=True):
    return {
        "id": channel_id,
        "chat_id": f"-100{channel_id}",
        "username": username,
        "title": f"Channel {channel_id}",
        "is_required": True,
        "is_active": active,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


class Member:
    def __init__(self, status, is_member=None):
        self.status = status
        if is_member is not None:
            self.is_member = is_member


@pytest.mark.asyncio
async def test_no_required_channels():
    service = RequiredChannelService(FakeRepository([]))
    result = await service.check_all_required_channels(AsyncMock(), 10)
    assert result.is_complete
    assert result.missing_channels == []


@pytest.mark.asyncio
async def test_required_channel_user_is_member():
    bot = AsyncMock()
    bot.get_chat_member.return_value = Member(ChatMemberStatus.MEMBER)
    service = RequiredChannelService(FakeRepository([channel()]))
    result = await service.check_all_required_channels(bot, 10)
    assert result.is_complete
    assert result.missing_channels == []


@pytest.mark.asyncio
async def test_required_channel_user_is_not_member():
    bot = AsyncMock()
    bot.get_chat_member.return_value = Member(ChatMemberStatus.LEFT)
    service = RequiredChannelService(FakeRepository([channel()]))
    result = await service.check_all_required_channels(bot, 10)
    assert not result.is_complete
    assert [c["id"] for c in result.missing_channels] == ["1"]


@pytest.mark.asyncio
async def test_multiple_channels():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = [
        Member(ChatMemberStatus.MEMBER),
        Member(ChatMemberStatus.ADMINISTRATOR),
    ]
    service = RequiredChannelService(FakeRepository([channel("1"), channel("2")]))
    result = await service.check_all_required_channels(bot, 10)
    assert result.is_complete
    assert bot.get_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_user_member_of_only_some_channels():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = [
        Member(ChatMemberStatus.MEMBER),
        Member(ChatMemberStatus.LEFT),
        Member(ChatMemberStatus.KICKED),
    ]
    service = RequiredChannelService(FakeRepository([channel("1"), channel("2"), channel("3")]))
    result = await service.check_all_required_channels(bot, 10)
    assert not result.is_complete
    assert [c["id"] for c in result.missing_channels] == ["2", "3"]


@pytest.mark.asyncio
async def test_inactive_channel_is_ignored():
    bot = AsyncMock()
    service = RequiredChannelService(
        FakeRepository([channel("1", active=False)])
    )
    result = await service.check_all_required_channels(bot, 10)
    assert result.is_complete
    bot.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_api_error_is_not_membership():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = RuntimeError("telegram api failure")
    service = RequiredChannelService(FakeRepository([channel()]))
    result = await service.check_all_required_channels(bot, 10)
    assert not result.is_complete
    assert result.missing_channels == []
    assert len(result.errors) == 1
    assert result.errors[0].is_member is False


@pytest.mark.asyncio
async def test_restricted_member_status_uses_is_member_flag():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = [
        Member(ChatMemberStatus.RESTRICTED, is_member=False),
        Member(ChatMemberStatus.RESTRICTED, is_member=True),
    ]
    service = RequiredChannelService(FakeRepository([channel()]))

    first = await service.check_all_required_channels(bot, 10)
    second = await service.check_all_required_channels(bot, 10)

    assert not first.is_complete
    assert second.is_complete


@pytest.mark.asyncio
async def test_recheck_after_subscription_change():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = [
        Member(ChatMemberStatus.LEFT),
        Member(ChatMemberStatus.MEMBER),
    ]
    service = RequiredChannelService(FakeRepository([channel()]))

    first = await service.check_all_required_channels(bot, 10)
    second = await service.check_all_required_channels(bot, 10)

    assert not first.is_complete
    assert second.is_complete
