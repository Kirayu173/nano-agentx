import copy

from nanobot.config.loader import _migrate_config


def test_migrate_legacy_web_search_api_key_to_brave_provider() -> None:
    raw = {
        "tools": {
            "web": {
                "search": {
                    "apiKey": "legacy-brave-key",
                }
            }
        }
    }

    migrated = _migrate_config(copy.deepcopy(raw))
    brave_api_key = migrated["tools"]["web"]["search"]["providers"]["brave"]["apiKey"]
    assert brave_api_key == "legacy-brave-key"


def test_migrate_web_search_does_not_override_new_provider_key() -> None:
    raw = {
        "tools": {
            "web": {
                "search": {
                    "apiKey": "legacy-brave-key",
                    "providers": {
                        "brave": {
                            "apiKey": "new-brave-key",
                        }
                    },
                }
            }
        }
    }

    migrated = _migrate_config(copy.deepcopy(raw))
    brave_api_key = migrated["tools"]["web"]["search"]["providers"]["brave"]["apiKey"]
    assert brave_api_key == "new-brave-key"


def test_migrate_web_search_fills_default_provider_base_urls() -> None:
    raw = {
        "tools": {
            "web": {
                "search": {
                    "providers": {
                        "tavily": {
                            "apiKey": "tvly-xxx",
                            "baseUrl": "",
                        }
                    }
                }
            }
        }
    }

    migrated = _migrate_config(copy.deepcopy(raw))
    providers = migrated["tools"]["web"]["search"]["providers"]
    assert providers["brave"]["baseUrl"] == "https://api.search.brave.com/res/v1/web/search"
    assert providers["tavily"]["baseUrl"] == "https://api.tavily.com/search"
    assert providers["serper"]["baseUrl"] == "https://google.serper.dev/search"
