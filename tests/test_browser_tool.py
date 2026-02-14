import json

import pytest

from nanobot.agent.tools.browser.tool import BrowserRunTool
from nanobot.config.schema import BrowserToolConfig


@pytest.mark.asyncio
async def test_browser_run_success_flow(monkeypatch, tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    captured: dict[str, object] = {}

    async def fake_run_once(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "browser": kwargs["browser_name"],
            "headless": kwargs["headless"],
            "finalUrl": "https://example.com/",
            "title": "Example Domain",
            "steps": [{"index": 1, "type": "goto", "url": "https://example.com"}],
            "artifacts": [],
            "error": None,
        }

    monkeypatch.setattr(tool, "_run_once", fake_run_once)

    raw = await tool.execute(
        browser="firefox",
        headless=False,
        startUrl="https://example.com",
        stateKey="demo",
        saveState=True,
        actions=[{"type": "goto", "url": "https://example.com"}],
    )

    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["browser"] == "firefox"
    assert captured["browser_name"] == "firefox"
    assert captured["headless"] is False
    assert captured["state_path"] == tool.state_dir / "demo.json"


@pytest.mark.asyncio
async def test_browser_run_rejects_private_network(monkeypatch, tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    called = {"run": False}

    async def fake_run_once(**kwargs):
        called["run"] = True
        return {}

    monkeypatch.setattr(tool, "_run_once", fake_run_once)

    raw = await tool.execute(actions=[{"type": "goto", "url": "http://127.0.0.1"}])
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "Private/local host blocked" in payload["error"]["message"]
    assert called["run"] is False


@pytest.mark.asyncio
async def test_browser_run_rejects_file_scheme(tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    raw = await tool.execute(actions=[{"type": "goto", "url": "file:///tmp/a.html"}])
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "file://" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_browser_run_state_key_validation(tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    raw = await tool.execute(
        stateKey="../bad",
        saveState=True,
        actions=[{"type": "goto", "url": "https://example.com"}],
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "stateKey" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_browser_run_screenshot_path_escape_blocked(tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    raw = await tool.execute(
        actions=[
            {"type": "goto", "url": "https://example.com"},
            {"type": "screenshot", "path": "../outside.png"},
        ]
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "workspace" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_browser_run_auto_install_triggered_once(monkeypatch, tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=True),
    )

    calls = {"run": 0, "install": 0}

    async def fake_run_once(**kwargs):
        calls["run"] += 1
        if calls["run"] == 1:
            raise RuntimeError("Executable doesn't exist at /tmp/ms-playwright")
        return {
            "ok": True,
            "browser": kwargs["browser_name"],
            "headless": kwargs["headless"],
            "finalUrl": "https://example.com/",
            "title": "Example",
            "steps": [],
            "artifacts": [],
            "error": None,
        }

    async def fake_install(browsers):
        calls["install"] += 1
        assert tuple(browsers) == ("chromium", "firefox")
        return True, "installed"

    monkeypatch.setattr(tool, "_run_once", fake_run_once)
    monkeypatch.setattr(
        "nanobot.agent.tools.browser.tool.install_playwright_browsers",
        fake_install,
    )

    raw = await tool.execute(actions=[{"type": "goto", "url": "https://example.com"}])
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["installOutput"] == "installed"
    assert calls == {"run": 2, "install": 1}


@pytest.mark.asyncio
async def test_browser_run_action_limit_enforced(tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(max_actions=1, auto_install_browsers=False),
    )

    raw = await tool.execute(
        actions=[
            {"type": "goto", "url": "https://example.com"},
            {"type": "goto", "url": "https://example.org"},
        ]
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "maxActions" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_browser_run_requires_state_key_when_save_state(tmp_path) -> None:
    tool = BrowserRunTool(
        workspace=tmp_path,
        web_browser_config=BrowserToolConfig(auto_install_browsers=False),
    )

    raw = await tool.execute(
        saveState=True,
        actions=[{"type": "goto", "url": "https://example.com"}],
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_input"
    assert "stateKey" in payload["error"]["message"]
