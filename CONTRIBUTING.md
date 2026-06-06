# Contributing

## Principles (read docs/01 and docs/06 first)
1. Store invariants, not coordinates.
2. Every assertion carries provenance + confidence (ADR-0004).
3. The store is append-only (ADR-0001). No update/delete of knowledge.
4. Core stays runtime-agnostic; runtime code lives in adapters (ADR-0003).
5. The oracle is sacred: never let a single-source signal become a "believed"
   success criterion.

## Dev setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,browser-use]"
pytest
ruff check .
```

Or verify everything (deps + pytest + ruff + mypy + the offline experiment) in one
command:
```bash
bash verify.sh
```

To run the experiment LIVE with a Claude Code subscription (no API key), see
`experiments/ui-mutation/LOCAL_RUN.md` or invoke the `/run-mneme-experiment` skill.

## Definition of done for any change to the model/schema
- `schema/examples/*` still validate.
- New assertion fields propagate provenance + confidence.
- An ADR is added if a structural decision was made.
