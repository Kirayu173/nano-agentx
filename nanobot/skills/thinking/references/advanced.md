# Advanced File-Driven Thinking Patterns

This skill uses one lightweight reasoning log file: `memory/thinking.md`.

## Entry Lifecycle Pattern

1. Append a new entry header with timestamp and topic.
2. Fill `Problem`, `Goal`, `Constraints`, and `Known facts`.
3. Add numbered thoughts as reasoning progresses.
4. Append revisions and branches when needed.
5. End with `Conclusion`, `Next action`, and `Confidence`.

Do not keep reasoning only in transient chat. Persist it in the file first.

## Revision Pattern

Use revision when a prior thought is wrong or incomplete.

```text
Thought 2: Bottleneck is network latency.
Revision of Thought 2: Profiling shows DB lock contention dominates.
Impact: Shift effort from retries to query/index changes.
```

Use revision when:

- New evidence contradicts earlier conclusions.
- A hidden assumption is exposed.
- Scope was misunderstood.
- A factual error appears in earlier steps.

## Branching Pattern

Use branching when multiple approaches are truly viable.

```text
Branch A (Caching): Fast reads, invalidation complexity.
Branch B (Indexing): Predictable behavior, migration cost.
Comparison: A is faster short-term, B has lower long-term ops risk.
Decision: Choose B.
```

Branch only if it improves decision quality. Avoid trivial branches.

## Convergence Pattern

Always resolve branches to a single decision:

```text
Conclusion: ...
Next action: ...
Confidence: medium
```

Do not leave branches open-ended in the final entry.

## Scope Adjustment Pattern

Treat step count as guidance, not a contract.

- Expand if uncertainty remains high.
- Shrink if the answer is already clear.
- Stop when extra steps no longer improve the decision.

## File Hygiene Rules

- Keep `memory/thinking.md` append-only for task entries.
- Use short, readable sections instead of long prose blocks.
- Keep each entry focused on one task.
- Do not create nested thinking directories.

## Quick Checklist

- Reasoning was written to `memory/thinking.md`.
- Problem and goal are explicit.
- Revisions are called out clearly.
- Branches are compared and converged.
- Final action is specific and executable.
