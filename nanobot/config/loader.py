"""Configuration loading utilities."""

import json
from pathlib import Path

from nanobot.config.schema import Config


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            data = _migrate_config(data)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move legacy tools.browser.* -> tools.web.browser.*
    legacy_browser_cfg = tools.pop("browser", None)

    # Move legacy tools.web.search.apiKey -> tools.web.search.providers.brave.apiKey
    web_cfg = tools.setdefault("web", {})
    if legacy_browser_cfg and "browser" not in web_cfg:
        web_cfg["browser"] = legacy_browser_cfg
    search_cfg = web_cfg.get("search", {})
    legacy_api_key = search_cfg.get("apiKey")
    if legacy_api_key:
        providers_cfg = search_cfg.setdefault("providers", {})
        brave_cfg = providers_cfg.setdefault("brave", {})
        if not brave_cfg.get("apiKey"):
            brave_cfg["apiKey"] = legacy_api_key

    # Fill default search provider base URLs when missing/empty
    default_base_urls = {
        "brave": "https://api.search.brave.com/res/v1/web/search",
        "tavily": "https://api.tavily.com/search",
        "serper": "https://google.serper.dev/search",
    }
    providers_cfg = search_cfg.setdefault("providers", {})
    for name, base_url in default_base_urls.items():
        provider_cfg = providers_cfg.setdefault(name, {})
        if not provider_cfg.get("baseUrl"):
            provider_cfg["baseUrl"] = base_url

    # Move legacy tools.redactSensitiveOutput -> security.redactSensitiveOutput
    security_cfg = data.setdefault("security", {})
    legacy_redaction = tools.pop("redactSensitiveOutput", None)
    if legacy_redaction is None:
        legacy_redaction = tools.pop("redact_sensitive_output", None)
    if legacy_redaction is not None and "redactSensitiveOutput" not in security_cfg:
        security_cfg["redactSensitiveOutput"] = legacy_redaction

    return data
