#!/usr/bin/env python3
"""Generate a daily diff/conflict report between origin/main and upstream/main."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import tempfile
import time
from pathlib import Path


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{detail}")
    return result


def run_text(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    return run(cmd, cwd=cwd, check=check).stdout.strip()


def run_with_retry(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    attempts: int = 3,
    initial_delay_s: float = 2.0,
    backoff: float = 2.0,
) -> subprocess.CompletedProcess[str]:
    """Run command with retry/backoff."""
    delay_s = initial_delay_s
    last_result: subprocess.CompletedProcess[str] | None = None

    for attempt in range(1, attempts + 1):
        result = run(cmd, cwd=cwd, check=False)
        if result.returncode == 0:
            return result

        last_result = result
        if attempt < attempts:
            print(
                f"Command failed (attempt {attempt}/{attempts}), retrying in {delay_s:.1f}s: "
                f"{' '.join(cmd)}"
            )
            time.sleep(delay_s)
            delay_s *= backoff

    assert last_result is not None
    stderr = last_result.stderr.strip()
    stdout = last_result.stdout.strip()
    detail = stderr or stdout or f"exit code {last_result.returncode}"
    raise RuntimeError(
        f"Command failed after {attempts} attempts: {' '.join(cmd)}\n{detail}"
    )


def fetch_ref(ref: str) -> None:
    if "/" not in ref:
        return
    remote, branch = ref.split("/", 1)
    run_with_retry(
        ["git", "fetch", "--no-tags", remote, branch],
        attempts=3,
        initial_delay_s=2.0,
        backoff=2.0,
    )


def top_commits(base_ref: str, target_ref: str, limit: int) -> list[str]:
    out = run_text(
        ["git", "log", "--oneline", "--no-merges", f"{base_ref}..{target_ref}", f"-n{limit}"],
        check=False,
    )
    return [line for line in out.splitlines() if line.strip()]


def merge_conflict_probe(base_ref: str, upstream_ref: str) -> tuple[bool, list[str], str]:
    with tempfile.TemporaryDirectory(prefix="upstream-merge-probe-") as tmpdir:
        worktree = Path(tmpdir) / "worktree"
        run(["git", "worktree", "add", "--detach", str(worktree), base_ref], check=True)

        merge_result = run(
            ["git", "merge", "--no-commit", "--no-ff", upstream_ref],
            cwd=worktree,
            check=False,
        )
        has_conflict = merge_result.returncode != 0
        conflicted = run_text(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=worktree,
            check=False,
        )
        conflict_files = [line for line in conflicted.splitlines() if line.strip()]

        # Always cleanup merge state/worktree.
        run(["git", "merge", "--abort"], cwd=worktree, check=False)
        run(["git", "reset", "--hard", "HEAD"], cwd=worktree, check=False)
        run(["git", "worktree", "remove", "--force", str(worktree)], check=False)

        output_parts: list[str] = []
        if merge_result.stdout.strip():
            output_parts.append(merge_result.stdout.strip())
        if merge_result.stderr.strip():
            output_parts.append(merge_result.stderr.strip())
        merge_output = "\n".join(output_parts).strip()
        return has_conflict, conflict_files, merge_output


def to_code_block(lines: list[str]) -> str:
    if not lines:
        return "_None_"
    return "```text\n" + "\n".join(lines) + "\n```"


def resolve_default_output_dir() -> Path:
    """Resolve nanobot's current configured workspace report directory."""
    try:
        from nanobot.config.loader import load_config

        workspace = load_config().workspace_path
    except Exception:
        workspace = Path.home() / ".nanobot" / "workspace"
    return workspace / "reports"


