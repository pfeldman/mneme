# ADR-0023: praxis regress and explore - dual surface, aggregate default, break-vs-drift report

Status: Accepted (2026-06-08)

## Context

ADR-0009 shipped the two runner modes the regression-recall falsifier
consumes. R-mode (`src/praxis/runner/regression.py`) is the pre-deploy
check: it reads believed `success_signals` and `failure_signals` and
computes pass / fail per goal, with the auditor scenarios explicitly NOT
an input because feeding them in would leak ground truth into the memory
under test (ADR-0009 section 6, the closed leak path). E-mode
(`src/praxis/runner/exploration.py`) hunts off the happy path, reads risks
plus uncertainties plus the failure-signal watch-list, and logs
`off_path_fraction` as the floor against E-mode collapsing into R-mode.
ADR-0010 cleared the gate provisionally, so both modes earned their place
by a real number.

Two facts about the current surface drive this ADR. First, the CLI today
takes a goal: a regress or explore invocation runs ONE goal at a time, so
checking a whole repo means scripting a loop and the per-goal results
arrive as raw runner output, not as one report a non-engineer can read.
Second, ADR-0019 fixed that the agentic operations (`teach`, `regress`,
`explore`) are delivered on a dual surface over the same engine: a console
CLI (test-style red / green, a process exit code, what CI runs and what a
user scripts) and a Claude Code skill (the same engine plus the local
Claude brain triaging interactively). ADR-0019 section 4 named the dual
surface and deferred the report contract that distinguishes the two
surfaces to this ADR.

The core value over a plain test runner is not that a goal failed; it is
WHY it failed. A red test tells you something is wrong; it does not tell
you whether the APP broke (a real regression, file a bug) or whether the
app CHANGED on purpose and the stored knowledge is now outdated (drift,
update the knowledge). That break-vs-drift distinction is the product. A
plain assert library cannot make it because it has no model of believed
knowledge to compare the live behavior against; Praxis does. This ADR owns
the dual surface for regress and explore, the default-all aggregate
report, the OK / REGRESSED / STALE verdict that carries the break-vs-drift
distinction, the loud-failure contract, the skill-adds-triage behavior,
the inheritance of the ADR-0009 no-auditor-input rule, and the per-goal
budget rule. CI wiring of these operations is owned by ADR-0024.

## Decision

### 1. Both operations are delivered on the dual surface from ADR-0019.

The regress and explore operations each ship on TWO surfaces over the SAME
engine, per ADR-0019 section 4:

- A **console CLI** (`praxis regress`, `praxis explore`): test-style
  output, a process exit code, no chat. This is what a team wires into its
  own CI (ADR-0024) and what a user scripts. Driven by whichever brain the surface
  selects (the CI API-key brain in automation, the local brain when invoked
  from a session).
- A **Claude Code skill** (`/praxis:regress`, `/praxis:explore`): the same
  engine plus the local Claude brain, which triages failures interactively
  and proposes the next step. This is the free local path on the user's
  subscription (ADR-0019 section 3).

Same body, same store reads and writes, same operation semantics. The only
differences are which brain drives the run and whether the output is a
process exit code or an interactive triage. Both surfaces produce the same
verdict for the same goal and live app; the skill adds triage ON TOP of the
verdict, it does not change the verdict. When a run must authenticate, it
reads the app credential from the ADR-0021 secrets channel (`.praxis.secrets`
or an environment variable, never from knowledge); a missing credential
follows the ADR-0021 ask-or-fail behavior (the skill asks the user, the
console and CI fail loudly).

### 2. With no `--goal`, the operation runs every believed goal and emits one aggregate report.

The current per-goal-required invocation stays available: `praxis regress
--goal <name>` runs exactly that goal. The new default is aggregate. With
no `--goal`, the regress operation runs every goal under
`.praxis/knowledge/` (the seeded / believed set per ADR-0018 section 4) and
emits ONE aggregated report. The explore operation defaults the same way:
no `--goal` hunts off-happy-path across every believed goal. The aggregate
report is the non-engineer surface: a per-goal verdict line plus a single
roll-up, readable without knowing the runner internals. The report is
written as markdown under `.praxis/runs/<timestamp>/` (the gitignored,
regenerable run dir owned by ADR-0021); it is a report, never a mutation of
committed knowledge.

### 3. The per-goal verdict is OK / REGRESSED / STALE, and REGRESSED vs STALE is the break-vs-drift distinction.

Each goal in an aggregate or single-goal regress run gets exactly one of
three verdicts:

- **OK**: the believed `success_signals` were observed and no
  `failure_signal` fired. The app still behaves as the knowledge says.
- **REGRESSED**: a believed `success_signal` is now absent or a
  `failure_signal` fired. The APP broke; this is a real bug. The verdict
  names the goal and the specific signal that flipped.
- **STALE**: the live behavior diverges from the believed knowledge in a
  way consistent with an intentional app change rather than a defect (for
  example, the success path moved but a healthy equivalent is observed, or
  the goal's anchored `observed_app_version` is behind the live app per the
  ADR-0013 decay model). The APP changed on purpose; the KNOWLEDGE is
  outdated and should be re-seeded, not the app fixed.

