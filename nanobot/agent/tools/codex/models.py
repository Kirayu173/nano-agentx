"""Data models for codex merge orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MergePlanStatus = Literal["planned", "revised", "executed", "failed"]


@dataclass(slots=True)
class ExecutionResult:
    """Execution outcome recorded for a merge plan."""

    ok: bool
    summary: str
    at_ms: int
    thread_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "atMs": self.at_ms,
            "threadId": self.thread_id,
            "usage": self.usage,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionResult":
        return cls(
            ok=bool(data.get("ok")),
            summary=str(data.get("summary", "")),
            at_ms=int(data.get("atMs", 0) or 0),
            thread_id=data.get("threadId"),
            usage=dict(data.get("usage") or {}),
            error=data.get("error"),
        )


@dataclass(slots=True)
class MergePlanRecord:
    """Persisted merge advisory plan."""

    plan_id: str
    status: MergePlanStatus
    created_at_ms: int
    updated_at_ms: int
    base_ref: str
    upstream_ref: str
    target_branch: str
    working_dir: str
    report_path: str
    report_excerpt: str
    recommendation: str
    confirmation_token_hash: str
    revision: int = 0
    last_feedback: str | None = None
    plan_thread_id: str | None = None
    plan_usage: dict[str, Any] = field(default_factory=dict)
    execution: ExecutionResult | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "planId": self.plan_id,
            "status": self.status,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
            "baseRef": self.base_ref,
            "upstreamRef": self.upstream_ref,
            "targetBranch": self.target_branch,
            "workingDir": self.working_dir,
            "reportPath": self.report_path,
            "reportExcerpt": self.report_excerpt,
            "recommendation": self.recommendation,
            "confirmationTokenHash": self.confirmation_token_hash,
            "revision": self.revision,
            "lastFeedback": self.last_feedback,
            "planThreadId": self.plan_thread_id,
            "planUsage": self.plan_usage,
            "execution": self.execution.to_dict() if self.execution else None,
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MergePlanRecord":
        execution_raw = data.get("execution")
        execution = (
            ExecutionResult.from_dict(execution_raw)
            if isinstance(execution_raw, dict)
            else None
        )
        return cls(
            plan_id=str(data.get("planId", "")),
            status=str(data.get("status", "planned")),
            created_at_ms=int(data.get("createdAtMs", 0) or 0),
            updated_at_ms=int(data.get("updatedAtMs", 0) or 0),
            base_ref=str(data.get("baseRef", "origin/main")),
            upstream_ref=str(data.get("upstreamRef", "upstream/main")),
            target_branch=str(data.get("targetBranch", "main")),
            working_dir=str(data.get("workingDir", "")),
            report_path=str(data.get("reportPath", "")),
            report_excerpt=str(data.get("reportExcerpt", "")),
            recommendation=str(data.get("recommendation", "")),
            confirmation_token_hash=str(data.get("confirmationTokenHash", "")),
            revision=int(data.get("revision", 0) or 0),
            last_feedback=data.get("lastFeedback"),
            plan_thread_id=data.get("planThreadId"),
            plan_usage=dict(data.get("planUsage") or {}),
            execution=execution,
        )

    def to_public_dict(self, *, include_recommendation: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plan_id": self.plan_id,
            "status": self.status,
            "revision": self.revision,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "base_ref": self.base_ref,
            "upstream_ref": self.upstream_ref,
            "target_branch": self.target_branch,
            "working_dir": self.working_dir,
            "report_path": self.report_path,
            "has_execution": self.execution is not None,
        }
        if self.execution is not None:
            payload["execution"] = self.execution.to_dict()
        if include_recommendation:
            payload["recommendation"] = self.recommendation
        return payload
