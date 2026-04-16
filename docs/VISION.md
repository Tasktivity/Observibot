# Vision

> **This document is the north star.** When any future decision, doc, or
> scope question comes up, it's grounded here. If something in the
> roadmap, README, or architecture docs contradicts this file, this file
> wins and the other doc gets updated.

## The Short Version

Observibot builds a live model of your production system — schema,
topology, code, and runtime state — and keeps it continuously current
so specialized agents can reason about it.

The core product is the model and the layer that builds it. Agents —
ours, community-contributed, or fully private — are first-class
consumers of that model, not features of it.

## The Longer Version

Modern production systems span source code on GitHub or GitLab,
databases on Supabase or Neon or RDS, deployment platforms like
Railway or Fly or Vercel, and cloud infrastructure across AWS, GCP,
and Azure. Understanding what a system *is* — not just what it's
doing right now, but how it's structured, what the tables mean, what
the services do, how they change over time — usually requires a human
to stitch together information from a dozen dashboards, read a lot of
code, and hold the whole picture in their head.

Observibot automates that stitching. It connects to each layer of a
running system, continuously builds a structured, machine-readable
model of what it finds, and keeps that model current as the system
evolves. The model isn't just raw schema metadata — it captures
meaning: this table holds orders, this column is sensitive, this
metric is a counter not a gauge, this service owns this data.

On top of that model, specialized agents reason about the system
through different lenses. The first agent monitors the system for
performance and anomalies — the role a human SRE would fill. The
next will model security threat surfaces. A cost agent, a compliance
agent, an on-call agent — all become practical to build because the
hard work of understanding the system was already done by the layer
underneath.

## What We're Building, In Three Layers

**1. The connector layer.** Each external system — source code host,
database, deployment platform, cloud provider — has a connector. The
connector knows how to discover what exists, collect relevant metrics,
and surface recent changes. Connectors are shared infrastructure,
usable by any agent built on the platform. The more we support, the
more valuable the platform becomes for everyone building on it.

**2. The system model.** The unified representation of a running
system, built from connector output and interpreted into meaning
agents can use. Tables with their relationships and semantics.
Services with their dependencies and ownership. Metrics with their
units and baselines. Changes with their timing and attribution. This
is the product's real moat.

**3. The agents.** Specialized reasoners that consume the system
model and produce insights. We ship some (SRE first, security next).
The community can contribute more. Teams can build private agents
that encode their own expertise and never leave their infrastructure.
All three modes are first-class design constraints, not
aspirations.

## The Initial Wedge

Our first validation target is indie developers and small teams
running production applications on PaaS stacks — Supabase + Railway,
Neon + Fly, PlanetScale + Render, and similar combinations. They're
underserved by enterprise tools that are too expensive and require
too much configuration, and they tend to adopt new developer tools
fast. They're a good first audience because their systems are small
enough to prove we can understand them end-to-end.

But the architecture is not built for that audience alone. Indie
teams increasingly use AWS, GCP, and Azure too. The same
mid-sized team that uses Supabase for their app database might run
analytics workloads on BigQuery or background jobs on ECS. A product
that stops at the PaaS boundary stops being useful exactly when it
gets interesting. Everything we build has to work for the indie wedge
today and scale to the broader landscape as connector coverage grows.

## Principles

These are invariants. Every decision we make — architecture,
roadmap, UX — is checked against these. If a principle and a proposed
feature conflict, the principle wins.

**Autonomous discovery.** Never require manual configuration to
understand a system. If the platform can infer something from source
code, schema, metrics, or topology, it infers it. Users correct
mistakes through conversation, not by editing YAML.

**Semantic fidelity over raw coverage.** A table isn't just columns;
it's meaning. The platform's value to agents scales with the quality
of its interpretation, not the volume of raw data it surfaces. If
we have to choose between indexing more metrics and better
understanding the ones we have, we choose understanding.

**Read-only, always.** We observe. We never write to production
systems. This is non-negotiable and enables every other use case.
A platform that can write is a platform that can break things; a
platform that can only read can be trusted by security-sensitive
teams, regulated industries, and anyone else.

**Local-first.** Your data stays on your infrastructure. No
telemetry. No phone-home. The project is designed so that a
fully-offline deployment behind a corporate firewall works exactly
the same as a public cloud deployment.

**Every platform, eventually.** Connectors are shared infrastructure.
We will ship the ones that prove the pattern. The community will
extend the ones we can't justify building ourselves. The platform
stays worth contributing to because the substrate it extends is
valuable and the contribution contract is clear.

**Agents are first-class, in three modes.** Core agents (shipped with
the platform), community agents (contributed upstream or distributed
externally), and private agents (built and run only by their
authors). All three are architectural constraints on the core API —
not side-use-cases.

**Ground truth is versioned and attributable.** Every semantic claim
the platform makes about a system can be traced (where did this come
from?), corrected (this is wrong, here's the right answer), and
versioned (what did the platform believe when this insight fired?).

## Commercial Intent

Observibot is Apache 2.0 and open-core. The full platform — every
connector, every agent, every API — lives in this repository. We
intend to offer managed hosting for teams who don't want to run it
themselves, which is how we plan to sustain long-term development.
The open-source tier is not a trial or a crippled version; it's the
same codebase we run in production.

## What This Document Is Not

**Not a feature list.** Features go in the roadmap. This document
defines the trajectory; the roadmap defines what we're building right
now.

**Not a positioning statement.** The README handles public
positioning, using language adapted for the audience. This document
is for us.

**Not locked.** When the vision evolves, this document evolves. Every
change should be a deliberate decision and ideally reviewed the same
way architecture decisions are. But evolution is expected — we'll
know more in six months than we do today.

## Decision Grounding

Use this document as the reference for scope questions. When a piece
of work is proposed:

- Does it strengthen the connector layer, the system model, or the
  agent ecosystem? If not, what is it for?
- Does it hold the seven principles? If a principle needs to bend,
  that's a bigger decision than the feature.
- Does it serve the current wedge *and* preserve the ability to
  scale to broader audiences? Work that only serves the wedge is
  fine; work that locks us into the wedge is not.
- Does it favor semantic fidelity over raw coverage?

If all four answers are yes, the work likely belongs. If any answer
is no, the work probably needs rescoping or a real discussion.
