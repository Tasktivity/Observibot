# Contributing to Observibot

Thanks for your interest. Before anything else, please read
[docs/VISION.md](docs/VISION.md) — it's the project's north star and
the grounding for everything we decide about scope, priorities, and
contribution.

## Current State of Contribution

Observibot is open source and designed to grow through community
contribution. But we're being deliberate about *when* and *how* we
open specific surfaces to external contributors, because premature
contribution creates technical debt that hurts the project more than
it helps.

**What's open now:**
- Bug reports and reproductions
- Documentation improvements
- Test coverage for existing features
- Small, focused fixes to existing functionality

**What's coming soon:**
- Connector contributions — see
  [docs/contributing/CONNECTORS.md](docs/contributing/CONNECTORS.md)
  for the timeline and design constraints. We're building out a few
  more connectors ourselves first so the pattern is well-proven
  before we invite external ones.

**What's coming later (Phase 7):**
- Agent contributions — see
  [docs/contributing/AGENTS.md](docs/contributing/AGENTS.md). Agents
  are a bigger surface and need a stable `BaseAgent` contract first.

## Before You Open an Issue or PR

1. Read [VISION.md](docs/VISION.md). If your proposed change doesn't
   strengthen the connector layer, the system model, or the agent
   ecosystem, it may be off-mission.
2. Skim [ROADMAP.md](docs/phases/ROADMAP.md) to see where we are.
3. For non-trivial changes, open an issue first to discuss the
   approach before writing code. This saves both of us time.

## How to Set Up a Dev Environment

See [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Testing Requirements

All changes must follow the three-tier testing standard documented in
[docs/TESTING_STANDARDS.md](docs/TESTING_STANDARDS.md):

1. Unit tests (pytest with mocks) — fast, cover logic and edge cases.
2. Contract tests for any external API — validate real responses.
3. Live end-to-end verification against a running instance — no
   exceptions for changes that touch the monitoring or chat pipeline.

Pattern-based changes (anomaly detection, insight generation, schema
heuristics, data-quality safeguards) also require **Tier 0** — the
generality firewall. The TESTING_STANDARDS doc explains what that
means and what's required.

## Code Style

- Python: ruff clean. Run `ruff check src/` before pushing.
- Type hints required on public functions.
- No LLM-generated code in PRs without human review.
- No AI-written commit messages without human review either; we want
  real explanations of *why* a change was made.

## Reporting Security Issues

Please don't open public issues for security vulnerabilities. Email
the maintainer directly — contact info in the repo's top-level
[README.md](README.md). We'll respond within one business day.

## License

By contributing, you agree that your contributions will be licensed
under the Apache 2.0 license (same as the project).
