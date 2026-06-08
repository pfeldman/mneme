"""Packaged Claude Code skills shipped inside the wheel (ADR-0020 decision 7).

This package carries the Praxis Claude Code skills as package data. `praxis
init` scaffolds them into a consuming project's `.claude/skills/` directory so a
`pip install praxis-qa` followed by `praxis init` gives the project the local
brain surface with no manual skill copying.

The real skills (`/praxis:teach`, `/praxis:regress`, `/praxis:explore`) are
authored in Wave 2. This package ships at least one placeholder skill now so the
ship-then-scaffold round trip is exercisable before then.

The skill files themselves are `SKILL.md` markdown under a `praxis/` namespace;
this `__init__.py` exists only to make the tree an importable package so
`importlib.resources` resolves it identically from a wheel and from the src tree.
"""
