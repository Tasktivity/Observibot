# Contributing Connectors

> This document is a stub. A full contribution guide lands when the
> core team has shipped enough connectors to validate the pattern
> (see Phase 6 in [ROADMAP.md](../phases/ROADMAP.md)). For now this
> doc exists to signal intent, document the contract, and set
> expectations for potential contributors.

## Status

**Community connector contributions are not yet open for general
submission.** We're building out a few more connectors ourselves
first so that the `BaseConnector` contract, test harness, and review
process are well-proven before external developers build on them.

What we're doing right now:
- Shipping connectors for the next 3–5 platforms (AWS, GCP, GitLab,
  Fly.io, Neon — priorities subject to change based on research and
  usage feedback)
- Refining the `BaseConnector` ABC based on what each new connector
  teaches us
- Building a connector test harness that validates a connector works
  against a real target
- Documenting the review process, code style, and CI gates

What we'll need before opening contribution:
- At least 5 core-team connectors covering database, deployment, and
  source-code dimensions
- A connector test harness contributors can run locally
- A clear review rubric so contributors know what "accepted" looks
  like
- Enough infrastructure stability that connector PRs don't have to
  be rebased on core changes every week

Track progress in [ROADMAP.md](../phases/ROADMAP.md) — the connector
ecosystem work happens in Phase 6.

## The Connector Contract (Preview)

A connector is the module that teaches Observibot about one external
system — a database, a deployment platform, a cloud provider, a
source-code host. Every connector honors the same contract, and that
contract is what allows agents to consume the system model without
caring which platforms produced it.

Every connector implements four methods via `BaseConnector`:

- `discover() -> SystemFragment` — return what the platform contains.
  Tables, services, topology, configuration, whatever the platform
  exposes. The discovery engine merges fragments from all connectors
  into a unified SystemModel.
- `collect_metrics() -> MetricSnapshot` — return current metric
  values. Called every monitoring cycle.
- `get_recent_changes(since) -> list[ChangeEvent]` — return deploys,
  migrations, or configuration changes since the given timestamp.
- `health_check() -> HealthStatus` — verify connectivity and
  permissions. Surfaces actionable error messages when misconfigured.

## Non-Negotiable Constraints

Any connector, core or community, honors these. A PR that doesn't
will be rejected on principle before code review.

1. **Read-only.** A connector never writes to the connected system.
   No INSERT, UPDATE, DELETE, DDL, API writes, or configuration
   changes of any kind.
2. **Credentials in environment.** Never hard-code, never log,
   never put in config files.
3. **Graceful failure.** If the connector can't reach the platform
   or lacks permissions, it must return a useful `HealthStatus`
   error rather than crashing the process.
4. **Rate limit respect.** If the platform has rate limits, the
   connector honors them. ETag/conditional requests, backoff on
   429, circuit breaker on repeated failures.
5. **Timeout discipline.** Every external call has a timeout. No
   blocking the monitor loop.
6. **No PII in logs.** Platform responses often contain customer
   data. Logging must redact or exclude sensitive fields.
7. **Fingerprintable output.** Discovery output must be stable
   across calls so drift detection works.

## Testing Requirements

Every connector contribution will need to pass the full three-tier
testing standard (see
[../TESTING_STANDARDS.md](../TESTING_STANDARDS.md)):

- Unit tests with mocked platform responses
- A contract test that makes one real API call and validates the
  response shape
- Tier 0 generality coverage — any connector feature that looks
  like a pattern (enum detection, sensitive-column classification,
  etc.) must be tested against a synthetic schema in
  `tests/fixtures/synthetic_schemas.py`, not only against the
  contributor's own test environment

## Licensing

Observibot is Apache 2.0. Contributed connectors will be licensed
the same way. If a platform's API terms of service prohibit the
kind of read access a connector needs, we won't accept the
contribution regardless of code quality.

## What Would Make a Great Early Contribution

Even though general contribution isn't open yet, the following are
genuinely useful and we'll look at them:

- **Bug reports** for existing connector behavior, especially edge
  cases in discovery or metrics collection
- **Test coverage** for existing connectors, particularly edge-case
  schemas or unusual platform configurations
- **Documentation PRs** for
  [../architecture/CONNECTORS.md](../architecture/CONNECTORS.md)
  where the existing connector interface is under-documented

## Questions?

Open a GitHub issue with the `question` label. We'd rather answer
questions early than accept a PR that doesn't fit.
