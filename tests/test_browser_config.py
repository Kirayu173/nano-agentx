from nanobot.config.loader import _migrate_config, convert_keys, convert_to_camel
from nanobot.config.schema import Config


def test_browser_config_defaults() -> None:
    config = Config()
    browser = config.tools.web.browser

    assert browser.enabled is True
    assert browser.default_browser == "chromium"
    assert browser.headless is True
    assert browser.timeout_ms == 15000
    assert browser.max_actions == 12
    assert browser.max_extract_chars == 20000


def test_browser_config_roundtrip_with_camel_case() -> None:
    config = Config()
    data = convert_to_camel(config.model_dump())
    reloaded = Config.model_validate(convert_keys(data))

    assert reloaded.tools.web.browser.default_browser == config.tools.web.browser.default_browser
    assert reloaded.tools.web.browser.state_dir == config.tools.web.browser.state_dir


def test_config_migrates_legacy_tools_browser_section() -> None:
    data = {
        "tools": {
            "browser": {
                "enabled": False,
                "defaultBrowser": "firefox",
            },
            "web": {
                "search": {},
            },
        }
    }

    migrated = _migrate_config(data)

    assert "browser" not in migrated["tools"]
    assert migrated["tools"]["web"]["browser"]["enabled"] is False
    assert migrated["tools"]["web"]["browser"]["defaultBrowser"] == "firefox"
