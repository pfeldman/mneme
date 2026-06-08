# ADR-0021: The `.praxis/` repository convention and git as shared memory

Status: Accepted (2026-06-08)

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS, and made git the
team-level shared memory: a `git pull` brings teammates the latest believed
knowledge, a `git push` shares a discovery, and there is no hosted backend.
ADR-0018 section 4 fixed the believed-vs-contested mapping onto the on-disk
layout (`.praxis/knowledge/` is believed, `.praxis/candidates/` is contested,
promotion is a human seed event via git merge) and named this ADR as the owner
of the layout in full. This ADR fixes the concrete `.praxis/` directory
convention: what files live where, what is committed versus gitignored, how the
per-machine append-only event log reconciles with git history, and how the
init operation scaffolds the tree.

On 2026-06-08 Pablo chose `.praxis/` over `praxis/` ("less clutter, more
serious"). The directory is a dotted convention at the repo root, one repo per
project (the ADR-0018 / ADR-0012 tenant boundary is the repo boundary), so a
project's entire knowledge state is one self-contained tree under version
control.

Two prior decisions are load-bearing here and constrain the layout:

- ADR-0001 made the append-only event log the source of truth: knowledge is
  never mutated in place, each observation is an immutable event, one file per
  event keyed by id, and believed state is a projection over the log. Git
  introduces a second history layer on top of that per-machine log; ADR-0018
  recorded the tension and left the reconciliation to this ADR.
- ADR-0012 set the file-per-event store layout (each event is its own file
  named by a content-addressable id, the filesystem rename is the commit point,
  event id collisions are impossible by construction) precisely so concurrent
  writers need no locks and lose no events. That same property is what makes
  candidate files safe to add concurrently across machines without a git merge
  conflict; the ADR-0012 layout is load-bearing for the anti-conflict rule
  below.

The deterministic init operation that materializes this tree is owned as a
plain CLI command by ADR-0019; the skills it installs and the package data it
unpacks are owned by ADR-0020. This ADR fixes what init produces on disk.

## Decision

### 1. The `.praxis/` directory layout.

A project's knowledge lives in a single tree at the repo root:

```
.praxis/
  config.yaml                       committed
  knowledge/
    <goal>.knowledge.yaml           committed
  candidates/
    <goal>/
      <candidate_id>.yaml           committed, one file per candidate
  runs/
    <timestamp>/                    gitignored, local, regenerable
  .praxisignore                     committed
```

- `config.yaml` holds the per-repo Praxis settings (tenant id default, schema
  version pin, adapter selection). It is committed so every teammate shares the
  same configuration.
- `knowledge/<goal>.knowledge.yaml` is one file per goal, the seeded / believed
  knowledge for that goal. This is the shared, committed, pulled-and-pushed team
  memory, validated against the active schema (`schema/knowledge.schema.json`).
- `candidates/<goal>/<candidate_id>.yaml` is the contested-candidate store: each
  agent-proposed risk or uncertainty (the ADR-0014 `CandidateEvent` projection)
  is its own file under a per-goal subdirectory, named by its candidate id.
- `runs/<timestamp>/` holds the raw per-machine append-only event log and the
  per-run artifacts (transcripts, intermediate reports) for one teach / regress
  / explore session.
- `.praxisignore` declares paths the Praxis operations must not read or write
  (analogous to `.gitignore`), committed so the exclusion set is shared.

### 2. Committed versus gitignored: the shared projection versus local logs.

The committed set is the believed-or-contested projection and the shared
config; the gitignored set is the raw per-machine log and run artifacts:

- **Committed and shared:** `config.yaml`, everything under `knowledge/`,
  everything under `candidates/`, and `.praxisignore`. These ARE the team-level
  store. They travel between teammates by `git pull` / `git push`.
- **Gitignored by default:** everything under `runs/`. The raw event logs are
  local to the machine that produced them and regenerable; they are the
  per-machine source of truth (ADR-0001) but they are NOT the shared artifact.
  Committing them would bloat history with machine-local transcripts and risk
  leaking unredacted run output. The init operation writes the `runs/` ignore
  line (decision 5).
- **Outside the tree, never committed:** `.praxis.secrets`, the root-level
  credentials file (decision 6), is not part of the `.praxis/` tree at all
  and is gitignored; secrets are a separate channel, not knowledge.

The committed knowledge and candidate files are the projection a teammate needs
to pull; the local `runs/` log is the evidence trail behind that projection on
the machine that wrote it.

### 3. The per-machine event log reconciles with git history; force-push is the forbidden mutation.

ADR-0001 keeps the append-only event log as the per-machine source of truth,
and git adds a second history layer. The two reconcile as follows, and neither
overrides the other:

- The per-machine append-only event log under `runs/` stays the local source of
  truth. The projection (`merge`) folds that log into the believed / contested
  state, exactly as in Phase 1 and Phase 2.
- The git history of the committed `.praxis/knowledge/` and `.praxis/candidates/`
  files IS the team-level append-only analog. Each commit that adds a goal file
  or a candidate file, or that merges a promotion, is one append to the shared
  history. The git log is the team-level audit trail; `git log` over `.praxis/`
  replaces the hosted dashboard ADR-0018 dropped.
- Because the committed files are an append-only analog, **a force-push that
  rewrites `.praxis/` history is the forbidden mutation.** Rewriting committed
  knowledge history is the git-level equivalent of the in-place event mutation
  ADR-0001 forbids: it erases provenance and makes a wrong assertion silent
  instead of traceable. Knowledge evolves by new commits (a new seed event, a
  decay event, a promotion merge), never by rewriting the past.

This is the resolution of the ADR-0018 negative consequence: the local event
log is the per-machine source of truth, the git history of the committed
projection is the shared append-only analog, and force-push to `.praxis/` is
prohibited.

### 4. Candidate files are one-file-per-id so concurrent adds never merge-conflict.

Each candidate is its own file (`candidates/<goal>/<candidate_id>.yaml`), keyed
by its candidate id, never a row in a shared mutable list. This is the git-level
realization of the ADR-0012 file-per-event store layout, and it is what makes
the shared candidate store safe under concurrent contribution:

- Two teammates (or two CI runs) that each discover a candidate for the same
  goal add two different files under the same per-goal directory. Git merges two
  added files in the same directory cleanly; there is no shared line both writers
  edited, so there is no text-level merge conflict.
- A single mutable `candidates.yaml` listing all candidates for a goal would
  reintroduce exactly the last-write-wins erasure ADR-0001 and ADR-0012 exist to
  prevent: two writers appending to the same list collide on the same lines and
  one discovery is lost or silently overwritten on merge.

The ADR-0012 source-independence contract carries over unchanged: each candidate
file records its own `source_id = agent_identity` (ADR-0009 / ADR-0014), so N
same-model writers adding N candidate files still count as ONE source under
ADR-0008's source-independence rule and the diversity-or-seed gate. One file per
id gives merge-safety; `source_id = agent_identity` keeps the safety from
becoming a self-promotion path.

The file id is per-observation, not the finding identity. A candidate file
is named by its observation event id (the ADR-0012 content-addressable
event id), NOT by the finding it reports. Two observations of the SAME
finding are two files that share the same structured `trigger`; they are
never merged into one file. Deduplication and corroboration of same-trigger
observations happen at the projection (the merge groups observations by
`trigger`), never by editing or overwriting a candidate file. The on-disk
store stays append-only and merge-safe; the judgment that two observations
are the same finding is made at projection time, not by a filename
collision.

### 5. The init operation scaffolds the tree, gitignores `runs/`, and installs the skills.

The deterministic `praxis init` command (a plain CLI command per ADR-0019)
materializes the convention in a fresh repo:

- It creates the `.praxis/` tree (`config.yaml`, `knowledge/`, `candidates/`,
  `runs/`, `.praxisignore`) with empty knowledge and candidate directories.
- It adds the gitignore lines for `.praxis/runs/` and `.praxis.secrets`
  (writing or appending the repo's `.gitignore`), so the raw logs and the
  local secrets file stay local without the user having to remember, and the
  secrets file can never be committed by accident (decision 6).
- It scaffolds the Praxis Claude Code skills into the project's
  `.claude/skills/` from the package data shipped in the wheel. The
  skill-distribution mechanism and the package-data packaging are owned by
  ADR-0020; this ADR fixes only that init is the operation that unpacks them and
  that it runs deterministically with no brain.

The init operation reads and writes the filesystem and reports; it does not
reason and needs no brain (ADR-0019 deterministic class).

### 6. Credentials and secrets live outside committed knowledge.

The credentials a teach, regress, or explore run needs to authenticate
against the live app are NOT knowledge and never enter `.praxis/`. They live
in a separate secrets channel, read at runtime to drive the browser and
never written into any committed file, candidate, knowledge file, or log:

- A gitignored `.praxis.secrets` file at the repo root (a `KEY=value` file,
  a sibling of `.praxis/`, never inside the committed tree), and / or
  environment variables (a CI secret in automation). An environment variable
  takes precedence over the file, so CI supplies credentials as runner
  secrets with no file and local use supplies them through the file.
- `praxis init` adds `.praxis.secrets` to the repo `.gitignore` (decision 5)
  so the file can never be committed by accident.

When a needed credential is absent, the behavior splits by surface
(ADR-0019). On the Claude Code skill surface the operation asks the user for
the missing key and offers the exact append command to add it, for example
`! echo "APP_USERNAME=<your-username>" >> .praxis.secrets`. On the console
surface (and in CI) the operation fails LOUDLY, naming the missing key and
how to set it (an environment variable or the secrets file), with a non-zero
exit and no interactive prompt, because there is no human to answer. A
secret value is never echoed back into the chat, stdout, or a log after it
is set. What knowledge records about authentication is only the ADR-0017
abstract `auth_state` posture (`authenticated` plus `scope`), never the
secret that produced it.

### Forbidden alternatives

DO NOT, in any Phase 3 ADR or implementation:

- Force-push or otherwise rewrite the git history of `.praxis/`. The committed
  knowledge and candidate files are the team-level append-only analog; rewriting
  that history is the git-level equivalent of the in-place event mutation
  ADR-0001 forbids.
- Apply last-write-wins on shared knowledge. Concurrent writes reconcile through
  the append-only store (ADR-0001) and clean git merges of added files, never by
  one writer overwriting another's committed knowledge.
- Store candidates in a single mutable file per goal. Candidates are
  one-file-per-id so concurrent adds merge cleanly; a shared list reintroduces
  the last-write-wins erasure ADR-0012 prevents.
- Commit raw run logs by default. Everything under `.praxis/runs/` is gitignored,
  local, and regenerable; committing it bloats history and risks leaking
  unredacted run output.
- Write secrets, tokens, or PII into any committed file under `.praxis/`. The
  adapter-boundary redaction rule (ADR-0009, ADR-0017) stays binding for every
  knowledge and candidate file the convention commits.

## Consequences

Positive:

- A project's entire knowledge state is one self-contained, version-controlled
  tree. Cloning the repo gets the believed knowledge and the contested
  candidates; `git pull` updates them; `git push` shares discoveries. No backend
  is in the loop, exactly the ADR-0018 posture.
- The committed-vs-gitignored split keeps the shared artifact small and the raw
  per-machine logs local. Teammates pull a projection, not gigabytes of run
  transcripts, and the leak surface of unredacted run output never enters git
  history.
- One-file-per-candidate makes concurrent contribution merge-safe at the git
  layer for free: two discoveries for the same goal are two added files, not a
  contested edit of one list. The ADR-0012 store property carries straight into
  the git workflow.
- `git log` over `.praxis/` is the team-level audit trail and replaces the hosted
  dashboard the reframe dropped. Provenance and the history of every promotion
  are inspectable with plain git.

Negative:

- Two history layers coexist: the per-machine event log under `runs/` and the
  git history of the committed projection. A contributor must understand that
  the local log is the per-machine source of truth and the git history is the
  shared analog; this ADR names the reconciliation but the dual model is more to
  hold than a single store.
- Gitignoring `runs/` by default means the raw evidence behind a committed
  projection does not travel with a push. A teammate who needs to audit the
  underlying events of a discovery must re-run or have the producing machine
  share the run out of band; the committed files carry provenance metadata but
  not the full raw log.
- One file per candidate inflates the on-disk and in-tree file count for a goal
  that accumulates many candidates, the same tradeoff ADR-0012 accepted for the
  event store. Acceptable for Phase 3; a future packed representation would have
  to preserve the merge-safety and per-source provenance this decision buys.

Invariants respected:

- `append-only-store-no-mutation`: the local event log under `runs/` is
  append-only per ADR-0001, and the committed git history of `.praxis/` is an
  append-only analog; force-push that rewrites it is forbidden, so knowledge
  evolves only by new commits, never by in-place rewriting.
- `concurrent-writes-lose-no-knowledge`: one-file-per-candidate keyed by
  candidate id means concurrent adds for the same goal merge cleanly with no
  shared edited line, the git-level realization of the ADR-0012 file-per-event
  guarantee; no discovery is lost on merge.
- `no-secrets-tokens-pii-in-knowledge`: every committed file under `.praxis/` is
  subject to the adapter-boundary redaction rule (ADR-0009, ADR-0017); raw run
  logs that might carry unredacted output are gitignored and never committed by
  default. Credentials live in a separate gitignored secrets channel
  (`.praxis.secrets` or environment variables, decision 6), read at runtime
  and never written into any committed file; knowledge records only the
  ADR-0017 `auth_state` posture.
- `tenant-scoping-prevents-leakage`: one repo per project is the tenant boundary
  (ADR-0018, refining the ADR-0012 single-tenant-by-contract placeholder); there
  is no cross-tenant store, so the `.praxis/` tree of one project never mixes
  with another's.
- `loud-and-traceable-over-silent-and-convenient`: the committed-vs-gitignored
  split, the force-push prohibition, and the one-file-per-candidate rule are
  named explicitly so a later contributor cannot quietly rewrite knowledge
  history, commit raw logs, or collapse candidates into a mutable list.

Invariants this ADR does NOT cover:

- `provenance-and-confidence-mandatory` and `first-oracle-must-be-seeded` for
  the teach operation that writes knowledge files: owned by ADR-0022. This ADR
  fixes where knowledge and candidate files land and how they reconcile with
  git; ADR-0022 fixes how the teach operation seeds a legitimate first oracle.
- `no-silent-success-when-app-broken` and the OK / REGRESSED / STALE report for
  the operations that read these files: owned by ADR-0023. This ADR fixes the
  on-disk convention; ADR-0023 fixes what regress and explore report over it.
- `no-self-corroboration-source-independence` for the autonomous CI candidate
  path that adds candidate files: owned by ADR-0024. This ADR fixes the
  one-file-per-id layout that keeps those adds merge-safe; ADR-0024 owns how the
  CI brain's writes stay source-independent.
- The brain-agnostic split that decides which operations touch this tree with or
  without a brain: owned by ADR-0019. This ADR fixes that init is deterministic;
  ADR-0019 owns the deterministic-vs-agentic classification.

## Relation to prior ADRs

Depends on ADR-0001 (append-only event log, Accepted): the committed git history
of `.praxis/` is the team-level append-only analog of the per-machine event log,
and force-push to `.praxis/` is the git-level form of the in-place mutation
ADR-0001 forbids. This ADR reconciles the two history layers rather than
replacing the per-machine log.

Depends on ADR-0012 (multi-writer concurrency contract, Accepted): the
one-file-per-candidate anti-conflict rule is the git-level realization of the
ADR-0012 file-per-event store layout, and the `source_id = agent_identity`
contract carries over so concurrent same-model writers stay one source.

Refines the ADR-0011 / ADR-0012 single-tenant-by-contract tenant-scoping
placeholder into one repo per project, the concrete form ADR-0018 chose for the
git-native reframe: the repo boundary is the tenant boundary, with no
cross-tenant store to isolate.

Realizes the ADR-0018 section 4 believed-vs-contested mapping on disk:
`.praxis/knowledge/` is the believed store and `.praxis/candidates/` is the
contested store, with promotion a human seed event via git merge per ADR-0018
and ADR-0005. Depends on ADR-0019 for the deterministic classification of the
init command and cross-references ADR-0020 for the skill distribution and
package data that init unpacks. Does not supersede any prior ADR.
