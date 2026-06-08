# Running the live experiment LOCALLY with your Claude Code subscription

This is the no-extra-cost path: **Claude Code is the agent**, a **Playwright MCP**
server is its hands on the browser, and `testapp.py` is the System Under Test. No
Anthropic API key, no local GPU model — it runs on the subscription you already pay
for.

```
  Claude Code (the agent, = your subscription)
        │  reads believed knowledge (the Mneme prompt)
        ▼
  Playwright MCP  ──drives──▶  testapp.py  (login / search / checkout + mutations)
        │
        ▼  observes signals, writes events back through the Mneme store
  praxis.adapters.BrowserUseAdapter  (store → merge → oracle)
```

> Why not Browser Use here? Browser Use is a separate agent that needs its OWN LLM
> credential (an API key or a local model) — it cannot use your Claude subscription.
> Making Claude Code itself the agent is the only way to use the subscription. The
> Mneme core is unchanged; only the runtime differs. (For the Browser-Use path with
> an API key or a local Ollama model, see "Alternatives" at the bottom.)

## Cost metric note
A flat-rate subscription does not expose per-task token counts, so the existential
gate uses **browser actions** (clicks/fills/navigations) + **wall time** as the cost
proxy. `RunResult.actions` carries this; `metrics.existential_gate` auto-falls back
to actions when tokens are 0 (`cost_unit` in the output tells you which it used).

---

## 1. Prerequisites (on your machine, NOT this sandbox)
```bash
git clone <your fork> && cd praxis
git checkout claude/mneme-phase-0-core-ZfmKT
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # core + pytest/ruff/mypy (no browser-use needed)
```

Add a Playwright MCP server to Claude Code (any of these works; pick one):
```bash
# Microsoft Playwright MCP (recommended)
claude mcp add playwright -- npx -y @playwright/mcp@latest
```
Confirm Claude Code sees its `browser_*` tools with `/mcp`.

## 2. Start the test app
```bash
python experiments/ui-mutation/testapp.py --port 8000
# flows:   http://127.0.0.1:8000/login  /search  /cart
# control: /_mutate?set=NAME   /_reset   /_state
```

## 3. Seed the oracles BEFORE exploring (ADR-0005)
The first oracle for each goal is seeded by spec, never self-certified. Dump the
seeds the runtime already defines:
```bash
python - <<'PY'
import sys; sys.path[:0] = ["src", "experiments/ui-mutation"]
import runtimes
from praxis.model import dump
for flow in ("login", "search", "checkout"):
    seed = runtimes.seed_for(flow)
    dump(seed, f"experiments/ui-mutation/seed_{flow}.knowledge.yaml")
    print("seeded", seed.goal_id)
PY
```

## 4. Run the protocol (give this to your LOCAL Claude Code)
Open Claude Code in the repo and paste:

> Act as the experiment agent for `experiments/ui-mutation/`. The test app is on
> http://127.0.0.1:8000 and you drive it with the Playwright MCP `browser_*` tools.
> Follow the protocol in `LOCAL_RUN.md` §5 exactly, in order. After each browser
> action increment an action counter. Record one `metrics.RunResult` per run and,
> at the end, call `metrics.existential_gate`, `metrics.robustness_gate`,
> `metrics.oracle_error_rates`, `metrics.verdict`, and `metrics.write_markdown`.
> STOP and report numbers if Measurement 1 fails.

## 5. The protocol (measurement ORDER is the point — docs/04)

**Setup once:** `GET /_reset`. Build a `BrowserUseAdapter` seeded for all 3 flows
(see `runtimes.make_adapter`), pointed at a fresh `FileEventStore` dir.

**Population (memory arm only, unmutated):** for each flow, achieve the goal by
exploring, then `adapter.write_observations(...)` the success signals you actually
saw (behavioral: a "Sign out"/results/confirmation element; network: the 2xx on
/session, /search, /order). Two evidence types = the diversity the oracle needs.

**Measurement 1 — EXISTENTIAL GATE (run FIRST, unmutated):**
- `memory` arm: for each flow, `kf = adapter.read_knowledge(goal_id)`, use
  `adapter.knowledge_to_prompt(kf)` as your guidance, achieve the goal, count
  actions. `oracle_said = runtimes.oracle_said_success(kf, observed)`.
- `cold_agent` arm: same flows with NO prior knowledge (ignore the prompt; figure
  each out from scratch), count actions.
- `gate1 = metrics.existential_gate(memory, cold)`. **If `gate1["PASSED"]` is False
  → STOP and report. Do not continue.**

**Measurement 2 — ROBUSTNESS (only if M1 passed):** for each mutation
`{rename_control, move_field, swap_email_for_username, insert_intermediate_step}`:
- `GET /_mutate?set=<mutation>`, then for each flow it perturbs:
  - `memory` arm: regenerate steps from knowledge, achieve goal, record.
  - `recorded_script` arm: run the brittle baseline (§6). It should break.
- `GET /_reset` after each mutation.
- `gate2 = metrics.robustness_gate(mutated_memory, recorded)`.

**Guardrail — oracle honesty under regression (the product-killer test):** for each
flow, `GET /_break?set=<flow>` so the goal is UNREACHABLE (success signals never
appear), run the `memory` arm, and record honestly — `succeeded=False`,
`ground_truth_success=False`, `oracle_said_success=runtimes.oracle_said_success(kf,
observed)` (must be False: the believed signals were not observed). `GET /_unbreak`
after each. `metrics.oracle_error_rates(...)`; **false_pass must be 0**.

**Verdict:** `metrics.verdict(gate1, gate2, oracle)` →
`metrics.write_markdown(...)`.

## 6. The recorded-script baseline (brittle, for M2 only)
Emit a real Playwright script per flow from the recorded coordinates:
```bash
pip install playwright && playwright install chromium   # only for this arm
python - <<'PY'
import sys; sys.path[:0] = ["src", "experiments/ui-mutation"]
from praxis.adapters.playwright import RecordedScript, RecordedStep
login = RecordedScript("login", [
    RecordedStep("goto", value="http://127.0.0.1:8000/login"),
    RecordedStep("fill", 'input[name="identifier"]', "alice"),
    RecordedStep("fill", 'input[name="secret"]', "pw"),
    RecordedStep("click", 'text="Sign in"'),
    RecordedStep("expect", 'text="Sign out"', "logged in"),
])
open("experiments/ui-mutation/recorded_login.py", "w").write(login.emit())
print("wrote recorded_login.py")
PY
```
Run it against baseline (passes) and against each mutation (the `text="Sign in"`
selector and positional fills break) to get `recorded_recovery`.

---

## Alternatives
- **Browser Use + local model (truly local, no subscription):** install
  `pip install -e ".[browser-use]"` + run Ollama with a capable vision model
  (e.g. `ollama pull qwen2.5vl`), then point the adapter's agent at
  `browser_use.llm.ChatOllama(...)` in `BrowserUseAdapter.build_agent`. Caveat: weak
  models fail tasks and you end up measuring the model, not the thesis.
- **Browser Use + API key:** `pip install -e ".[browser-use]"`, set the provider key,
  use `build_agent(...)`. Most reliable, but costs per token.

## What you get
`results.md` with the three gates and a CONTINUE/STOP verdict — this time from a
real browser driven by a real LLM, against a real (if local) app. That is the
empirical existential gate the offline `simapp` run could not give you.
