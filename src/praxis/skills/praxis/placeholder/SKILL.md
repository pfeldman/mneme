---
name: praxis-placeholder
description: Placeholder Praxis skill shipped as package data to exercise the ship-then-scaffold path. Real skills (praxis:teach, praxis:regress, praxis:explore) land in Wave 2. Not intended to be invoked.
---

# Praxis placeholder skill

This is a placeholder under the `praxis` skill namespace. It exists so the
novel round trip that ADR-0020 decision 7 introduces is exercisable before the
real skills are authored:

    pip install praxis-qa  ->  praxis init  ->  .claude/skills/

That is: a skill that ships inside the wheel as package data, scaffolded into a
consuming project's `.claude/skills/` by `praxis init`.

This file does nothing on its own. The real local-brain skills replace it in
Wave 2:

- `/praxis:teach` (ADR-0022): the human-in-the-loop authoring loop.
- `/praxis:regress` (ADR-0023): the dual-surface regression check.
- `/praxis:explore` (ADR-0023): the off-happy-path hunt.

Do not invoke this skill. It carries no behavior.
