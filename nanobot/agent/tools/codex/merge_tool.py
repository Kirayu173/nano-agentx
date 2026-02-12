"""Codex merge orchestration tool."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.codex.client import CodexClient
from nanobot.agent.tools.codex.models import ExecutionResult, MergePlanRecord
from nanobot.agent.tools.codex.store import MergePlanStore
from nanobot.config.schema import CodexToolConfig

_REPORT_GLOB = "upstream-main-conflict-report-*.md"
_DEFAULT_REPO_ROOT = Path("d:/Work/nano-agentx")


class CodexMergeTool(Tool):
    """Plan and execute merge operations through Codex."""

    name = "codex_merge"
    description = (
        "Codex merge advisor and executor. "
        "Actions: plan_latest, revise_plan, execute_merge, status, list. "
        "Nanobot only orchestrates and reports; codex performs merge/conflict/push operations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["plan_latest", "revise_plan", "execute_merge", "status", "list"],
                "description": "Action to run",
            },
            "plan_id": {
                "type": "string",
                "description": "Merge plan ID for revise_plan/execute_merge/status",
            },
            "feedback": {
                "type": "string",
                "description": "User feedback for revise_plan",
            },
            "confirmation_token": {
                "type": "string",
                "description": "Token required by execute_merge",
            },
            "base_ref": {
                "type": "string",
                "description": "Merge base ref for planning",
            },
            "upstream_ref": {
                "type": "string",
                "description": "Upstream ref to merge from",
            },
            "target_branch": {
                "type": "string",
                "description": "Target branch to merge into",
            },
            "working_dir": {
                "type": "string",
                "description": "Repository root used by codex",
            },
            "model": {
                "type": "string",
                "description": "Optional codex model override",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7200,
                "description": "Optional timeout override",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "List action result limit",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        workspace: Path,
        codex_config: CodexToolConfig | None = None,
        restrict_to_workspace: bool = False,
        store: MergePlanStore | None = None,
        client: CodexClient | None = None,
        repo_root: Path | None = None,
    ):
        self.workspace = workspace.resolve()
        self.config = codex_config or CodexToolConfig()
        self.store = store or MergePlanStore(self.workspace)
        if client is not None:
            self._plan_client = client
            self._exec_client = client
        else:
            plan_cfg = self.config.model_copy(update={"allow_dangerous_full_access": False})
            self._plan_client = CodexClient(
                workspace=self.workspace,
                codex_config=plan_cfg,
                restrict_to_workspace=restrict_to_workspace,
            )
            self._exec_client = CodexClient(
                workspace=self.workspace,
                codex_config=self.config,
                restrict_to_workspace=restrict_to_workspace,
            )

        configured_root = repo_root or _DEFAULT_REPO_ROOT
        configured_root = configured_root.expanduser()
        self.repo_root = (
            configured_root.resolve()
            if configured_root.exists() and configured_root.is_dir()
            else self.workspace
        )

    async def execute(
        self,
        action: str,
        plan_id: str | None = None,
        feedback: str | None = None,
        confirmation_token: str | None = None,
        base_ref: str = "origin/main",
        upstream_ref: str = "upstream/main",
        target_branch: str = "main",
        working_dir: str | None = None,
        model: str | None = None,
        timeout_sec: int | None = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> str:
        selected_action = (action or "").strip().lower()
        if selected_action == "plan_latest":
            return await self._plan_latest(
                base_ref=base_ref,
                upstream_ref=upstream_ref,
                target_branch=target_branch,
                working_dir=working_dir,
                model=model,
                timeout_sec=timeout_sec,
            )
        if selected_action == "revise_plan":
            return await self._revise_plan(
                plan_id=plan_id,
                feedback=feedback,
                model=model,
                timeout_sec=timeout_sec,
            )
        if selected_action == "execute_merge":
            return await self._execute_merge(
                plan_id=plan_id,
                confirmation_token=confirmation_token,
                model=model,
                timeout_sec=timeout_sec,
            )
        if selected_action == "status":
            return self._status(plan_id=plan_id)
        if selected_action == "list":
            return self._list(limit=limit)

        return self._error("invalid_action", "action must be one of plan_latest|revise_plan|execute_merge|status|list")

    async def _plan_latest(
        self,
        *,
        base_ref: str,
        upstream_ref: str,
        target_branch: str,
        working_dir: str | None,
        model: str | None,
        timeout_sec: int | None,
    ) -> str:
        if not self.config.enabled:
            return self._error("codex_disabled", "tools.codex.enabled=false; codex_merge is unavailable")

        report_path = self._find_latest_report()
        if report_path is None:
            return self._error(
                "report_not_found",
                f"No report found under {self.workspace / 'reports'} matching {_REPORT_GLOB}",
            )

        report_excerpt = self._read_excerpt(report_path)
        selected_working_dir = self._select_working_dir(working_dir)

        prompt = self._build_plan_prompt(
            report_path=report_path,
            report_excerpt=report_excerpt,
            base_ref=base_ref,
            upstream_ref=upstream_ref,
            target_branch=target_branch,
        )
        codex = await self._plan_client.run(
            prompt=prompt,
            mode="exec",
            working_dir=selected_working_dir,
            sandbox="read-only",
            model=model,
            timeout_sec=timeout_sec,
        )
        if not codex.get("ok"):
            codex["action"] = "plan_latest"
            return self._dump(codex)

        now_ms = self._now_ms()
        plan_id = secrets.token_hex(4)
        confirmation_token = secrets.token_hex(16)
        record = MergePlanRecord(
            plan_id=plan_id,
            status="planned",
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            base_ref=base_ref,
            upstream_ref=upstream_ref,
            target_branch=target_branch,
            working_dir=selected_working_dir,
            report_path=str(report_path),
            report_excerpt=report_excerpt,
            recommendation=str(codex.get("message", "")),
            confirmation_token_hash=self._hash_token(confirmation_token),
            revision=0,
            last_feedback=None,
            plan_thread_id=codex.get("thread_id"),
            plan_usage=dict(codex.get("usage") or {}),
            execution=None,
        )
        self.store.save(record)

        return self._dump(
            {
                "ok": True,
                "action": "plan_latest",
                "plan_id": record.plan_id,
                "confirmation_token": confirmation_token,
                "status": record.status,
                "report_path": record.report_path,
                "summary": self._summarize(record.recommendation),
                "message": "Merge plan prepared. Merge is not executed yet.",
            }
        )

    async def _revise_plan(
        self,
        *,
        plan_id: str | None,
        feedback: str | None,
        model: str | None,
        timeout_sec: int | None,
    ) -> str:
        if not self.config.enabled:
            return self._error("codex_disabled", "tools.codex.enabled=false; codex_merge is unavailable")

        selected_plan_id = (plan_id or "").strip()
        if not selected_plan_id:
            return self._error("missing_plan_id", "plan_id is required for revise_plan")

        clean_feedback = (feedback or "").strip()
        if not clean_feedback:
            return self._error("missing_feedback", "feedback is required for revise_plan")

        record = self.store.load(selected_plan_id)
        if record is None:
            return self._error("plan_not_found", f"plan_id not found: {selected_plan_id}")

        report_path = Path(record.report_path)
        if not report_path.exists() or not report_path.is_file():
            return self._error("report_not_found", f"report file not found: {record.report_path}")

        report_excerpt = self._read_excerpt(report_path)
        prompt = self._build_revise_prompt(record=record, feedback=clean_feedback, report_excerpt=report_excerpt)
        codex = await self._plan_client.run(
            prompt=prompt,
            mode="exec",
            working_dir=record.working_dir or self._select_working_dir(None),
            sandbox="read-only",
            model=model,
            timeout_sec=timeout_sec,
        )
        if not codex.get("ok"):
            codex["action"] = "revise_plan"
            codex["plan_id"] = selected_plan_id
            return self._dump(codex)

        confirmation_token = secrets.token_hex(16)
        record.recommendation = str(codex.get("message", ""))
        record.status = "revised"
        record.revision += 1
        record.last_feedback = clean_feedback
        record.updated_at_ms = self._now_ms()
        record.plan_thread_id = codex.get("thread_id")
        record.plan_usage = dict(codex.get("usage") or {})
        record.report_excerpt = report_excerpt
        record.confirmation_token_hash = self._hash_token(confirmation_token)
        self.store.save(record)

        return self._dump(
            {
                "ok": True,
                "action": "revise_plan",
                "plan_id": record.plan_id,
                "confirmation_token": confirmation_token,
                "status": record.status,
                "revision": record.revision,
                "summary": self._summarize(record.recommendation),
                "message": "Merge plan revised. Merge is not executed yet.",
            }
        )

    async def _execute_merge(
        self,
        *,
        plan_id: str | None,
        confirmation_token: str | None,
        model: str | None,
        timeout_sec: int | None,
    ) -> str:
        if not self.config.enabled:
            return self._error("codex_disabled", "tools.codex.enabled=false; codex_merge is unavailable")
        if not self.config.allow_dangerous_full_access:
            return self._error(
                "dangerous_full_access_not_allowed",
                "execute_merge requires tools.codex.allowDangerousFullAccess=true",
            )

        selected_plan_id = (plan_id or "").strip()
        if not selected_plan_id:
            return self._error("missing_plan_id", "plan_id is required for execute_merge")

        provided_token = (confirmation_token or "").strip()
        if not provided_token:
            return self._error("missing_confirmation_token", "confirmation_token is required for execute_merge")

        record = self.store.load(selected_plan_id)
        if record is None:
            return self._error("plan_not_found", f"plan_id not found: {selected_plan_id}")

        expected_hash = record.confirmation_token_hash
        if not expected_hash or self._hash_token(provided_token) != expected_hash:
            return self._error("invalid_confirmation_token", "confirmation token mismatch")

        report_path = Path(record.report_path)
        if not report_path.exists() or not report_path.is_file():
            return self._error("report_not_found", f"report file not found: {record.report_path}")

        prompt = self._build_execute_prompt(record)
        codex = await self._exec_client.run(
            prompt=prompt,
            mode="exec",
            working_dir=record.working_dir or self._select_working_dir(None),
            sandbox="danger-full-access",
            model=model,
            timeout_sec=timeout_sec,
        )

        now_ms = self._now_ms()
        if codex.get("ok"):
            record.status = "executed"
            record.updated_at_ms = now_ms
            record.confirmation_token_hash = ""
            record.execution = ExecutionResult(
                ok=True,
                summary=self._summarize(str(codex.get("message", "")), max_chars=1200),
                at_ms=now_ms,
                thread_id=codex.get("thread_id"),
                usage=dict(codex.get("usage") or {}),
                error=None,
            )
            self.store.save(record)
            return self._dump(
                {
                    "ok": True,
                    "action": "execute_merge",
                    "plan_id": record.plan_id,
                    "status": record.status,
                    "summary": record.execution.summary,
                    "message": "Merge execution completed by codex.",
                }
            )

        error_message = self._extract_error_message(codex)
        record.status = "failed"
        record.updated_at_ms = now_ms
        record.execution = ExecutionResult(
            ok=False,
            summary=error_message,
            at_ms=now_ms,
            thread_id=codex.get("thread_id"),
            usage=dict(codex.get("usage") or {}),
            error=error_message,
        )
        self.store.save(record)

        codex["action"] = "execute_merge"
        codex["plan_id"] = record.plan_id
        codex["status"] = "failed"
        return self._dump(codex)

    def _status(self, *, plan_id: str | None) -> str:
        selected_plan_id = (plan_id or "").strip()
        if not selected_plan_id:
            return self._error("missing_plan_id", "plan_id is required for status")

        record = self.store.load(selected_plan_id)
        if record is None:
            return self._error("plan_not_found", f"plan_id not found: {selected_plan_id}")

        return self._dump(
            {
                "ok": True,
                "action": "status",
                "plan": record.to_public_dict(include_recommendation=True),
            }
        )

    def _list(self, *, limit: int) -> str:
        records = self.store.list(limit=max(1, int(limit)))
        return self._dump(
            {
                "ok": True,
                "action": "list",
                "plans": [record.to_public_dict(include_recommendation=False) for record in records],
            }
        )

    def _find_latest_report(self) -> Path | None:
        reports_dir = self.workspace / "reports"
        if not reports_dir.exists() or not reports_dir.is_dir():
            return None

        candidates = [path for path in reports_dir.glob(_REPORT_GLOB) if path.is_file()]
        if not candidates:
            return None

        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return candidates[0]

    @staticmethod
    def _read_excerpt(path: Path, limit: int = 16000) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= limit:
            return text
        return text[:limit]

    def _select_working_dir(self, override: str | None) -> str:
        candidate = (override or "").strip()
        if candidate:
            return candidate
        return str(self.repo_root)

    def _build_plan_prompt(
        self,
        *,
        report_path: Path,
        report_excerpt: str,
        base_ref: str,
        upstream_ref: str,
        target_branch: str,
    ) -> str:
        return (
            "You are a senior merge advisor. Planning phase only.\\n"
            "Do not execute git commands and do not modify files.\\n\\n"
            f"Repository working directory: {self.repo_root}\\n"
            f"Base ref: {base_ref}\\n"
            f"Upstream ref: {upstream_ref}\\n"
            f"Target branch: {target_branch}\\n"
            f"Report file: {report_path}\\n\\n"
            "Analyze the report and produce a merge recommendation.\\n"
            "Required sections:\\n"
            "1. Overall recommendation\\n"
            "2. Conflict hotspots and risks\\n"
            "3. Suggested merge strategy\\n"
            "4. Concrete execution checklist for codex\\n"
            "5. Validation gates before push\\n"
            "6. Go/No-Go decision with rationale\\n\\n"
            "Report content:\\n"
            f"{report_excerpt}"
        )

    def _build_revise_prompt(
        self,
        *,
        record: MergePlanRecord,
        feedback: str,
        report_excerpt: str,
    ) -> str:
        return (
            "You are revising a merge recommendation. Planning phase only.\\n"
            "Do not execute git commands and do not modify files.\\n\\n"
            f"Plan ID: {record.plan_id}\\n"
            f"Base ref: {record.base_ref}\\n"
            f"Upstream ref: {record.upstream_ref}\\n"
            f"Target branch: {record.target_branch}\\n"
            f"Report path: {record.report_path}\\n\\n"
            "Previous recommendation:\\n"
            f"{record.recommendation}\\n\\n"
            "User feedback:\\n"
            f"{feedback}\\n\\n"
            "Generate a revised recommendation with the same required sections.\\n"
            "Include a short change log compared with the previous recommendation.\\n\\n"
            "Report content:\\n"
            f"{report_excerpt}"
        )

    def _build_execute_prompt(self, record: MergePlanRecord) -> str:
        return (
            "You are responsible for executing a real merge workflow.\\n"
            "You must perform all steps yourself in the repository.\\n"
            "Tasks:\\n"
            "1. Analyze the report and previous recommendation.\\n"
            "2. Fetch remotes, prepare branch, and merge upstream into target branch.\\n"
            "3. Resolve conflicts by editing code directly when needed.\\n"
            "4. Run minimal relevant verification before push.\\n"
            "5. Push results to origin target branch if verification passes.\\n"
            "6. If not safe, stop and explain exactly why.\\n\\n"
            f"Working directory: {record.working_dir or self.repo_root}\\n"
            f"Base ref: {record.base_ref}\\n"
            f"Upstream ref: {record.upstream_ref}\\n"
            f"Target branch: {record.target_branch}\\n"
            f"Report path: {record.report_path}\\n\\n"
            "Previous recommendation:\\n"
            f"{record.recommendation}\\n\\n"
            "Return a final summary with:\\n"
            "- merged files/conflicts\\n"
            "- verification commands and outcomes\\n"
            "- push result\\n"
            "- follow-up risks"
        )

    @staticmethod
    def _summarize(text: str, max_chars: int = 800) -> str:
        clean = (text or "").strip()
        if not clean:
            return ""
        lines = [line.strip() for line in clean.splitlines() if line.strip()]
        compact = "\\n".join(lines[:8])
        if len(compact) <= max_chars:
            return compact
        return compact[:max_chars].rstrip() + "..."

    @staticmethod
    def _extract_error_message(payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return "codex execution failed"

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _error(self, code: str, message: str, **extra: Any) -> str:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        }
        for key, value in extra.items():
            if value is not None:
                payload[key] = value
        return self._dump(payload)