REGRESSED versus STALE is the break-vs-drift distinction and the core value
over a plain test runner: REGRESSED routes to "file a bug against the app",
STALE routes to "update the knowledge". The engine emits the verdict
together with the evidence (the signal that flipped, the version anchor)
so the routing is traceable, not a guess. The verdict reuses the believed
signals and the ADR-0013 decay anchor; it activates no new schema field.

### 4. A regression is LOUD: non-zero exit, named goal, named signal.

A REGRESSED verdict on any goal makes the whole run fail loudly. On the
console surface the process exits non-zero, names every REGRESSED goal, and
names the signal that flipped for each. The aggregate roll-up never reports
"mostly green" in a way that buries a single regression: one REGRESSED goal
fails the run regardless of how many goals are OK. This is the docs/06
asymmetry made operational - a confidently-wrong silent pass is the failure
mode this project exists to avoid, so the regress operation chooses a loud,
named, non-zero failure over a convenient green summary. A goal that ERRORS
(the run could not reach a verdict: the app would not load, the adapter
threw) is NOT silently skipped and NOT counted as OK; it is surfaced as its
own loud non-OK outcome that also fails the run.

### 5. The skill surface adds break-vs-drift triage and proposes the next step.

On the Claude Code skill surface, after the engine produces the verdicts,
the local Claude brain triages each non-OK goal and proposes the next step:
for a REGRESSED goal, "this looks like the app broke, here is the signal
that flipped, file a bug"; for a STALE goal, "this looks like the app
changed on purpose, the knowledge is outdated, here is the proposed
re-seed". The triage is advisory: it surfaces the break-vs-drift routing
and a proposed action for a human, and it NEVER mutates committed knowledge
on its own. Updating a STALE goal's knowledge is a human seed event
(ADR-0005), realized as a `/praxis:teach` re-seed or a candidate the human
reviews and merges (ADR-0018 section 4, ADR-0022); the regress skill
proposes it, the human commits it. The console surface has no triage step;
it emits the verdict and the exit code, and the same routing is recoverable
from the named signal in its output.

### 6. R-mode still excludes auditor scenarios as inputs.

The regress operation is R-mode (ADR-0009). Its inputs stay believed
`success_signals` and `failure_signals` only. Auditor scenarios are NOT an
input on either surface, exactly as ADR-0009 section 6 closed the leak:
feeding the auditor's ground-truth scenarios into the memory under test
would let the regress operation pass by reading the answer key rather than
by re-checking believed knowledge against the live app. The dual surface,
the aggregate default, and the skill triage do NOT reopen that path; the
auditor protocol stays an offline oracle-correctness check, and a
`refuted` status driven by auditor input remains Phase 1.5 work
(ADR-0009 section 6, ADR-0011 section 3, ADR-0018 section 5).

### 7. Per-goal budget allocation.

Aggregate runs allocate a token-and-wall-time budget PER GOAL, not one pool
the goals race for. Each goal gets its own budget slice so one expensive or
pathological goal cannot starve the rest of an aggregate run, and a goal
that exhausts its slice without reaching a verdict surfaces as a loud
budget-exhaustion ERROR for that goal (per decision 4) rather than silently
truncating the run. The per-goal budget keeps the aggregate report complete
and bounded: every goal is attempted within its own slice and every
non-verdict is named.

### 8. Explore writes contested candidate files, grouped by trigger in the report; the skill triages inline.

