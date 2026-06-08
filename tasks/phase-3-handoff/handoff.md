# Handoff prompt - Praxis, end of Phase 2

Copy this entire document into the next Claude Code session (or a fresh
agent) as the opening prompt. It carries the smallest complete brief
needed to pick up the work without re-reading every file. Self-contained
for a cold reader.

---

## What Praxis is

A shared **operational knowledge** layer for QA agents: success / failure
oracles, risks, uncertainties, all carrying provenance + confidence +
status, decoupled from any procedure. Agents read believed knowledge,
regenerate their own steps, and write observations back through an
append-only event log. Believed state is a projection over events.
Single sources never self-corroborate; promotion requires a seeded
human/spec OR two independent sources with diverse evidence types
(ADR-0005, ADR-0008).

The project was originally called `mneme`; ADR-0009 renamed it to
`praxis`. GitHub repo URL is still `github.com/pfeldman/mneme` (rename
on GitHub is a separate manual step Pablo will do when ready).

## Where you are landing

- Repo: clone `github.com/pfeldman/mneme`. Branch: `main` (default).
- Working tree: `cd mneme; bash verify.sh` must end ALL GREEN before
  any new work.
- Last commits on `main` (newest first): merge Phase 2 into integration,
  Phase 2 user-facing docs + CHANGELOG entry, 5 feature merges
  (real-app-sut, exploration-reward, candidate-persistence,
  recency-decay, multi-writer), Accept ADRs 0011-0017, 7 ADRs authored,
  ADR-0010 Phase 1 verdict CONTINUE.
- Also locally: `claude/mneme-phase-1` preserved as a checkpoint of the
  pre-merge state.

## The contract you must honor

`AGENTS.md` has the five non-negotiables. Read it first. Summary:

1. Invariants, not coordinates. No selectors as durable knowledge.
2. Provenance + confidence are mandatory on every assertion.
3. Append-only store; believed state is a projection, never
   last-write-wins.
4. Runtime-agnostic core: `model / store / merge / oracle / runner`
   have zero browser deps.
5. The oracle is sacred. A single source never self-corroborates.

Plus, per `docs/06-risks-and-failure-modes.md`: a confidently-wrong
oracle is worse than no memory. Brittleness is loud; bad knowledge is
silent. When in doubt, choose the option that makes a wrong assertion
LOUD and TRACEABLE over silent and convenient.

## Where Phase 2 stopped (state of the world)

**Done (everything below is on `main`):**

- ADRs 0001-0010 from prior phases plus seven new Accepted ADRs
  (0011-0017) sealing Phase 2 architectural decisions.
- Five Phase 2 features implemented in parallel worktrees, all merged,
  all tests green:
  - **multi-writer concurrency** (ADR-0012): per-tenant append-only file
    store layout, source_id = agent_identity (model + prompt lineage),
    five-scenario adversarial harness wired into verify.sh.
  - **recency-decay** (ADR-0013): projection-time event-driven
    demotion, explicit DecayEvent on status flips, observed_app_version
    primary anchor + wall-clock secondary, unidirectional decay.
  - **candidate-persistence** (ADR-0014): sibling `CandidateEvent` type
    in the store, default `contested`, promotion via diversity-or-seed,
    `praxis review` CLI surfaces the queue.
  - **exploration-reward** (ADR-0015): pre-registered formula `reward =
    (resolved_uncertainties + alpha * new_unique_candidate_risks) /
    budget_tokens`, observability-only, paired Goodhart attacks doc,
    random-walk baseline required.
  - **real-app-sut** (ADR-0016 + 0017): Conduit goal slate (login,
    publish_article, follow_user, favorite_article, edit_article),
    docker bring-up gated behind env var, additive `auth_state`
    projected field with adapter-boundary redaction (no
    tokens/cookies/PII).
- Plain English user docs at `docs/phase-2-features/` (5 feature pages
  plus index README).
- CHANGELOG.md entry for "Phase 2 (ADRs 0011 through 0017)".
- 8 commits on `main` since Phase 1 closed.
- `bash verify.sh` ALL GREEN, ~250+ tests total.

**Deferred to Phase 1.5 (NOT done, NOT in main):**

- Stagehand adapter + dedicated benchmark + head-to-head experiment.
- One paid API-key happy-path run confirming tokens/$ margin (the
  subscription path produces actions + wall time as proxy; an API-key
  run produces the dollar number).
- Auditor protocol as offline oracle-stress with `refuted` status
  promotion via diversity-or-seed.

Phase 1.5 has no ADRs yet. When you take it, each item gets its own
ADR before code.

## The Phase 3 reframe (decided by Pablo, 2026-06-08)

