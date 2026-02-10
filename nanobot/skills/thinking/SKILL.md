---
name: thinking
description: Structured step-by-step reasoning for complex, ambiguous, or multi-stage tasks. Use when planning, debugging, architecture trade-offs, or decomposing unclear scope. For complex tasks, persist the reasoning process to memory/thinking.md before giving the final recommendation.
---

# Thinking

Use this skill to reason in explicit, numbered steps. Do not rely on any special reasoning tool.

For complex tasks, file logging is mandatory: write and maintain the reasoning in `memory/thinking.md` before sending the final answer.

## Mandatory File Logging

Apply this requirement when the task is multi-step, uncertain, or high impact.

- Use exactly one lightweight file: `memory/thinking.md`
- Do not create deep directories
- Create a new entry for each substantial task
- Update the same entry as you revise thoughts or explore branches
- End the entry with conclusion and next action before final reply

Skip file logging only for simple one-step questions.

## Core Workflow

1. Open or create `memory/thinking.md`.
2. Append a new task entry with timestamp and topic.
3. Define problem, goal, constraints, and known facts.
4. Write numbered thoughts with clear cause-effect links.
5. Record revisions and branches explicitly when direction changes.
6. End with conclusion, next action, and confidence.
7. Send the user answer aligned with the saved conclusion.

## Output Template

Use this shape inside `memory/thinking.md` for each entry:

```text
## [YYYY-MM-DD HH:MM] <topic>
Problem:
Goal:
Constraints:
Known facts:

Thought 1: ...
Thought 2: ...
Thought 3: ...

Revision of Thought N: ... (optional)
Branch A: ... (optional)
Branch B: ... (optional)
Comparison: ... (optional)

Conclusion:
Next action:
Confidence: low|medium|high
```

## Revision Pattern

When a previous thought is wrong or incomplete, explicitly correct it:

```text
Revision of Thought N: ...
Impact: ...
```

Then continue from the corrected state instead of silently changing direction.

## Branch Pattern

When two or more approaches are viable, branch briefly and then compare:

```text
Branch A (approach name): ...
Branch B (approach name): ...
Comparison: trade-offs, risks, cost, time.
Decision: choose one and explain why.
```

## File Hygiene

- Keep entries concise and practical
- Prefer appending new entries over rewriting old history
- Preserve older entries for traceability
- Use plain text headings and short lines for easy scanning

## Scope Control

- Increase steps if the task remains unclear.
- Reduce steps if the answer becomes obvious.
- Prefer testable reasoning over speculative detail.

## Stop Condition

Stop the chain when all are true:

- The root cause or decision is clear.
- Main risks and trade-offs are explicit.
- The recommended action is specific and executable.

## References

- For revision and branching tactics: `references/advanced.md`
- For full worked examples: `references/examples.md`
- For copy-ready entry template: `references/template.md`
