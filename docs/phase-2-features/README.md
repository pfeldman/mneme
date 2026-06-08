# Phase 2 features

Phase 1 ended with a verdict: the memory arm beat the steelmanned cold-readme baseline by a wide margin on every pre-registered gate, so the operational-knowledge moat survives. The decision was CONTINUE, with caveats. Phase 2 is the follow-up: take the same machinery off the toy app it was tuned against, let multiple agents write into the shared memory at once without poisoning it, age out knowledge that nobody is re-confirming, persist agent hunches across runs so a human can act on them, and add a single hidden number that says whether exploration is paying for its tokens. Five features ship under five ADRs.

Each link below points to a feature doc written for non-engineers. The ADRs they reference live under `docs/adr/`.

## Features

1. [Multi-writer concurrency](01-multi-writer.md) (ADR-0012). Lets many QA agents append to the same shared memory at once, without losing notes and without letting identical agents fake agreement to promote a bad finding.

2. [Recency decay](02-recency-decay.md) (ADR-0013). Marks knowledge "stale" when no agent has re-confirmed it within a pre-registered number of app versions or days, and writes a visible audit record every time something ages out.

3. [Candidate persistence](03-candidate-persistence.md) (ADR-0014). Saves exploring-agent hunches across runs as "contested" so a human can review them, while preventing any single agent from voting itself into the trusted set.

4. [Exploration reward](04-exploration-reward.md) (ADR-0015). A single hidden number per exploration run that scores useful new knowledge per token spent. The agent never sees it, so it cannot game it.

5. [Real-app SUT: Conduit + auth_state](05-real-app-sut.md) (ADR-0016, ADR-0017). Moves the experiment off the in-repo toy app and onto Conduit, a public Medium-clone, and adds a small `auth_state` field to the schema that records login posture without ever storing credentials.

## Reference

- Phase 1 verdict: [ADR-0010](../adr/0010-phase-1-regression-recall-verdict.md)
- Phase 2 scope: [ADR-0011](../adr/0011-phase-2-scope-and-deferrals.md)
