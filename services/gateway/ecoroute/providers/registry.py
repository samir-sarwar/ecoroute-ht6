from __future__ import annotations

from ecoroute.config import Settings
from ecoroute.providers.azure_openai import AzureOpenAIProvider
from ecoroute.providers.base import ProviderAdapter
from ecoroute.providers.fake import FakeProvider
from ecoroute.providers.openai_compatible import OpenAICompatibleProvider


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.fake = FakeProvider(settings)
        self.azure = AzureOpenAIProvider(settings)
        self.generic = OpenAICompatibleProvider(settings)
        self.demo_mode = settings.demo_mode

    def for_provider(self, provider: str) -> ProviderAdapter:
        if provider == "fake":
            return self.fake
        if provider == "azure_openai":
            # Azure-shaped fixture endpoints exercise the real routing and evidence
            # path in demo mode without making external calls or requiring secrets.
            return self.fake if self.demo_mode else self.azure
        return self.generic
