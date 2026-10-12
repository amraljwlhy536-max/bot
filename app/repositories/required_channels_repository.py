from __future__ import annotations

from typing import Any

from app.services.json_storage import JsonStorage


class RequiredChannelsRepository:
    FILE_NAME = "required_channels.json"

    def __init__(self, storage: JsonStorage):
        self.storage = storage

    def get_active_required_channels(self) -> list[dict[str, Any]]:
        document = self.storage.read(self.FILE_NAME)
        return [
            channel
            for channel in document["channels"]
            if channel.get("is_required") is True and channel.get("is_active") is True
        ]
