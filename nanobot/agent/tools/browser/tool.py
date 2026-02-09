"""Browser automation tool built on Playwright."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.browser.installer import (
    install_playwright_browsers,
    is_missing_browser_error,
)
from nanobot.agent.tools.browser.safety import (
    request_url_block_reason,
    resolve_path_in_workspace,
    validate_navigation_url,
    validate_state_key,
)

_SUPPORTED_BROWSERS = ("chromium", "firefox")
_SUPPORTED_ACTIONS = ("goto", "click", "type", "wait_for", "extract_text", "screenshot")
_SUPPORTED_WAIT_UNTIL = ("domcontentloaded", "load", "networkidle")

if TYPE_CHECKING:
    from nanobot.config.schema import BrowserToolConfig


class BrowserRunTool(Tool):
    """Run a list of browser actions in a single Playwright session."""

    name = "browser_run"
    description = (
        "Run browser actions (goto/click/type/wait/extract/screenshot) in one "
        "Playwright session."
    )
    parameters = {
        "type": "object",
        "properties": {
            "browser": {
                "type": "string",
                "enum": list(_SUPPORTED_BROWSERS),
                "description": "Browser engine to use",
            },
            "headless": {
                "type": "boolean",
                "description": "Run browser in headless mode",
            },
            "startUrl": {
                "type": "string",
                "description": "Optional URL to open before actions",
            },
            "timeoutMs": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 120000,
                "description": "Default timeout per action in milliseconds",
            },
            "stateKey": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "Session key for persisted storage state",
            },
            "saveState": {
                "type": "boolean",
                "description": "Whether to save state after run",
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": list(_SUPPORTED_ACTIONS),
                        },
                        "url": {"type": "string"},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                        "timeoutMs": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 120000,
                        },
                        "waitUntil": {
                            "type": "string",
                            "enum": list(_SUPPORTED_WAIT_UNTIL),
                        },
                        "maxChars": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 100000,
                        },
                        "path": {"type": "string"},
                        "fullPage": {"type": "boolean"},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["actions"],
    }

    def __init__(self, workspace: Path, web_browser_config: BrowserToolConfig | None = None):
        from nanobot.config.schema import BrowserToolConfig

        self.workspace = workspace.resolve()
        self.config = web_browser_config or BrowserToolConfig()

        self.state_dir = resolve_path_in_workspace(
            self.workspace,
            self.config.state_dir,
            "tools.web.browser.stateDir",
        )
        self.artifacts_dir = resolve_path_in_workspace(
            self.workspace,
            self.config.artifacts_dir,
            "tools.web.browser.artifactsDir",
        )

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    async def execute(
        self,
        actions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        started_at = time.monotonic()
        browser = kwargs.get("browser")
        headless = kwargs.get("headless")
        start_url = kwargs.get("startUrl")
        timeout_ms = kwargs.get("timeoutMs")
        state_key = kwargs.get("stateKey")
        save_state = bool(kwargs.get("saveState", False))

        try:
            result = await self._execute_internal(
                actions=actions,
                browser=browser,
                headless=headless,
                start_url=start_url,
                timeout_ms=timeout_ms,
                state_key=state_key,
                save_state=save_state,
            )
            result["timingMs"] = int((time.monotonic() - started_at) * 1000)
            return json.dumps(result, ensure_ascii=False)
        except ValueError as e:
            return json.dumps(
                self._error_payload(
                    "invalid_input",
                    str(e),
                    timing_ms=int((time.monotonic() - started_at) * 1000),
                ),
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                self._error_payload(
                    "browser_run_failed",
                    str(e),
                    timing_ms=int((time.monotonic() - started_at) * 1000),
                ),
                ensure_ascii=False,
            )

    async def _execute_internal(
        self,
        *,
        actions: list[dict[str, Any]],
        browser: str | None,
        headless: bool | None,
        start_url: str | None,
        timeout_ms: int | None,
        state_key: str | None,
        save_state: bool,
    ) -> dict[str, Any]:
        if not actions:
            raise ValueError("actions must not be empty")

        if len(actions) > self.config.max_actions:
            raise ValueError(
                f"actions count exceeds maxActions={self.config.max_actions}"
            )

        browser_name = (browser or self.config.default_browser).lower()
        if browser_name not in _SUPPORTED_BROWSERS:
            raise ValueError(f"browser must be one of {_SUPPORTED_BROWSERS}")

        effective_timeout = timeout_ms if timeout_ms is not None else self.config.timeout_ms
        if effective_timeout < 1000 or effective_timeout > 120000:
            raise ValueError("timeoutMs must be in [1000, 120000]")

        effective_headless = self.config.headless if headless is None else bool(headless)

        state_path: Path | None = None
        if state_key:
            valid_key, error = validate_state_key(state_key)
            if not valid_key:
                raise ValueError(error)
            state_path = self.state_dir / f"{state_key}.json"

        if save_state and not state_path:
            raise ValueError("saveState=true requires stateKey")

        if start_url:
            ok, error = validate_navigation_url(
                start_url,
                allow_private_network=self.config.allow_private_network,
                block_file_scheme=self.config.block_file_scheme,
            )
            if not ok:
                raise ValueError(error)

        self._validate_actions(actions)

        if not start_url and not any(action.get("type") == "goto" for action in actions):
            raise ValueError("either startUrl or at least one goto action is required")

        try:
            return await self._run_once(
                actions=actions,
                browser_name=browser_name,
                headless=effective_headless,
                start_url=start_url,
                timeout_ms=effective_timeout,
                state_path=state_path,
                save_state=save_state,
            )
        except Exception as first_error:
            if not self.config.auto_install_browsers or not is_missing_browser_error(first_error):
                raise

            ok, details = await install_playwright_browsers(_SUPPORTED_BROWSERS)
            if not ok:
                return self._error_payload(
                    "browser_install_failed",
                    details,
                    details={"initialError": str(first_error)},
                )

            try:
                rerun_result = await self._run_once(
                    actions=actions,
                    browser_name=browser_name,
                    headless=effective_headless,
                    start_url=start_url,
                    timeout_ms=effective_timeout,
                    state_path=state_path,
                    save_state=save_state,
                )
            except Exception as second_error:
                return self._error_payload(
                    "browser_run_failed",
                    str(second_error),
                    details={
                        "initialError": str(first_error),
                        "installOutput": details,
                    },
                )

            rerun_result["installOutput"] = details
            return rerun_result

    def _validate_actions(self, actions: list[dict[str, Any]]) -> None:
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                raise ValueError(f"action #{index} must be an object")

            action_type = action.get("type")
            if action_type not in _SUPPORTED_ACTIONS:
                raise ValueError(
                    f"action #{index}: unsupported type '{action_type}', expected {_SUPPORTED_ACTIONS}"
                )

            timeout_ms = action.get("timeoutMs")
            if timeout_ms is not None and (timeout_ms < 100 or timeout_ms > 120000):
                raise ValueError(f"action #{index}: timeoutMs must be in [100, 120000]")

            if action_type == "goto":
                url = action.get("url")
                if not isinstance(url, str) or not url:
                    raise ValueError(f"action #{index}: goto requires non-empty url")
                ok, error = validate_navigation_url(
                    url,
                    allow_private_network=self.config.allow_private_network,
                    block_file_scheme=self.config.block_file_scheme,
                )
                if not ok:
                    raise ValueError(f"action #{index}: {error}")

                wait_until = action.get("waitUntil")
                if wait_until is not None and wait_until not in _SUPPORTED_WAIT_UNTIL:
                    raise ValueError(
                        f"action #{index}: waitUntil must be one of {_SUPPORTED_WAIT_UNTIL}"
                    )

            if action_type in {"click", "type", "wait_for", "extract_text"}:
                selector = action.get("selector")
                text = action.get("text")
                if action_type in {"click", "type"} and (
                    not isinstance(selector, str) or not selector
                ):
                    raise ValueError(f"action #{index}: {action_type} requires selector")

                if action_type == "type" and (not isinstance(text, str)):
                    raise ValueError(f"action #{index}: type requires text")

                if action_type == "wait_for" and not selector and not text:
                    # wait_for can be sleep-only when timeoutMs is provided.
                    if action.get("timeoutMs") is None:
                        raise ValueError(
                            f"action #{index}: wait_for requires selector/text or timeoutMs"
                        )

            if action_type == "extract_text":
                max_chars = action.get("maxChars")
                if max_chars is not None and (max_chars < 100 or max_chars > 100000):
                    raise ValueError(f"action #{index}: maxChars must be in [100, 100000]")

            if action_type == "screenshot":
                output_path = action.get("path")
                if output_path:
                    resolve_path_in_workspace(
                        self.workspace,
                        output_path,
                        f"action #{index} screenshot.path",
                    )

    async def _run_once(
        self,
        *,
        actions: list[dict[str, Any]],
        browser_name: str,
        headless: bool,
        start_url: str | None,
        timeout_ms: int,
        state_path: Path | None,
        save_state: bool,
    ) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        artifacts: list[str] = []
        steps: list[dict[str, Any]] = []

        async with async_playwright() as playwright:
            browser_type = getattr(playwright, browser_name)
            browser_instance = await browser_type.launch(headless=headless)

            try:
                context_kwargs: dict[str, Any] = {"accept_downloads": False}
                if state_path and state_path.exists():
                    context_kwargs["storage_state"] = str(state_path)

                context = await browser_instance.new_context(**context_kwargs)
                try:
                    await context.route("**/*", self._apply_network_guard)

                    page = await context.new_page()

                    if start_url:
                        response = await page.goto(
                            start_url,
                            wait_until="domcontentloaded",
                            timeout=timeout_ms,
                        )
                        steps.append(
                            {
                                "index": 0,
                                "type": "goto",
                                "source": "startUrl",
                                "url": start_url,
                                "status": response.status if response else None,
                            }
                        )

                    for index, action in enumerate(actions, start=1):
                        step_result = await self._execute_action(
                            page=page,
                            action=action,
                            index=index,
                            default_timeout_ms=timeout_ms,
                            artifacts=artifacts,
                        )
                        steps.append(step_result)

                    if save_state and state_path:
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        await context.storage_state(path=str(state_path))

                    final_url = page.url
                    title = await page.title()
                finally:
                    await context.close()
            finally:
                await browser_instance.close()

        return {
            "ok": True,
            "browser": browser_name,
            "headless": headless,
            "finalUrl": final_url,
            "title": title,
            "steps": steps,
            "artifacts": artifacts,
            "error": None,
        }

    async def _apply_network_guard(self, route: Any, request: Any) -> None:
        reason = request_url_block_reason(
            request.url,
            allow_private_network=self.config.allow_private_network,
            block_file_scheme=self.config.block_file_scheme,
        )
        if reason:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _execute_action(
        self,
        *,
        page: Any,
        action: dict[str, Any],
        index: int,
        default_timeout_ms: int,
        artifacts: list[str],
    ) -> dict[str, Any]:
        action_type = action["type"]
        timeout_ms = int(action.get("timeoutMs") or default_timeout_ms)

        if action_type == "goto":
            url = str(action["url"])
            wait_until = str(action.get("waitUntil") or "domcontentloaded")
            response = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return {
                "index": index,
                "type": "goto",
                "url": url,
                "status": response.status if response else None,
                "waitUntil": wait_until,
            }

        if action_type == "click":
            selector = str(action["selector"])
            await page.click(selector, timeout=timeout_ms)
            return {
                "index": index,
                "type": "click",
                "selector": selector,
            }

        if action_type == "type":
            selector = str(action["selector"])
            text = str(action["text"])
            await page.fill(selector, text, timeout=timeout_ms)
            return {
                "index": index,
                "type": "type",
                "selector": selector,
                "chars": len(text),
            }

        if action_type == "wait_for":
            selector = action.get("selector")
            text = action.get("text")
            if selector:
                await page.wait_for_selector(str(selector), timeout=timeout_ms)
                return {
                    "index": index,
                    "type": "wait_for",
                    "selector": selector,
                }
            if text:
                await page.get_by_text(str(text)).first.wait_for(timeout=timeout_ms)
                return {
                    "index": index,
                    "type": "wait_for",
                    "text": text,
                }

            await page.wait_for_timeout(timeout_ms)
            return {
                "index": index,
                "type": "wait_for",
                "sleepMs": timeout_ms,
            }

        if action_type == "extract_text":
            selector = action.get("selector")
            locator = page.locator(str(selector)).first if selector else page.locator("body")
            extracted = await locator.inner_text(timeout=timeout_ms)
            max_chars = int(action.get("maxChars") or self.config.max_extract_chars)
            max_chars = min(max_chars, self.config.max_extract_chars)
            truncated = len(extracted) > max_chars
            text = extracted[:max_chars] if truncated else extracted
            return {
                "index": index,
                "type": "extract_text",
                "selector": selector,
                "length": len(text),
                "truncated": truncated,
                "text": text,
            }

        if action_type == "screenshot":
            output = self._resolve_screenshot_path(action.get("path"), index)
            output.parent.mkdir(parents=True, exist_ok=True)
            full_page = bool(action.get("fullPage", False))
            await page.screenshot(path=str(output), full_page=full_page)
            artifacts.append(str(output.relative_to(self.workspace)))
            return {
                "index": index,
                "type": "screenshot",
                "path": str(output.relative_to(self.workspace)),
                "fullPage": full_page,
            }

        raise RuntimeError(f"Unsupported action type: {action_type}")

    def _resolve_screenshot_path(self, raw_path: Any, index: int) -> Path:
        if raw_path:
            return resolve_path_in_workspace(
                self.workspace,
                str(raw_path),
                "screenshot path",
            )

        timestamp_ms = int(time.time() * 1000)
        filename = f"screenshot-{timestamp_ms}-{index}.png"
        return self.artifacts_dir / filename

    def _error_payload(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        timing_ms: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "steps": [],
            "artifacts": [],
            "finalUrl": None,
            "title": None,
            "error": {
                "code": code,
                "message": message,
            },
        }
        if details:
            payload["error"]["details"] = details
        if timing_ms is not None:
            payload["timingMs"] = timing_ms
        return payload
