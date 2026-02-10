from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import nanobot.agent.tools.codex as codex_module
from nanobot.agent.tools.codex import CodexRunTool
from nanobot.config.schema import CodexToolConfig

pytestmark = pytest.mark.asyncio


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        delay_sec: float = 0.0,
    ):
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")
        self.returncode = returncode
        self.delay_sec = delay_sec
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _codex_success_output(message: str = "done") -> str:
    events = [
        {"type": "thread.started", "thread_id": "thread_123"},
        {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": message},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _install_subprocess_spy(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return process

    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    return captured


def _allow_codex_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module.shutil, "which", lambda command: command)


async def test_codex_run_builds_exec_command_with_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout=_codex_success_output("Hi from Codex."))
    captured = _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, default_sandbox="read-only"),
    )

    result = await tool.execute(prompt="Say hi.")
    payload = json.loads(result)
    args = captured["args"]

    assert payload["ok"] is True
    assert payload["message"] == "Hi from Codex."
    assert args[0] == "codex"
    assert args[1] == "exec"
    assert "--sandbox" in args
    assert "read-only" in args
    assert "--skip-git-repo-check" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert captured["cwd"] == str(tmp_path.resolve())


async def test_codex_run_rejects_danger_full_access_without_global_allow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)

    async def should_not_spawn(*args, **kwargs):  # pragma: no cover
        raise AssertionError("subprocess should not be called")

    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", should_not_spawn)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=False),
    )

    result = await tool.execute(prompt="Run with full access", sandbox="danger-full-access")
    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "dangerous_full_access_not_allowed"


async def test_codex_run_full_access_uses_bypass_without_sandbox_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout=_codex_success_output("Done."))
    captured = _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
    )

    result = await tool.execute(prompt="Do task")
    payload = json.loads(result)
    args = captured["args"]

    assert payload["ok"] is True
    assert payload["sandbox"] == "danger-full-access"
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--sandbox" not in args


async def test_codex_run_review_command_supports_full_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout=_codex_success_output("Review done."))
    captured = _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
    )

    result = await tool.execute(
        prompt="Review this repo",
        mode="review",
        model="o3",
    )
    payload = json.loads(result)
    args = captured["args"]

    assert payload["ok"] is True
    assert args[:3] == ["codex", "exec", "review"]
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--skip-git-repo-check" not in args
    assert "-m" in args
    assert args[-1] == "Review this repo"


async def test_codex_run_global_full_access_overrides_requested_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout=_codex_success_output("Done."))
    captured = _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, allow_dangerous_full_access=True),
    )

    result = await tool.execute(prompt="Do task", sandbox="read-only")
    payload = json.loads(result)
    args = captured["args"]

    assert payload["ok"] is True
    assert payload["sandbox"] == "danger-full-access"
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "--sandbox" not in args


async def test_codex_run_handles_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout=_codex_success_output(), delay_sec=2.0)
    _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(workspace=tmp_path, codex_config=CodexToolConfig(enabled=True))

    result = await tool.execute(prompt="Slow task", timeout_sec=1)
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "timeout"
    assert process.killed is True


async def test_codex_run_handles_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(returncode=2, stderr="auth required")
    _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(workspace=tmp_path, codex_config=CodexToolConfig(enabled=True))

    result = await tool.execute(prompt="Task")
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "codex_failed"
    assert payload["exit_code"] == 2


async def test_codex_run_handles_invalid_jsonl_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    process = FakeProcess(stdout="not-json\nstill-not-json\n")
    _install_subprocess_spy(monkeypatch, process)
    tool = CodexRunTool(workspace=tmp_path, codex_config=CodexToolConfig(enabled=True))

    result = await tool.execute(prompt="Task")
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_output"


async def test_codex_run_reports_missing_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_module.shutil, "which", lambda command: None)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, command="codex-not-found"),
    )

    result = await tool.execute(prompt="Task")
    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "command_not_found"


async def test_codex_run_rejects_workspace_write_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    tool = CodexRunTool(
        workspace=tmp_path,
        codex_config=CodexToolConfig(enabled=True, allow_workspace_write=False),
    )

    result = await tool.execute(prompt="Task", sandbox="workspace-write")
    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "workspace_write_not_allowed"


async def test_codex_run_blocks_working_dir_outside_workspace_when_restricted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_codex_binary(monkeypatch)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    tool = CodexRunTool(
        workspace=workspace,
        codex_config=CodexToolConfig(enabled=True),
        restrict_to_workspace=True,
    )
    result = await tool.execute(prompt="Task", working_dir=str(outside))
    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_working_dir"