The explore operation writes any candidate risks and uncertainties it finds
as contested candidate files under `.praxis/candidates/` (ADR-0021,
ADR-0014), one file per observation, on both surfaces. In the candidate
report, observations are GROUPED by their structured `trigger`: each finding
appears ONCE, annotated with how many times it was observed and how many
DISTINCT `source_id`s attest to it. N observations from the same
`agent_identity` count as ONE source (ADR-0008), never as N duplicate
entries, and a finding earns `believed` only by diversity-or-seed
(ADR-0005, ADR-0014). On the console surface, the explore operation writes
the files and exits. On the Claude Code skill surface, it ALSO surfaces what
it just found and triages it inline with the user, who can promote, leave,
or discard a fresh finding in the same session, applied immediately as the
corresponding review action. The `praxis review` command remains the
surface for the AGGREGATE contested queue, the candidates the user was not
present to triage (a teammate's runs, the autonomous CI runs, the history);
the inline skill triage handles only what the current explore run just
produced.

### Forbidden alternatives

DO NOT, in any surface or implementation of regress or explore:

- Feed auditor scenarios into the regress operation as an input. R-mode
  reads believed `success_signals` and `failure_signals` only; the ADR-0009
  leak path stays closed on both surfaces.
- Silently skip a goal that errors. A goal that cannot reach a verdict is
  surfaced as a loud non-OK ERROR that fails the run; it is never dropped
  and never counted as OK.
- Emit a "mostly green" aggregate that hides a single regression. One
  REGRESSED goal fails the whole run with a non-zero exit and a named goal
  plus signal; the roll-up may not bury it.
- Auto-mutate committed knowledge on a STALE verdict without a human
  confirm. A STALE verdict routes to a proposed re-seed; the actual update
  is a human seed event (ADR-0005) via teach or a reviewed candidate merge,
  never an automatic in-place edit (ADR-0001).

## Consequences

Positive:

- A non-engineer gets one readable aggregate report instead of scripting a
  per-goal loop and reading raw runner output. The default-all behavior is
  the ergonomic surface ADR-0018's library posture needs.
- The break-vs-drift verdict is the concrete value over a plain test
  runner. REGRESSED routes to "file a bug", STALE routes to "update the
  knowledge", and the routing is backed by the named signal that flipped,
  not by a human guessing why a test went red.
- A regression cannot pass silently. The loud, named, non-zero failure
  contract makes the docs/06 "bad knowledge is silent" asymmetry impossible
  to hit through a convenient green summary.
- The same engine serves both the free local skill (with triage) and the
  CI console surface (exit code), so the operation logic is not forked
  across surfaces.

Negative:

- Two surfaces is two paths to keep working for each operation. Every change
  to regress or explore must be exercised both as a Claude Code skill and as
  a console CLI run; this ADR carries the dual-surface test burden ADR-0019
  flagged.
- The REGRESSED-vs-STALE classification is a judgment the engine can get
  wrong: a real regression mislabeled STALE would route a bug to "update the
  knowledge" and quietly accept broken behavior. The mitigation is that the
  verdict ships with its evidence and a STALE verdict never auto-mutates
  knowledge; a human confirms the re-seed, so a mislabel is caught at the
  human-confirm gate rather than silently committed.
- Per-goal budget slicing means a large believed set multiplies the
  aggregate run cost (and, on the CI brain, the API-key spend). The budget
  is bounded per goal, but the total scales with the number of goals;
  trimming an aggregate run to a subset stays a `--goal`-scoped invocation.

Invariants respected:

- `no-silent-success-when-app-broken`: a REGRESSED or ERROR goal fails the
  run loudly with a non-zero exit and a named goal plus signal; no aggregate
  roll-up reports green over a hidden regression.
- `loud-and-traceable-over-silent-and-convenient`: every verdict ships with
  the evidence that produced it (the flipped signal, the version anchor);
  errors and budget exhaustion are named, not swallowed; the break-vs-drift
  routing is recoverable from the output.
- `operational-knowledge-not-procedures`: both operations re-check believed
  operational knowledge (success / failure signals, risks, uncertainties)
  against the live app; they assert what counts as success, not a
  click-by-click procedure.
- `append-only-store-no-mutation`: a STALE verdict never edits committed
  knowledge in place; the knowledge-update path is a human seed event
  appended via teach or a reviewed candidate merge (ADR-0005, ADR-0001,
  ADR-0014).

Invariants this ADR does NOT cover:

- The CI wiring of these operations - how a team invokes the console
  commands in its own CI and gates on the exit code: owned by ADR-0024.
  This ADR fixes what the operations report; ADR-0024 fixes how a team
  wires them into CI.
- `first-oracle-must-be-seeded` and the teach re-seed protocol that a STALE
  verdict proposes: owned by ADR-0022 (the teach skill) and ADR-0005 (the
  seed rule). This ADR proposes the re-seed; it does not own the seed
  protocol.
- The `.praxis/runs/<timestamp>/` run-dir layout the markdown report is
  written into, and its gitignored / regenerable status: owned by ADR-0021.

## Relation to prior ADRs

Depends on ADR-0019 (brain pluggability and execution surfaces, Proposed)
for the dual surface: ADR-0019 section 4 named the console CLI plus Claude
Code skill for the agentic operations and deferred the report contract that
distinguishes them to this ADR, which now fixes that contract.

Depends on ADR-0021 (the `.praxis/` repository convention, Proposed) for
where the aggregate report is written (the gitignored, regenerable
`runs/<timestamp>/` dir) and for the `.praxis/knowledge/` believed set the
default-all run iterates.

Extends the ADR-0009 R-mode and E-mode contracts. R-mode keeps believed
`success_signals` and `failure_signals` as its only inputs and keeps the
auditor scenarios excluded (the closed leak path); E-mode keeps the
risks-plus-uncertainties-plus-failure-watch-list inputs and the
`off_path_fraction` floor. This ADR adds the dual surface, the aggregate
default, the OK / REGRESSED / STALE report, and the per-goal budget on top
of those modes; it does not change what the modes read or relax the leak
closure.

Re-cites ADR-0005 (first-oracle-must-be-seeded) and ADR-0001 (append-only
store) for the STALE-verdict knowledge-update path, ADR-0013 (recency
decay) for the version-anchor input to the STALE classification, and
ADR-0014 (candidate persistence) and ADR-0022 (the teach skill) for the
human-confirmed re-seed a STALE verdict proposes. It does not supersede any
prior ADR.
