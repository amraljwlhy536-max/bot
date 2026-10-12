from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram import Bot

from app.repositories.required_channels_repository import RequiredChannelsRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MembershipCheckResult:
    channel: dict[str, Any]
    is_member: bool
    error: str | None = None


@dataclass(frozen=True)
class RequiredChannelsCheckResult:
    required_channels: list[dict[str, Any]]
    missing_channels: list[dict[str, Any]]
    errors: list[MembershipCheckResult]

    @property
    def is_complete(self) -> bool:
        return not self.missing_channels and not self.errors


class RequiredChannelService:
    """Checks required Telegram channel membership without mutating channel data."""

    MEMBER_STATUSES = {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    }

    def __init__(self, repository: RequiredChannelsRepository):
        self.repository = repository

    def get_active_required_channels(self) -> list[dict[str, Any]]:
        return self.repository.get_active_required_channels()

    async def check_user_membership(
        self,
        bot: Bot,
        user_id: int,
        channel: dict[str, Any],
    ) -> MembershipCheckResult:
        try:
            member = await bot.get_chat_member(
                chat_id=channel["chat_id"],
                user_id=user_id,
            )
        except TelegramAPIError as exc:
            logger.warning(
                "Telegram membership check failed for required channel id=%s: %s",
                channel.get("id"),
                type(exc).__name__,
            )
            return MembershipCheckResult(
                channel=channel,
                is_member=False,
                error=type(exc).__name__,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected membership check failure for required channel id=%s: %s",
                channel.get("id"),
                type(exc).__name__,
            )
            return MembershipCheckResult(
                channel=channel,
                is_member=False,
                error=type(exc).__name__,
            )

        status = getattr(member, "status", None)
        # Telegram's Restricted status is membership only when is_member=True.
        if status == ChatMemberStatus.RESTRICTED:
            is_member = bool(getattr(member, "is_member", False))
        else:
            is_member = status in self.MEMBER_STATUSES

        return MembershipCheckResult(channel=channel, is_member=is_member)

    async def check_all_required_channels(
        self,
        bot: Bot,
        user_id: int,
    ) -> RequiredChannelsCheckResult:
        channels = self.get_active_required_channels()
        if not channels:
            return RequiredChannelsCheckResult([], [], [])

        results = [
            await self.check_user_membership(bot, user_id, channel)
            for channel in channels
        ]
        missing = [result.channel for result in results if not result.is_member and result.error is None]
        errors = [result for result in results if result.error is not None]
        return RequiredChannelsCheckResult(channels, missing, errors)