Earlier drafts of Phase 3 listed governance/RBAC, hosted multi-tenant
shared memory, dashboards, web UI, pricing/GTM. Pablo rejected the SaaS
framing on 2026-06-08:

> "no es un framework local, subido a pypi y ya? realmente necesitamos
> un saas?"

The new Phase 3 vision is **library + git, no SaaS**:

- Praxis ships as a PyPI package.
- A repo's knowledge lives under `.praxis/` (dotfile convention; Pablo
  picked it 2026-06-08 over `praxis/` for "less clutter, more
  serious").
- Shared memory is git, not a backend. `git pull` brings the team the
  latest knowledge; `git push` shares discoveries.
- Multi-tenancy is "one repo per project".
- Permissions are git permissions.
- Conflicts are git merge conflicts.
- Auditability is git log.

## Phase 3 work items (no ADRs yet)

These are decisions to be sealed in ADRs before implementation. Pablo
confirmed the vision; you write the ADRs. Order matters; later items
depend on earlier ones.

1. **PyPI packaging + publish.** `pip install praxis` works. CLI
   entry point `praxis`. Versioning policy. Wheels for major
   platforms. Lockfile / setup.cfg / pyproject.toml details.

2. **`.praxis/` convention.** What lives in `.praxis/` per repo:
   - `.praxis/config.yaml` (app name, base_url, agent identity hints)
   - `.praxis/knowledge/<goal>.knowledge.yaml` (the seeded + promoted
     knowledge; goes to git)
   - `.praxis/candidates/<goal>/<id>.yaml` (contested candidates
     awaiting promotion; goes to git)
   - `.praxis/runs/<timestamp>/` (per-run event logs; possibly
     gitignored by default, or summarized to a single rolling log)
   - `.praxisignore` (which paths under the SUT to skip)
   - A `.praxis init` command that scaffolds these.

