from __future__ import annotations

from app.core.errors import ProviderUnavailableError
from app.domain.models import ExtractedMedia


class XProvider:
    name = "x-native"

    def can_handle(self, url: str) -> bool:
        return False

    def extract(self, url: str) -> ExtractedMedia:
        raise ProviderUnavailableError("native X provider not implemented yet")
