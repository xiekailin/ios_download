from __future__ import annotations

from typing import Protocol

from app.domain.models import ExtractedMedia


class ExtractorProvider(Protocol):
    name: str

    def can_handle(self, url: str) -> bool: ...

    def extract(self, url: str) -> ExtractedMedia: ...