3. **`praxis teach "<intencion>"` command.** The missing piece for
   non-engineers. Given a natural-language intent ("quiero probar que
   puedo crear una campania"), Claude opens the app via Playwright,
   explores until it can perform the happy path, asks the user
   interactively when blocked (credentials, role, where a button is),
   and writes the resulting goal YAML. This is the
   "teach-Claude-the-app" loop.

   Subdecisions:
   - Interactive prompt protocol (Claude asks, user replies in CLI).
   - When does `teach` end (happy path observed? user confirms? N
     minutes? budget?).
   - YAML scaffold the agent emits + UX for the user to edit before
     commit.
   - How teach interacts with existing goals (extend? warn? refuse?).

4. **`praxis regress` without args.** Today the runner needs
   `--goal X`. The Phase 3 ergonomic is "regress everything in
   `.praxis/knowledge/`" by default. Output should be an aggregated
   report a non-engineer can read (X goals OK, Y regressions, Z
   uncertainties resolved).

5. **GitHub Action.** A reusable action `praxis-action@v1` that runs
   regress on every PR or release tag, opens a draft PR with new
   candidates surfaced during E-mode exploration. This is what makes
   the "team shares knowledge via git" loop close on its own.

6. **Landing page + docs site.** Minimal. The story for a non-engineer
   evaluating Praxis. Examples (testapp, Conduit, one real OSS app).
   No analytics, no signup, no SaaS funnel.

Each item is an ADR before code. The PyPI publish itself may not need
an ADR (mechanical packaging); decide that case-by-case.

## What to do next (priority order)

If Pablo asks "what next", default to this sequence:

1. **Confirm the Phase 3 reframe with Pablo verbally** before writing
   ADRs. He shifted vision on 2026-06-08; the next session should
   re-anchor on "library + git, no SaaS" and the `.praxis/` directory
   name before any architectural commitment.

2. **Author Phase 3 ADRs in numeric order** starting at 0018. The same
   /task + workflow pattern used for ADRs 0011-0017 works here. Pablo
   approved that pattern.

3. **Implement Phase 3 features in parallel via the same worktree
   pattern** Pablo used for Phase 2. Five Phase 2 features were
   implemented by five parallel agents, then merged sequentially; same
   approach is viable for Phase 3.

4. **Phase 1.5 in parallel or after Phase 3 launch**. The items
   (Stagehand adapter, paid API-key run, auditor offline harness) are
   not blocking Phase 3 but should not be abandoned. They were
   deferred by ADR-0011 with a placeholder.

## How Pablo and you communicate

- Spanish in chat. Short messages, one idea per turn. Do NOT invent
  numbers; verify before asserting.
- Code, commits, comments, docs: English. ASCII only. The hook
  `~/.claude/hooks/block-em-dashes.py` will reject em-dashes, en-dashes,
  smart quotes, ellipsis, Unicode arrows. Use commas, parentheses,
  hyphens with spaces, three ASCII dots, straight quotes, ASCII arrows
  (`->`).
- Pablo is a senior contributor, not the implementer. He approves plans
  and validates direction. You are the engineer; you implement.
- Pablo is a non-engineer for product evaluation. When summarizing
  results, write plain English: no jargon, define internal terms inline
  the first time, anchor every claim with a concrete example.
- One commit = one logical change. Never commit without explicit Pablo
  approval ("dale", "manda", "commit", "/commit").
- Never push to remote without explicit Pablo approval. The repo's
  `origin/HEAD` is now `main` (changed 2026-06-08).
- No Co-Authored-By trailer on commits in this repo.

## Files that have the rest of the truth

Read in this order if anything above is ambiguous:

1. `AGENTS.md` (the contract).
2. `docs/01-vision-and-thesis.md` (what we are building and why).
3. `docs/06-risks-and-failure-modes.md` (where this goes wrong).
4. `docs/phase-2-features/README.md` (plain-English index of the 5
   Phase 2 features just landed).
5. `CHANGELOG.md` (Phase 2 entry dated 2026-06-08).
6. `docs/adr/README.md` (ADR index, 0001 through 0017 Accepted).
7. `docs/adr/0011-phase-2-scope-and-deferrals.md` (the Phase 2 umbrella;
   names what was deferred to Phase 1.5 and Phase 3).
8. `docs/adr/0010-phase-1-regression-recall-verdict.md` (Phase 1
   verdict CONTINUE with concrete numbers: memory 0.75 vs cold_readme
   0.25 vs cold 0.12 at n=3, all 6 gates pass).
9. The five feature ADRs 0012-0017 (the ground truth for the
   implementations now on main).

## Useful commands

```bash
# Run everything end-to-end (must be ALL GREEN):
bash verify.sh

# Just the unit tests (fast):
source .venv/bin/activate && python -m pytest -q

# Start the test app on a free port:
source .venv/bin/activate && python experiments/ui-mutation/testapp.py --port 8765

# Use the CLI on a real project:
praxis init --app my-saas --base-url https://staging.example.com
praxis learn login --from-file login.knowledge.yaml
praxis regress --goal login --budget-tokens 5000
praxis explore --goal checkout --happy-path /cart /cart/apply /orders
praxis review            # Phase 2 candidate queue
praxis status

# Phase 2 adversarial multi-writer harness:
python -m pytest tests/test_multi_writer.py tests/test_multi_writer_harness.py -v

# Phase 2 Conduit bring-up (gated behind env var, 30min ceiling):
PRAXIS_RUN_CONDUIT_BRINGUP=1 python -m pytest tests/test_conduit_bringup.py -v
```

## Acknowledgements + caveats the next agent should carry

- The Phase 2 implementations were done by five parallel agents in
  isolated worktrees, then merged sequentially with conflict resolution
  by another agent. The verify.sh suite is green end-to-end, but the
  individual feature integration paths are still "tested in isolation +
  smoke-passed together". A real cross-feature stress run (multi-writer
  + decay + candidates all firing in one experiment) has NOT been done.
  That belongs to Phase 3 hardening or Phase 1.5 cross-validation.
- The Conduit Docker bring-up test exists but is gated behind an env
  var; nobody has run it end-to-end against a real Conduit deploy yet.
- The exploration-reward formula is observability-only; if Phase 3 ever
  feeds it back into agent state (to bias toward higher-reward paths),
  ADR-0015's Goodhart attacks doc becomes load-bearing. Re-read it
  before any such change.
- The `.praxis/` directory convention does NOT exist yet in code.
  Phase 3 ADR-0018 (whichever ADR documents the convention) is the
  place to land it. Today the CLI does `praxis init --app X --base-url
  Y` which writes a config; the directory layout for storing knowledge
  / candidates / runs is what needs decision.
- The `praxis teach` command does NOT exist. It is the central Phase 3
  ADR. Interactive prompt-and-reply between Claude and the user is the
  new behavior that has no precedent in Phase 0-2.
- The Phase 1 verdict (ADR-0010) is real and Accepted. Do NOT hedge
  about it. Memory beats cold_readme on the regression-recall harness;
  the moat claim survived the falsifier.
- Branch `claude/mneme-phase-1` is preserved as a local-only checkpoint
  of the pre-Phase-2-merge state. Do NOT delete it without Pablo
  approval. The five worktree branches `worktree-wf_912cfd1e-074-*`
  were already deleted; their commits remain reachable through the
  merge history on `main`.
- The GitHub repo URL is still `github.com/pfeldman/mneme`. The rename
  to `praxis` is a manual step Pablo will do when he is ready.

Good luck. Read `AGENTS.md` first.

<!-- /copy posted: 2026-06-08T10:31:18Z -->

<!-- /copy posted: 2026-06-08T10:35:27Z -->
