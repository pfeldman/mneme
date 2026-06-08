---
name: praxis-explore
description: Local-brain off-happy-path hunt over believed Praxis knowledge. Runs the same console explore engine, writes contested candidate files grouped by trigger, then triages fresh findings inline with the user (promote / leave / discard), applied immediately as the matching review action. Triage is ADVISORY and NEVER auto-mutates committed knowledge - a promote is a human review action, not an automatic edit. Use when a human wants to hunt risks and uncertainties on their Claude Code subscription (no API key).
---

# /praxis:explore (local-brain off-happy-path hunt)

You are the LOCAL BRAIN for the explore operation (ADR-0019 section 3 + 4,
ADR-0023). You run the SAME engine the console `praxis explore` runs, which
hunts off the happy path and writes any candidate risks and uncertainties it
finds as contested candidate files under `.praxis/candidates/`, one file per
observation (ADR-0021, ADR-0014). On this skill surface you ALSO surface what
the run just found and triage it inline with the user.

This is E-mode (ADR-0009): the inputs are risks plus uncertainties plus the
failure-signal watch-list. The engine logs `off_path_fraction` as the floor
against E-mode collapsing into R-mode; keep that floor in the report and call
it out if it is low.

## What you must never do

- NEVER auto-mutate committed knowledge. A fresh finding earns `believed` ONLY
  by evidence diversity or a human/spec seed (ADR-0005, ADR-0008, ADR-0014). A
  "promote" you apply inline is a HUMAN review action the user chose in the
  session, realized as the corresponding review action (append a seed event,
  never an in-place edit, ADR-0001); it is not an automatic edit you make on
  your own judgement. If the user does not choose to promote, you leave the
  candidate contested.
- NEVER collapse N observations from the same source into N entries. N
  observations from the same `agent_identity` count as ONE source (ADR-0008),
  never as N duplicate corroborations.
- NEVER edit a candidate file in place. Candidate events are immutable
  (ADR-0001); promotion is a NEW seed event with diversity.

## Protocol

1. Confirm you are inside a project that has `.praxis/` with seeded goals under
   `.praxis/knowledge/`. If there are no seeds, tell the user to seed a goal
   with `/praxis:teach` first and stop.

2. If a run must authenticate, read the app credential from the ADR-0021
   secrets channel: an environment variable wins, else the gitignored
   `.praxis.secrets` (`KEY=value`) at the repo root. The credential NEVER lives
   in knowledge. If a needed credential is absent, ASK the user for it and
   offer to append it (for example `echo "KEY=value" >> .praxis.secrets`).
   Never echo a secret value back to the user or into a log.

3. Run the engine across the believed set. Default-all is the aggregate run:

       praxis explore

   That hunts off-happy-path across EVERY goal under `.praxis/knowledge/`,
   writes one candidate file per observation under
   `.praxis/candidates/<goal>/<observation_id>.yaml`, and writes ONE report
   under `.praxis/runs/<timestamp>/explore-candidates.md` GROUPED by the
   structured `trigger`. To scope to one goal, run
   `praxis explore --goal <name>`. Each goal gets its own token-and-wall-time
   budget slice (ADR-0023 decision 7); a goal that exhausts its slice is a loud
   ERROR for that goal, not a silent skip.

4. Surface what the run just found, grouped by trigger. Each finding appears
   ONCE, annotated with how many times it was observed and how many DISTINCT
   `source_id`s attest to it. Remember the source rule: N observations from the
   same `agent_identity` are ONE source (ADR-0008). Show the trigger, the
   description / question, the confidence, and the distinct-source count.

5. Triage each FRESH finding inline with the user. For each one, offer three
   choices and apply the choice immediately as the matching review action:

   - **promote**: the user judges this finding worth believing. This is a HUMAN
     review action: realize it as the corresponding `praxis review` promotion
     (append a seed entry with `source_type` human/spec and the same candidate
     id to the goal's `*.knowledge.yaml`). The candidate event itself stays
     immutable; the seed plus the candidate together satisfy the diversity rule
     (ADR-0008) and the next projection promotes it to `believed`. You apply
     this ONLY because the user chose it, never on your own judgement.
   - **leave**: keep the finding as a contested candidate. It stays in
     `.praxis/candidates/` for the aggregate review queue. This is the default;
     when in doubt, leave.
   - **discard**: the user judges the finding spurious. Note it for the user;
     do not delete the immutable candidate event (ADR-0001). Discarding means
     "do not promote," not "rewrite history."

6. The aggregate contested queue stays `praxis review`. Your inline triage
   handles ONLY what THIS explore run just produced. The candidates the user
   was not present to triage (a teammate's runs after `git pull`, autonomous CI
   runs, the history) are surfaced by `praxis review`, which folds the
   committed candidate tree. Point the user there for the backlog; do not try
   to triage the whole queue inline.

7. Report the roll-up honestly: goals explored, committed candidates written
   (one file per observation), findings by trigger (believed vs contested), and
   the per-goal `off_path_fraction`. If any goal ERRORED, name it loudly; an
   errored goal fails the run and is never silently skipped.

## Surface parity

Same body, same store writes, same candidate files as the console
`praxis explore` (ADR-0023 decision 1 + 8). On the console surface the engine
writes the candidate files and exits; on this skill surface you ALSO triage the
fresh findings inline. You never change what is written; you add the inline
triage on top, and a promote is always a human-chosen review action, never an
automatic mutation of committed knowledge.
