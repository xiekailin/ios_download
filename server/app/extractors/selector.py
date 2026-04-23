from __future__ import annotations

from app.core.errors import ProviderAppError, ProviderUnavailableError
from app.domain.models import ExtractedMedia
from app.extractors.base import ExtractorProvider


class ProviderSelector:
    def __init__(self, providers: list[ExtractorProvider]) -> None:
        self._providers = providers

    def extract(self, url: str) -> ExtractedMedia:
        last_error: Exception | None = None
        for provider in self._providers:
            if not provider.can_handle(url):
                continue
            try:
                return provider.extract(url)
            except ProviderUnavailableError as exc:
                last_error = exc
            except ProviderAppError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProviderUnavailableError("no provider available")
