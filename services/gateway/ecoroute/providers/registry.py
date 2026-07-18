from __future__ import annotations

from ecoroute.config import Settings
from ecoroute.providers.base import ProviderAdapter
from ecoroute.providers.fake import FakeProvider
from ecoroute.providers.openai_compatible import OpenAICompatibleProvider


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.fake = FakeProvider(settings)
        self.generic = OpenAICompatibleProvider(settings)

    def for_provider(self, provider: str) -> ProviderAdapter:
        if provider == "fake":
            return self.fake
        return self.generic
