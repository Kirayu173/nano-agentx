"""Unified web search client with pluggable providers."""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from nanobot.agent.tools.websearch.brave import search_brave
from nanobot.agent.tools.websearch.models import SearchHit
from nanobot.agent.tools.websearch.serper import search_serper
from nanobot.agent.tools.websearch.tavily import search_tavily

if TYPE_CHECKING:
    from nanobot.config.schema import SearchProviderConfig, WebSearchConfig

SearchProvider = Literal["brave", "tavily", "serper"]


class WebSearchError(Exception):
    """Raised when search provider selection or execution fails."""


class WebSearchClient:
    """Provider dispatcher for web search."""

    _ENV_KEYS: dict[SearchProvider, str] = {
        "brave": "BRAVE_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "serper": "SERPER_API_KEY",
    }
    _DEFAULT_BASE_URLS: dict[SearchProvider, str] = {
        "brave": "https://api.search.brave.com/res/v1/web/search",
        "tavily": "https://api.tavily.com/search",
        "serper": "https://google.serper.dev/search",
    }
    _SEARCHERS: dict[
        SearchProvider,
        Callable[..., "object"],
    ] = {
        "brave": search_brave,
        "tavily": search_tavily,
        "serper": search_serper,
    }

    def __init__(self, config: "WebSearchConfig | None" = None):
        from nanobot.config.schema import WebSearchConfig

        self.config = config or WebSearchConfig()

    async def search(self, *, query: str, count: int) -> list[SearchHit]:
        """Search using the configured provider."""
        provider = (self.config.provider or "brave").lower()
        if provider not in self._SEARCHERS:
            raise WebSearchError(f"unknown search provider: {provider}")

        provider_cfg = self._get_provider_config(provider)
        api_key = provider_cfg.api_key or os.environ.get(self._ENV_KEYS[provider], "")
        if not api_key:
            env_key = self._ENV_KEYS[provider]
            raise WebSearchError(
                f"{provider} api key not configured "
                f"(set tools.web.search.providers.{provider}.apiKey or {env_key})"
            )

        searcher = self._SEARCHERS[provider]
        base_url = provider_cfg.base_url or self._DEFAULT_BASE_URLS[provider]
        try:
            return await searcher(
                query=query,
                count=count,
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as e:
            raise WebSearchError(f"{provider} search failed: {e}") from e

    def _get_provider_config(self, provider: SearchProvider) -> "SearchProviderConfig":
        providers = self.config.providers
        return getattr(providers, provider)