def resolve_output_path(output_arg: str | None, now_cst: dt.datetime) -> Path:
    """Build output file path and enforce timestamp in report filename."""
    stamp = now_cst.strftime("%Y%m%d-%H%M%S")
    default_name = f"upstream-main-conflict-report-{stamp}.md"

    if not output_arg:
        return resolve_default_output_dir() / default_name

    output = Path(output_arg).expanduser()
    if output.suffix.lower() == ".md":
        # Keep caller's prefix but force timestamp in final filename.
        return output.with_name(f"{output.stem}-{stamp}{output.suffix}")
    return output / default_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-commits", type=int, default=30)
    args = parser.parse_args()

    fetch_ref(args.base_ref)
    fetch_ref(args.upstream_ref)

    base_sha = run_text(["git", "rev-parse", args.base_ref])
    upstream_sha = run_text(["git", "rev-parse", args.upstream_ref])
    merge_base = run_text(["git", "merge-base", args.base_ref, args.upstream_ref])

    ahead_behind = run_text(
        ["git", "rev-list", "--left-right", "--count", f"{args.base_ref}...{args.upstream_ref}"]
    )
    ahead_str, behind_str = ahead_behind.split()
    ahead = int(ahead_str)   # base-only commits
    behind = int(behind_str)  # upstream-only commits

    base_only = top_commits(args.upstream_ref, args.base_ref, args.max_commits)
    upstream_only = top_commits(args.base_ref, args.upstream_ref, args.max_commits)
    diff_stat = run_text(
        ["git", "diff", "--stat", "--find-renames", f"{args.base_ref}..{args.upstream_ref}"],
        check=False,
    ).splitlines()
    name_status = run_text(
        ["git", "diff", "--name-status", "--find-renames", f"{args.base_ref}..{args.upstream_ref}"],
        check=False,
    ).splitlines()

    has_conflict, conflict_files, merge_output = merge_conflict_probe(
        args.base_ref,
        args.upstream_ref,
    )

    now_utc = dt.datetime.now(dt.UTC)
    now_cst = now_utc.astimezone(dt.timezone(dt.timedelta(hours=8)))
    changed_files = len([line for line in name_status if line.strip()])

    report_lines = [
        "# Upstream Main Diff & Conflict Report",
        "",
        f"- Generated (UTC): `{now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}`",
        f"- Generated (UTC+8): `{now_cst.strftime('%Y-%m-%d %H:%M:%S %Z')}`",
        f"- Base ref: `{args.base_ref}` (`{base_sha[:12]}`)",
        f"- Upstream ref: `{args.upstream_ref}` (`{upstream_sha[:12]}`)",
        f"- Merge base: `{merge_base[:12]}`",
        "",
        "## Summary",
        "",
        f"- Base ahead of upstream: **{ahead}** commit(s)",
        f"- Base behind upstream: **{behind}** commit(s)",
        f"- Files changed (`{args.base_ref}..{args.upstream_ref}`): **{changed_files}**",
        f"- Merge conflicts if merging `{args.upstream_ref}` into `{args.base_ref}` now: **{'YES' if has_conflict else 'NO'}**",
        "",
        f"## Commits only in {args.base_ref} (top {args.max_commits})",
        "",
        to_code_block(base_only),
        "",
        f"## Commits only in {args.upstream_ref} (top {args.max_commits})",
        "",
        to_code_block(upstream_only),
        "",
        f"## File Diff (`{args.base_ref}..{args.upstream_ref}`)",
        "",
        to_code_block(name_status),
        "",
        f"## Diff Stat (`{args.base_ref}..{args.upstream_ref}`)",
        "",
        to_code_block(diff_stat),
        "",
        "## Conflict Files (simulated merge)",
        "",
        to_code_block(conflict_files),
    ]

    if merge_output:
        report_lines.extend(
            [
                "",
                "## Merge Probe Output",
                "",
                "```text",
                merge_output,
                "```",
            ]
        )

    output_path = resolve_output_path(args.output, now_cst)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"has_conflict={'true' if has_conflict else 'false'}\n")
            fh.write(f"conflict_count={len(conflict_files)}\n")
            fh.write(f"report_path={output_path.as_posix()}\n")

    print(f"Report written to {output_path}")
    print(f"has_conflict={has_conflict}, conflict_count={len(conflict_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
