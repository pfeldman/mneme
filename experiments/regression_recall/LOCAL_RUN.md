# Running the Phase 1 regression-recall experiment LOCALLY

This is the subscription-path protocol: **Claude Code is the agent** (no
extra LLM key), a **Playwright MCP** server is its hands on the browser,
and `experiments/ui-mutation/testapp.py` is the System Under Test. The
harness is `experiments/regression_recall/harness.py`; you (the operator)
plant regressions, paste arm prompts into a Claude Code session, and feed
the observations back through the harness's Executor protocol.

The Phase 0 analog is `experiments/ui-mutation/LOCAL_RUN.md`. Read that
first if you have not - the moving parts are the same, the arms are new.

## Cost metric note

A flat-rate subscription does not expose per-task token counts; the
experiment records **browser actions** + **wall time** as the cost proxy
when running this path. The kill criteria in
`docs/phase-1-experiment.md` are written in tokens; one paid API-key
run (alternative path, bottom of this doc) confirms the margin
translates from actions to dollars.

Even on the subscription path, the operator's executor implementation
records a `tokens_used` estimate per run when possible (e.g. via the
session's metering UI). The harness accepts both axes; `tokens=null`
falls back to actions in the report.

## 1. Prerequisites (on your machine, NOT the sandbox)

```bash
git clone <your fork> && cd mneme
git checkout claude/mneme-phase-1
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Add a Playwright MCP server to Claude Code:

```bash
claude mcp add playwright -- npx -y @playwright/mcp@latest
```

Confirm Claude Code sees its `browser_*` tools with `/mcp`. Confirm
`praxis` imports cleanly:

```bash
python -c "import praxis; from praxis.runner import RegressionRunner, ExplorationRunner; print('ok')"
```

## 2. Start the test app

```bash
python experiments/ui-mutation/testapp.py --port 8000
# flows:        http://127.0.0.1:8000/login /search /cart
# phase-1:      /cart/apply /orders /settings/admin /list?page=N
# plant:        /_plant?set=NAME  /_unplant  /_planted
# state:        /_state
```

Verify the plant API works before any arm:

```bash
curl -s 'http://127.0.0.1:8000/_plant?set=k1_save10_at_49'
curl -s 'http://127.0.0.1:8000/_planted'
curl -s 'http://127.0.0.1:8000/_unplant'
```

## 3. Calibrate the budget (memory R-mode, clean release, target ~95%)

Per the pre-registration, the budget is calibrated against memory R-mode
happy-path cost on a CLEAN release, not against cold. Do a pilot run
across the 6 goals with memory R-mode and record the actions / tokens it
needed at 100% recall. Multiply by ~1.05 (5% slack) to target ~95%
utilization. Commit the calibrated budget to
`experiments/regression_recall/budget.json`:

```json
{ "budget_tokens_per_goal": 5000, "actions_floor_per_goal": 12 }
```

Edit `metrics.py` only if a kill threshold needs to change; that
invalidates the pre-registration.

## 4. Pin the run manifest

Before any arm runs, commit the run manifest to
`experiments/regression_recall/runs/<run_id>/manifest.json` with all
sealed shas:

```json
{
  "run_id": "phase-1-r1-<UTC-timestamp>",
  "started_at": "2026-06-07T...",
  "release": "phase-1-r1",
  "testapp_git_sha": "<git rev-parse HEAD>",
  "praxis_git_sha": "<git rev-parse HEAD>",
  "frozen_readme_sha": "<git hash-object README_FROZEN.md>",
  "cold_readme_per_goal_sha": "<git hash-object cold_readme_per_goal.md>",
  "manifest_sha": "<git hash-object manifest.json>",
  "prompts_py_sha": "<git hash-object src/praxis/runner/prompts.py>",
  "judge_prompt_sha": "<git hash-object judge_prompt.txt>",
  "metrics_sha": "<git hash-object metrics.py>",
  "model": "claude-sonnet-4-6",
  "model_provider": "claude-code-subscription",
  "budget_tokens_per_goal": 5000,
  "n_seeds": 5,
  "arms": ["cold", "cold_readme", "memory"]
}
```

`git hash-object` is canonical; copy each value verbatim. After the run
the same JSON is committed alongside `results.json` so a third party
can verify which artifacts were active.

## 5. The arms (arm-major order; same plant state across all three)

For each release candidate:

1. Plant the regressions for the release (see "Per-release plant
   commands" below). Verify with `curl /_planted`.
2. Run the THREE arms BACK TO BACK against the same plant state. The
   order is fixed: `cold` -> `cold_readme` -> `memory`. Memory runs
   last so any operator confusion does not let it secretly probe after
   cold has already given up.
3. For each (arm, seed, goal), open a fresh Claude Code session.
   Paste the prompt from the harness for that arm + goal (see "Prompts
   to paste" below). The session drives the browser via Playwright MCP,
   reports observations.
4. Feed the session's observations into the harness's Executor protocol
   (see "Wiring the Executor" below). The harness writes per-record
   files to `runs/<run_id>/summaries/<arm>/<seed>/<goal>.json`.
5. After all arms complete on the release, call `/_unplant` and verify
   `/_planted` returns the empty manifest.
6. Repeat for the CONTROL release (no plants) so the false-pass
   guardrail has data.

## 6. Per-release plant commands

Phase 1 ships ONE release (`phase-1-r1`) per the pre-registration. Plant
ALL eight regressions before the arms run:

```bash
curl -s 'http://127.0.0.1:8000/_plant?set=t1_login_500'
curl -s 'http://127.0.0.1:8000/_plant?set=t2_search_blank'
curl -s 'http://127.0.0.1:8000/_plant?set=k1_save10_at_49'
curl -s 'http://127.0.0.1:8000/_plant?set=k2_stack_codes'
curl -s 'http://127.0.0.1:8000/_plant?set=k3_double_order'
curl -s 'http://127.0.0.1:8000/_plant?set=k4_admin_settings'
curl -s 'http://127.0.0.1:8000/_plant?set=k5_filter_lost'
curl -s 'http://127.0.0.1:8000/_plant?set=s1_oracle_lies'

# verify the manifest matches what the harness expects:
curl -s 'http://127.0.0.1:8000/_planted'
curl -s 'http://127.0.0.1:8000/_state'
```

For the CONTROL release: run `/_unplant` + `/_unbreak`, verify clean,
then run all arms. Memory's false-pass rate on this release must be
0.0 (kill criterion).

## 7. Prompts to paste

The harness renders an arm-specific framing line. The operator's job is
to add the body that drives the agent. Three shapes:

### cold arm

```
Goal: <goal_id>. Base URL: http://127.0.0.1:8000.
Budget: <N> tokens. You have NO prior knowledge of this app.

Probe it via the Playwright MCP browser tools. For each observation you
can ground in HTTP-level evidence, emit a JSON line:

  {"value": "<one-sentence factual observation>", "kind": "failure",
   "type": "network|behavioral|text|url|accessibility",
   "visited": "/path"}

Stop when you have run out of budget or you have nothing more to
report. Output your final JSON list to the chat.
```

### cold_readme arm

Paste the same shape PLUS the contents of `README_FROZEN.md` and the
goal's section from `cold_readme_per_goal.md`. The agent reads them as
the public information surface. Do NOT paste the planted-regression
manifest; that is the leak the experiment defends against.

### memory arm (R-mode then E-mode)

R-mode prompt comes from
`praxis.runner.prompts.render_regression_prompt(kf, ...)`; E-mode from
`render_exploration_prompt(kf, ...)`. Construct the seeded `kf` from
the believed knowledge for this release (one `*.knowledge.yaml` per
goal, committed to `experiments/regression_recall/knowledge/`). Run
R-mode first, then E-mode (both within the same budget split). The
operator's executor passes `happy_path_urls` so the harness can
compute `off_path_fraction` post-run.

For the subscription path, render the prompts in a Python REPL and
paste them into Claude Code:

```python
import sys; sys.path[:0] = ["src"]
from praxis.model import load
from praxis.runner.prompts import (
    render_regression_prompt, render_exploration_prompt,
)
kf = load("experiments/regression_recall/knowledge/login.knowledge.yaml")
print(render_regression_prompt(kf, budget_tokens=2500))
print("---")
print(render_exploration_prompt(kf, budget_tokens=2500))
```

Two halves of the budget (R-mode + E-mode); recombine post-run.

## 8. Wiring the Executor

The harness's `Executor` callable is the seam where you feed the
session's observations back. The simplest subscription-path executor
reads the agent's JSON list from a file you copied off the Claude Code
chat:

```python
# experiments/regression_recall/exec_subscription.py
import json
from pathlib import Path

def make_subscription_executor(records_root: Path):
    def executor(arm, goal_id, prompt, inputs):
        path = records_root / arm / str(inputs["seed"]) / f"{goal_id}.json"
        if not path.exists():
            raise SystemExit(
                f"\nMISSING: paste the agent output for "
                f"{arm}/{inputs['seed']}/{goal_id} into {path}\n"
                f"Format: {{'observations': [...], 'actions_used': N, "
                f"'tokens_used': N, 'off_path_fraction': F, 'visited_urls': [...]}}"
            )
        return json.loads(path.read_text())
    return executor
```

This is the human-in-the-loop seam: the harness halts and prompts you
for the next paste, you do the run, you save the JSON, you continue.

Drive the harness:

```python
from pathlib import Path
from regression_recall.harness import build_default_plan, run_plan, report
from exec_subscription import make_subscription_executor

run_id = "phase-1-r1-2026-06-07T180000Z"
root = Path(f"experiments/regression_recall/runs/{run_id}")
plan = build_default_plan(release="phase-1-r1", n_seeds=5,
                          budget_tokens_per_goal=5000)

records = run_plan(plan, make_subscription_executor(root / "agent_output"),
                   out_dir=root / "summaries")
verdict = report(records, plan, out_dir=root)
print("verdict:", verdict)
```

For the CONTROL release run, pass `control_records` to `report(...)`.

## 9. After the run

1. Commit `runs/<run_id>/` (manifest.json, summaries/, agent_output/,
   results.json, results.md, run_manifest.json).
2. Write `docs/adr/0010-phase-1-verdict.md` mirroring the structure of
   ADR-0007. The verdict goes in the title (`continue` or `kill`).
3. If `kill`, return to the kill/continue gate in docs/04 and stop.
4. If `continue`, the Phase 1.5 work (Stagehand arm, auditor-as-offline,
   real-app generalization) is unblocked.

## 10. Alternative: API-key path (multi-model)

To compare models (e.g. Sonnet vs Haiku vs GPT-4o), implement an
Executor that calls the chosen LLM with the same prompt and a tool
description for browser actions, instead of the subscription paste-in
loop. Set the API key in `.claude/secrets.env`:

```
ANTHROPIC_API_KEY=...
# or OPENROUTER_API_KEY=... for multi-model
```

Then:

```python
def make_api_executor(model: str, max_tokens: int):
    import anthropic
    client = anthropic.Anthropic()
    def executor(arm, goal_id, prompt, inputs):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            tools=[BROWSER_TOOL_SCHEMA],   # see docs/runner-tools.md
        )
        # Parse resp.content for the JSON list; record tokens_used from
        # resp.usage; visited_urls from the tool_use blocks.
        return parse_response(resp)
    return executor
```

The API-key path produces the dollars/tokens number the docs/06
existential risk is framed in; the subscription path produces actions
+ wall time as proxies. Both feed the same harness.

## 11. Sanity checklist before the first arm

- [ ] testapp.py running on port 8000.
- [ ] `/_unplant` + `/_unbreak` returned clean state.
- [ ] All sealed sha values pinned in `runs/<run_id>/manifest.json`.
- [ ] `budget.json` written from the calibration pilot.
- [ ] Seeded knowledge files exist for all 6 goals (no R-mode prompt
      possible without them).
- [ ] An independent reviewer (Pablo role-playing the cold-arm
      advocate) signed off that `README_FROZEN.md` +
      `cold_readme_per_goal.md` are the strongest case for the cold
      arm (per ADR-0009 sec 5 + the threats-to-validity section).
- [ ] You read the kill criteria in `metrics.py` and accept them
      pre-registered.
