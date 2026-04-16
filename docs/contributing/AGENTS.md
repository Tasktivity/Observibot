# Contributing Agents

> This document is a stub. A full contribution guide lands in
> Phase 7 (see [ROADMAP.md](../phases/ROADMAP.md)), after the core
> team has shipped a second agent and the `BaseAgent` contract is
> stable. For now this doc exists to signal intent and document the
> design constraints that shape current work.

## Status

**Community agent contributions are not yet open.** Agents are a
bigger contribution surface than connectors — they involve their
own analysis logic, chat tools, prompt templates, and severity
taxonomies. We need the `BaseAgent` ABC to be battle-tested before
external developers build agents on top of it.

What we're doing right now (through Phase 7):
- Building a second core agent (security threat modeling) so that
  what "an agent" is becomes concrete beyond the SRE case
- Formalizing the `BaseAgent` ABC through the process of building
  that second agent
- Deciding the agent distribution model (separate process vs plugin
  class vs config-only) based on what the second agent teaches us
- Establishing per-agent chat tool registration so agents don't
  step on each other's chat surfaces

What we'll need before opening contribution:
- At least two core-team agents running concurrently in the same
  deployment, proving the multi-agent architecture works
- A stable `BaseAgent` ABC with versioned API
- A pattern for how agents declare their capabilities, severity
  taxonomies, and chat tools
- Documentation for the three coexistence modes (core, community,
  private) so contributors know which one their agent targets

Track progress in [ROADMAP.md](../phases/ROADMAP.md) — the agent
ecosystem work happens in Phase 7.

## Three Coexistence Modes

Observibot is designed to support three kinds of agents
simultaneously. This shapes every architectural decision we make.

**Core agents** — shipped with the platform, maintained by the core
team. The SRE agent today. Security threat modeling next. Others will
follow as we identify domains where the platform provides enough
leverage to justify maintaining an agent long-term.

**Community agents** — contributed upstream by external developers,
reviewed by the core team, bundled with the platform. These are
analogous to community connectors: broadly useful, general-purpose,
shareable.

**Private agents** — built by a single team for their own use, never
shared. A financial services firm's compliance agent. A healthcare
company's HIPAA audit agent. A platform team's proprietary runbook
automation. These agents encode team-specific expertise or sensitive
business logic that the team has no intention of open-sourcing.

All three modes use the same `BaseAgent` interface. The distribution
mechanism is what differs (upstream PR / community registry / local
package).

## Non-Negotiable Constraints (Preview)

When agent contribution opens, every agent — core, community, or
private — must honor these:

1. **Read-only reach.** Agents consume the system model, metrics,
   and store. They never write to production systems. They never
   configure connectors to take write actions. If an agent needs to
   take action (restart a service, send a notification), it does
   so through the alerting / output layer, never directly.
2. **Insight provenance.** Every insight an agent emits carries the
   agent's identifier in the `source` field. No anonymous insights.
3. **Evidence-backed output.** Agents that make claims about the
   system attach structured evidence — not just narrative. An SRE
   insight that says "847 idle connections" attaches the query
   result showing 847 idle connections.
4. **Severity taxonomy declaration.** An agent declares its severity
   scale up front. SRE uses info/warning/critical. Security will
   use CVSS-style. Cost will use quantitative impact. The UI
   respects the agent's declared scale.
5. **Chat tool isolation.** Agents register their own chat tools.
   Tools are namespaced by agent so two agents registering a tool
   called `query_posture` don't collide.
6. **Bounded cost.** An agent must respect configurable LLM and
   query budgets. Agents that cannot operate within bounds are
   disabled, not silently expensive.

7. **Generic by construction.** Agents must pass the Tier 0
   generality firewall (see [../TESTING_STANDARDS.md](../TESTING_STANDARDS.md)).
   No hardcoding to specific customer schemas, specific table
   names, or specific deployment topologies. Agents observe
   patterns and apply them; they don't encode one team's data
   model.
8. **Transparent failure.** If an agent can't do its job (LLM
   unavailable, connector broken, cost exceeded), it says so
   visibly. Silent degradation erodes trust in every agent, not
   just the one that failed.

## What Would Make a Great Future Community Agent

When agent contribution opens, these are the kinds of contributions
we're likely to accept:

- **Agents with a clear, well-bounded domain** — "cost optimization"
  or "compliance posture monitoring" rather than "general
  improvements."
- **Agents that consume the existing system model without requiring
  new connectors** — connector work is Phase 6; agent work is
  Phase 7. An agent that depends on connectors that don't exist yet
  is premature.
- **Agents that bring domain expertise the core team doesn't have.**
  We know SRE. We're learning security. An agent for regulatory
  compliance in a specific industry (finance, healthcare,
  government) would be genuinely additive.
- **Agents with tight cost discipline.** Anything that could run up
  LLM bills during a quiet week is a non-starter.

## Private Agent Path (Preview)

For teams building agents they don't plan to share: the path will
probably look like installing Observibot, installing your private
agent as a separate Python package, and letting Observibot's plugin
system discover it at startup. Details firm up in Phase 7. Your
agent's code stays entirely on your infrastructure; nothing phones
home.

## Licensing

Observibot is Apache 2.0. Contributed agents will be licensed the
same way. Private agents are, by definition, licensed however the
team building them chooses.

## Questions?

Open a GitHub issue with the `question` label, or follow the
[ROADMAP.md](../phases/ROADMAP.md) for updates.
