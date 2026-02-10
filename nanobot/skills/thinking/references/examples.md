# Thinking File Examples

All examples below are entries written to `memory/thinking.md`.

## Example 1: Debugging Production Latency

```text
## [2026-02-10 10:20] API latency regression
Problem: API p95 rose from 200ms to 2s after release.
Goal: Restore p95 below 300ms without rollback.
Constraints: Keep service online during diagnosis.
Known facts: DB CPU spiked after deployment.

Thought 1: Compare query profile between previous and current release.
Thought 2: Query count increased 12x on one endpoint.
Thought 3: New serializer introduced N+1 relationship loading.
Thought 4: Add eager loading and reduce selected columns.

Conclusion: N+1 query regression is root cause.
Next action: Ship eager-loading fix and add query-count regression test.
Confidence: high
```

## Example 2: Architecture Choice With Branching

```text
## [2026-02-10 10:45] v1 architecture decision
Problem: Choose architecture for a 3-month launch.
Goal: Maximize delivery speed while preserving future scale options.
Constraints: 5 engineers, moderate initial traffic.
Known facts: Team has limited ops bandwidth.

Thought 1: Two options are modular monolith or microservices.
Branch A (Modular monolith): Faster build, easier debugging, simpler deploy.
Branch B (Microservices): Better isolation, higher coordination overhead.
Comparison: Branch A has lower delivery risk for current team size and timeline.

Conclusion: Modular monolith best fits v1 constraints.
Next action: Define module boundaries and extraction criteria now.
Confidence: medium
```

## Example 3: Revision After New Evidence

```text
## [2026-02-10 11:10] Production payment failures
Problem: Payment calls fail only in production.
Goal: Restore payment success rate quickly and safely.
Constraints: TLS verification must remain enabled.
Known facts: Error logs include intermittent connection failures.

Thought 1: Initial hypothesis is outbound firewall block.
Thought 2: Check 443 egress policy and routing.
Revision of Thought 1: Firewall is open; TLS trust chain is broken.
Impact: Shift diagnosis from network policy to cert store and chain.
Thought 3: Install missing intermediate certificate and retest.

Conclusion: Root cause is incomplete TLS trust chain.
Next action: Add startup health check for certificate chain validity.
Confidence: high
```

## Pattern Summary

- Start each task with one entry header in `memory/thinking.md`.
- Keep one task per entry.
- Record revisions and branches explicitly.
- End every entry with conclusion, next action, and confidence.
