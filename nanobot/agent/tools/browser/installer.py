"""Playwright browser installer helpers."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

_INSTALL_LOCK = asyncio.Lock()
_DEFAULT_INSTALL_TIMEOUT_S = 10 * 60


def is_missing_browser_error(exc: Exception) -> bool:
    """Detect Playwright launch failures caused by missing browser binaries."""
    text = str(exc).lower()
    patterns = (
        "executable doesn't exist",
        "please run the following command",
        "browser has not been found",
    )
    return any(p in text for p in patterns)


async def install_playwright_browsers(
    browsers: Sequence[str],
    *,
    timeout_s: int = _DEFAULT_INSTALL_TIMEOUT_S,
) -> tuple[bool, str]:
    """Install required Playwright browsers via `python -m playwright install ...`."""
    requested = [b for b in dict.fromkeys(browsers) if b]
    if not requested:
        return False, "No browser targets specified"

    async with _INSTALL_LOCK:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            *requested,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            return False, f"Playwright install timed out after {timeout_s}s"

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        merged = "\n".join(part for part in (out, err) if part)

        if process.returncode == 0:
            return True, _trim_output(merged) or "Playwright browsers installed"

        if not merged:
            merged = f"playwright install exited with code {process.returncode}"
        return False, _trim_output(merged)


def _trim_output(text: str, max_chars: int = 4000) -> str:
    """Trim command output for tool responses."""
    if len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    return text[:max_chars] + f"\n... (truncated, {remaining} more chars)"
