---
name: Expense CLI Roadmap Structure
description: Milestones, feature dependencies, and deferred items for the expense_cli roadmap (as of 2026-04-05)
type: project
---

ROADMAP.md was created at C:\Users\Anand\Desktop\expense_cli\ROADMAP.md. It is the canonical backlog for remote agents.

Milestones and key dependencies:

- v0.2 Insights: analytics.py (new module) + cli.py. Cards: category summary (A), month-over-month trends (B), new counterparty detection (C). v0.2-A must exist before B and C.
- v0.3 Recurrence: detect_subscriptions in analytics.py (A), fixed/variable split using v0.3-A (B). Depends on v0.2-A.
- v0.4 Export: exporter.py (new module). Independent — no analytics dependency.
- v0.5 Budgeting: budget.py (new module), config subcommand (A), budget status command (B). v0.5-B depends on v0.5-A.
- v0.6 Tags: storage migration + CLI only. Independent.

**Why:** user wants remote agents to pick up cards autonomously without clarification. Roadmap is written as self-contained feature cards with acceptance criteria, files to touch, and test requirements.

**How to apply:** when asked "what should be built next", check this dependency chain. v0.2-A is the only unblocked analytics card. v0.4-A and v0.6-A are always independently pickable.
