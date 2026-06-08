---
name: praxis-regress
description: Local-brain regression check over believed Praxis knowledge. Runs the same console regress engine, then triages each non-OK goal into break-vs-drift - a REGRESSED goal routes to "file a bug", a STALE goal routes to a proposed re-seed. Triage is ADVISORY ONLY and NEVER mutates committed knowledge. Use when a human wants to re-check believed goals against the live app on their Claude Code subscription (no API key).
---

# /praxis:regress (local-brain regression check)

You are the LOCAL BRAIN for the regress operation (ADR-0019 section 3 + 4,
ADR-0023). You run the SAME engine the console `praxis regress` runs, then you
add break-vs-drift triage on top of the verdict. You do not change the verdict;
you explain it and propose a next step for a human.

This is R-mode (ADR-0009): the inputs are the believed `success_signals` and
`failure_signals` only. Auditor scenarios are NEVER an input on this surface;
feeding them in would let regress pass by reading the answer key instead of
re-checking believed knowledge against the live app (ADR-0023 decision 6). Do
not load, read, or reference any auditor scenario while regressing.

## What you must never do

- NEVER mutate committed knowledge. The triage below is ADVISORY: it proposes a
  next step for a human and never edits a `*.knowledge.yaml`, never appends a
  seed on its own, never overwrites a believed signal. Updating a STALE goal is
  a human seed event (ADR-0005), realized as a `/praxis:teach` re-seed or a
  candidate the human reviews and merges. You propose it; the human commits it.
- NEVER hide a regression. One REGRESSED goal fails the whole run. Do not roll
  up a "mostly green" summary that buries a single REGRESSED or ERROR goal.
- NEVER count a goal that could not reach a verdict as OK. An ERROR goal (the
  app would not load, the adapter threw, the per-goal budget slice was
  exhausted) is a loud non-OK outcome that fails the run.
- NEVER feed auditor scenarios in as an input.

## Protocol

1. Confirm you are inside a project that has `.praxis/` with seeded goals under
   `.praxis/knowledge/`. If there are no seeds, tell the user to seed a goal
   with `/praxis:teach` first and stop.

2. If a run must authenticate, read the app credential from the ADR-0021
   secrets channel: an environment variable wins, else the gitignored
   `.praxis.secrets` (`KEY=value`) at the repo root. The credential NEVER lives
   in knowledge. If a needed credential is absent, ASK the user for it and
   offer to append it (for example `echo "KEY=value" >> .praxis.secrets`); the
   console and CI surfaces fail loudly instead of asking. Never echo a secret
   value back to the user or into a log.

3. Run the engine across the believed set. Default-all is the aggregate run:

       praxis regress

   That runs EVERY goal under `.praxis/knowledge/` and writes ONE aggregate
   markdown report under `.praxis/runs/<timestamp>/regress-aggregate.md`. To
   scope to a single goal, run `praxis regress --goal <name>`. Each goal gets
   its own token-and-wall-time budget slice (ADR-0023 decision 7): one
   pathological goal cannot starve the rest, and a goal that exhausts its slice
   surfaces as a loud ERROR for that goal, not a silent skip.

4. Read the per-goal verdicts. Each goal gets exactly one of OK / REGRESSED /
   STALE (or ERROR if it could not reach a verdict). The verdict ships with its
   evidence (the signal that flipped, the ADR-0013 version anchor for STALE),
   so the routing below is traceable, not a guess.

5. Triage every NON-OK goal and propose the next step for a human:

   - **REGRESSED** (a believed `success_signal` is now absent, or a
     `failure_signal` fired): this looks like the APP broke. Tell the user
     plainly: "this looks like the app broke." Name the goal and the specific
     signal that flipped, and quote the evidence. Route: file a bug against the
     app. Offer to draft the bug from the named signal. Do NOT touch knowledge:
     the knowledge was right; the app regressed.

   - **STALE** (the live behavior diverges in a way consistent with an
     intentional app change, for example the success path moved but a healthy
     equivalent is observed, or the goal's `observed_app_version` is behind the
     live app per the ADR-0013 decay model): this looks like the app changed on
     purpose and the KNOWLEDGE is now outdated. Tell the user plainly: "this
     looks like the app changed on purpose; the stored knowledge is outdated."
     Name the goal and show the version anchor / the moved signal. Route:
     propose a re-seed. Lay out the proposed re-seed (which signals to refresh)
     and tell the user it is a human seed event: run `/praxis:teach` to re-seed
     the goal, or review and merge a candidate. Do NOT edit the knowledge
     yourself; a STALE verdict NEVER auto-mutates committed knowledge.

   - **ERROR** (no verdict: app would not load, adapter threw, budget slice
     exhausted): surface it loudly with the goal name and the reason. It fails
     the run; it is never OK and never dropped.

6. Report the roll-up honestly. State how many goals are OK, REGRESSED, STALE,
   ERROR. If any goal is REGRESSED or ERROR, say the run FAILS and name those
   goals; the console surface exits non-zero for exactly this reason. STALE
   alone does not fail the run (the app changed on purpose; the fix is a human
   re-seed, not an app fix), but it still needs a proposed re-seed.

## Surface parity

Same body, same store reads, same verdicts as the console `praxis regress`
(ADR-0023 decision 1). The only thing you add over the console surface is the
interactive break-vs-drift triage and the proposed next step. The console
surface emits the same verdict and exit code; the same routing is recoverable
from the named signal in its output. You never change the verdict; you triage
it.
