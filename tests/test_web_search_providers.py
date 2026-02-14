import httpx
import pytest

from nanobot.agent.tools.web import WebSearchTool
from nanobot.config.schema import WebSearchConfig


class FakeResponse:
    def __init__(self, payload: dict, error: Exception | None = None):
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error

    def json(self) -> dict:
        return self._payload


def _make_config(provider: str) -> WebSearchConfig:
    cfg = WebSearchConfig(provider=provider)  # type: ignore[arg-type]
    cfg.providers.brave.api_key = "brave-key"
    cfg.providers.tavily.api_key = "tavily-key"
    cfg.providers.serper.api_key = "serper-key"
    cfg.providers.brave.base_url = "https://brave.example/search"
    cfg.providers.tavily.base_url = "https://tavily.example/search"
    cfg.providers.serper.base_url = "https://serper.example/search"
    return cfg


@pytest.mark.asyncio
async def test_web_search_brave_success(monkeypatch) -> None:
    calls: dict = {}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            calls["url"] = url
            calls["params"] = params
            calls["headers"] = headers
            calls["timeout"] = timeout
            return FakeResponse(
                {
                    "web": {
                        "results": [
                            {
                                "title": "Brave Title",
                                "url": "https://example.com/brave",
                                "description": "Brave Snippet",
                            }
                        ]
                    }
                }
            )

    monkeypatch.setattr("nanobot.agent.tools.websearch.brave.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("brave"))
    result = await tool.execute(query="python", count=1)

    assert "1. Brave Title" in result
    assert "https://example.com/brave" in result
    assert "Brave Snippet" in result
    assert calls["url"] == "https://brave.example/search"
    assert calls["params"] == {"q": "python", "count": 1}
    assert calls["headers"]["X-Subscription-Token"] == "brave-key"


@pytest.mark.asyncio
async def test_web_search_tavily_success(monkeypatch) -> None:
    calls: dict = {}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls["url"] = url
            calls["json"] = json
            calls["headers"] = headers
            calls["timeout"] = timeout
            return FakeResponse(
                {
                    "results": [
                        {
                            "title": "Tavily Title",
                            "url": "https://example.com/tavily",
                            "content": "Tavily Snippet",
                        }
                    ]
                }
            )

    monkeypatch.setattr("nanobot.agent.tools.websearch.tavily.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("tavily"))
    result = await tool.execute(query="agent", count=1)

    assert "1. Tavily Title" in result
    assert "https://example.com/tavily" in result
    assert "Tavily Snippet" in result
    assert calls["url"] == "https://tavily.example/search"
    assert calls["json"] == {"api_key": "tavily-key", "query": "agent", "max_results": 1}


@pytest.mark.asyncio
async def test_web_search_serper_success(monkeypatch) -> None:
    calls: dict = {}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls["url"] = url
            calls["json"] = json
            calls["headers"] = headers
            calls["timeout"] = timeout
            return FakeResponse(
                {
                    "organic": [
                        {
                            "title": "Serper Title",
                            "link": "https://example.com/serper",
                            "snippet": "Serper Snippet",
                        }
                    ]
                }
            )

    monkeypatch.setattr("nanobot.agent.tools.websearch.serper.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("serper"))
    result = await tool.execute(query="search", count=1)

    assert "1. Serper Title" in result
    assert "https://example.com/serper" in result
    assert "Serper Snippet" in result
    assert calls["url"] == "https://serper.example/search"
    assert calls["json"] == {"q": "search", "num": 1}
    assert calls["headers"]["X-API-KEY"] == "serper-key"


@pytest.mark.asyncio
async def test_web_search_count_is_clamped(monkeypatch) -> None:
    counts: list[int] = []

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            counts.append(params["count"])
            return FakeResponse(
                {
                    "web": {
                        "results": [
                            {
                                "title": "T",
                                "url": "https://example.com",
                                "description": "S",
                            }
                        ]
                    }
                }
            )

    monkeypatch.setattr("nanobot.agent.tools.websearch.brave.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("brave"))
    await tool.execute(query="q", count=0)
    await tool.execute(query="q", count=1)
    await tool.execute(query="q", count=10)
    await tool.execute(query="q", count=999)

    assert counts == [1, 1, 10, 10]


@pytest.mark.asyncio
async def test_web_search_missing_provider_key(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    cfg = _make_config("tavily")
    cfg.providers.tavily.api_key = ""
    tool = WebSearchTool(web_search_config=cfg)

    result = await tool.execute(query="missing key")
    assert result.startswith("Error:")
    assert "tavily api key not configured" in result


@pytest.mark.asyncio
async def test_web_search_unknown_provider() -> None:
    cfg = _make_config("brave")
    cfg.provider = "unknown"  # type: ignore[assignment]
    tool = WebSearchTool(web_search_config=cfg)

    result = await tool.execute(query="provider?")
    assert result == "Error: unknown search provider: unknown"


@pytest.mark.asyncio
async def test_web_search_empty_results(monkeypatch) -> None:
    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            return FakeResponse({"results": []})

    monkeypatch.setattr("nanobot.agent.tools.websearch.tavily.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("tavily"))
    result = await tool.execute(query="nothing", count=3)
    assert result == "No results for: nothing"


@pytest.mark.asyncio
async def test_web_search_http_error_wrapped(monkeypatch) -> None:
    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            return FakeResponse({}, error=httpx.HTTPError("boom"))

    monkeypatch.setattr("nanobot.agent.tools.websearch.brave.httpx.AsyncClient", StubClient)

    tool = WebSearchTool(web_search_config=_make_config("brave"))
    result = await tool.execute(query="fail", count=1)
    assert result == "Error: brave search failed: boom"


@pytest.mark.asyncio
async def test_web_search_uses_default_base_url_when_config_base_url_empty(monkeypatch) -> None:
    calls: dict = {}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls["url"] = url
            return FakeResponse({"results": []})

    monkeypatch.setattr("nanobot.agent.tools.websearch.tavily.httpx.AsyncClient", StubClient)

    cfg = _make_config("tavily")
    cfg.providers.tavily.base_url = ""
    tool = WebSearchTool(web_search_config=cfg)
    result = await tool.execute(query="anything", count=1)

    assert calls["url"] == "https://api.tavily.com/search"
    assert result == "No results for: anything"
