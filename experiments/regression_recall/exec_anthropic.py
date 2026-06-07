"""Anthropic API Executor for the regression-recall experiment.

Implements the `Executor` protocol from harness.py using real Anthropic API
calls. Each (arm, seed, goal) call spins up a fresh `client.messages.create`
agent loop with two tools:

  - `http_probe`: GET/POST to the testapp running locally. The container of
    the experiment is HTTP-only (testapp.py is plain http.server), so this
    is the moral equivalent of "what would a real agent driving a browser
    see at the HTTP level". All 8 planted regressions surface at HTTP.
  - `report_findings`: the agent's final structured output. Forces a
    schema so the harness can compute recall deterministically.

Reads `ANTHROPIC_API_KEY` from `<repo>/.claude/secrets.env` via the same
Pattern A loader documented in `~/.claude/rules/secrets.md`. Default model
is `claude-sonnet-4-6` (matches the Phase-0 subscription baseline; same-model
caveat is documented in HANDOFF.md and acknowledged by the project owner).

Cost ceiling: ~5K input + ~2K output per call, x ~5-10 tool turns =
~25-50K tokens. At Sonnet 4.6 pricing ($3/$15 per Mtok), one call is
~$0.05-0.20; 45 calls is ~$2-10.

Honesty caveats baked into the design (see ADR-0009 + HANDOFF.md):
- Same model for all arms (not cross-model). Cross-model is Phase 1.5.
- The cold_readme arm reads the frozen README + per-goal sentences as
  documented in the manifest; the same authoring-leak caveat applies.
- The experiment falsifies "memory recall > cold_readme recall at fixed
  token budget on this app". It does NOT validate cross-app generalization.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

# Imports from the praxis core (used to render the memory arm's R/E-mode
# prompts) and the experiment manifest (used by the LLM judge). The harness
# already exposes these on sys.path via conftest.py + run_live.py.
from praxis.model import load as load_knowledge  # noqa: E402

from regression_recall.manifest import default_manifest  # noqa: E402

# Default model. claude-api skill prefers Opus 4.8 unless explicitly named,
# but Pablo named Sonnet 4.6 (matches Phase-0 baseline). Override with
# PRAXIS_LIVE_MODEL env var if needed.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Maximum tool-use turns per call. Cap is a runaway-loop guard, NOT the
# experiment's budget knob (that's `budget_tokens` from the plan, passed
# in via inputs).
MAX_TURNS = 20


# --- secrets loading (Pattern A from secrets.md) ---------------------------


def _load_secrets(repo_root: Path) -> None:
    """Load <repo>/.claude/secrets.env into os.environ. Pattern A: split on
    the first `=` only, leaves values with spaces intact (the canonical
    secrets.md pattern for connection strings, JWTs, etc).
    """
    path = repo_root / ".claude" / "secrets.env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n").rstrip("\r")
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value


# --- HTTP probe tool -------------------------------------------------------


HTTP_PROBE_TOOL: dict[str, Any] = {
    "name": "http_probe",
    "description": (
        "Send an HTTP request to the testapp at the given base URL. "
        "Use this to probe behavior; every request is one action against the "
        "system under test. Returns the response status, headers, and body. "
        "Use GET for navigation/inspection and POST for form submission and "
        "API calls. Include a JSON-encoded body for POST when needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST"],
                "description": "HTTP method.",
            },
            "path": {
                "type": "string",
                "description": "Path to request, beginning with /. Will be "
                                "appended to the base URL.",
            },
            "headers": {
                "type": "object",
                "description": "Optional request headers as a string->string "
                                "map (e.g. {\"Idempotency-Key\": \"abc\", "
                                "\"Cookie\": \"role=admin\"}).",
                "additionalProperties": {"type": "string"},
            },
            "form_body": {
                "type": "object",
                "description": "Optional form-urlencoded POST body. "
                                "Provide as a string->string map "
                                "(e.g. {\"coupon\": \"SAVE10\", "
                                "\"subtotal\": \"49\"}).",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}


REPORT_FINDINGS_TOOL: dict[str, Any] = {
    "name": "report_findings",
    "description": (
        "Emit your final structured findings for the harness to grade. Call "
        "this exactly once at the end of your probe, after you have spent "
        "your budget or are confident you have found what you can. After "
        "this call, do not emit further http_probe calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "description": "Each observation is one regression you "
                                "believe is happening. Be specific and "
                                "literal: describe the HTTP-level evidence "
                                "(method, path, body, observed response).",
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "description": "Specific factual observation, "
                                            "phrased as the regression itself.",
                        },
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
            "notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional free-text notes for the audit.",
            },
        },
        "required": ["observations"],
        "additionalProperties": False,
    },
}


# --- execution -------------------------------------------------------------


@dataclass
class _AgentRunResult:
    observations: list[dict[str, Any]]
    notes: list[str]
    actions_used: int
    tokens_used: int
    visited_urls: list[str]


def _run_probe(base_url: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one http_probe tool call. Returns a dict the agent can read."""
    method = args.get("method", "GET").upper()
    path = args.get("path") or "/"
    if not path.startswith("/"):
        path = "/" + path
    url = base_url.rstrip("/") + path
    headers = args.get("headers") or {}
    form_body = args.get("form_body") or {}
    data: bytes | None = None
    final_headers: dict[str, str] = dict(headers)
    if method == "POST" and form_body:
        data = "&".join(f"{k}={v}" for k, v in form_body.items()).encode("utf-8")
        final_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded",
        )
    req = urllib.request.Request(url, method=method, data=data,
                                  headers=final_headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": resp.status,
                "headers": dict(resp.headers),
                "body": body[:4000],  # cap to keep tool result small
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "status": e.code,
            "headers": dict(e.headers or {}),
            "body": body[:4000],
            "error": True,
        }
    except (urllib.error.URLError, TimeoutError) as e:
        return {"status": 0, "error": True, "body": f"network error: {e}"}


def _run_one_call(
    *,
    client: anthropic.Anthropic,
    model: str,
    arm: str,
    goal_id: str,
    prompt: str,
    base_url: str,
    max_tokens_per_response: int = 4096,
) -> _AgentRunResult:
    """Drive one (arm, goal) agent loop end-to-end.

    Builds the request, lets the agent call http_probe and finally
    report_findings, then returns the structured result.
    """
    system = (
        "You are an experiment agent for the Praxis regression-recall test. "
        "You will probe a running testapp by issuing HTTP requests via the "
        "http_probe tool. When you have completed your probe (either because "
        "you found what you can or you've used your budget), call "
        "report_findings exactly once with your structured findings. "
        "All observations should be HTTP-level and specific. Do not invent "
        "regressions you have no evidence for; an empty observations list is "
        "a valid honest answer."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tokens_used = 0
    actions_used = 0
    visited_urls: list[str] = []
    observations: list[dict[str, Any]] = []
    notes: list[str] = []

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens_per_response,
            system=system,
            tools=[HTTP_PROBE_TOOL, REPORT_FINDINGS_TOOL],
            messages=messages,
        )
        usage = response.usage
        tokens_used += (
            (usage.input_tokens or 0)
            + (usage.output_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )

        # The agent's response blocks: text + tool_use(s).
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # No tool calls. Either the agent gave up or said something
            # unstructured; treat as end of loop. Capture text as a note.
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    notes.append(f"[final-text/{arm}/{goal_id}] {block.text[:500]}")
            break

        # Append the assistant turn verbatim so the API sees its own tool_use.
        messages.append({"role": "assistant", "content": response.content})

        # Process each tool call and assemble the user tool_result reply.
        results: list[dict[str, Any]] = []
        terminate = False
        for tu in tool_uses:
            if tu.name == "http_probe":
                actions_used += 1
                args = tu.input if isinstance(tu.input, dict) else {}
                path = args.get("path", "/")
                visited_urls.append(path)
                probe = _run_probe(base_url, args)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(probe),
                })
            elif tu.name == "report_findings":
                args = tu.input if isinstance(tu.input, dict) else {}
                observations = list(args.get("observations") or [])
                notes.extend(list(args.get("notes") or []))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": "findings recorded",
                })
                terminate = True
            else:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": f"unknown tool {tu.name!r}",
                    "is_error": True,
                })

        messages.append({"role": "user", "content": results})

        if terminate:
            break
        if response.stop_reason == "end_turn":
            break
    else:
        notes.append(f"[hit-MAX_TURNS/{arm}/{goal_id}] loop capped at {MAX_TURNS}")

    return _AgentRunResult(
        observations=observations,
        notes=notes,
        actions_used=actions_used,
        tokens_used=tokens_used,
        visited_urls=visited_urls,
    )


# --- arm-specific prompt construction --------------------------------------


def _build_arm_prompt(arm: str, goal_id: str, base_url: str,
                       budget_tokens: int, repo_root: Path) -> str:
    """Build the user-message text for one (arm, goal_id) pair.

    The harness-level `_render_prompt_for_arm` is a one-line stub designed
    for an operator pasting into a Claude Code session; in the API path we
    construct the real arm-specific prompt here so all three arms receive
    the inputs the pre-registration says they should.

    - cold:        goal + base URL only
    - cold_readme: + frozen README + per-goal sentence
    - memory:      + R-mode and E-mode prompts rendered from the seed
                   knowledge for this goal
    """
    from praxis.runner.prompts import (  # local import to keep core untouched
        render_exploration_prompt, render_regression_prompt,
    )

    common = (
        f"Goal: {goal_id}. Base URL: {base_url}.\n"
        f"Budget: {budget_tokens} tokens.\n"
        f"You will probe via the http_probe tool. When done, call "
        f"report_findings exactly once with your observations.\n"
        f"Use HTTP-level evidence; describe each finding as the regression "
        f"itself (method, path, body, response).\n"
    )

    if arm == "cold":
        return (
            common
            + "\nYou have NO prior knowledge of this app. Discover it through "
              "probing; report any deviation from sensible behavior."
        )

    rr_root = repo_root / "experiments" / "regression_recall"

    if arm == "cold_readme":
        readme = (rr_root / "README_FROZEN.md").read_text(encoding="utf-8")
        per_goal_path = rr_root / "cold_readme_per_goal.md"
        per_goal_full = per_goal_path.read_text(encoding="utf-8")
        return (
            common
            + "\nYou have public information about this app, but no internal "
              "knowledge of its risks or oracles. Use the README below to "
              "understand what the app should do; then probe for deviations.\n"
              "\n# README_FROZEN.md\n"
            + readme
            + "\n\n# Per-goal guidance (cold_readme_per_goal.md)\n"
            + per_goal_full
        )

    if arm == "memory":
        seed_path = rr_root / "knowledge" / f"{goal_id}.knowledge.yaml"
        if not seed_path.exists():
            return (
                common
                + f"\n(no seed knowledge found at {seed_path.name}; "
                  "fall back to cold probing)"
            )
        kf = load_knowledge(seed_path)
        # Halve the budget between R-mode and E-mode so the combined
        # spend matches the per-arm budget; matches the experiment doc.
        half = max(1, budget_tokens // 2)
        r_prompt = render_regression_prompt(kf, budget_tokens=half)
        e_prompt = render_exploration_prompt(kf, budget_tokens=half)
        return (
            common
            + "\nYou have BELIEVED operational knowledge for this app. Use "
              "it. Run R-mode (regression) first to check the oracle "
              "signals; then run E-mode (exploration) probing each risk's "
              "trigger off the happy path.\n"
              "\n# R-mode (regression check)\n"
            + r_prompt
            + "\n\n# E-mode (exploration)\n"
            + e_prompt
        )

    return common  # defensive fallback


# --- LLM judge for observation -> manifest matching ------------------------


def _load_judge_prompt(repo_root: Path) -> str:
    return (repo_root / "experiments" / "regression_recall"
            / "judge_prompt.txt").read_text(encoding="utf-8")


def _format_manifest_for_judge(repo_root: Path) -> str:
    """Render the manifest's expected_observation set into a string the
    judge can read alongside the observation. The judge sees no
    plant_endpoint / probing hints; only the slug + expected_observation
    + category, which is what it needs to adjudicate label."""
    m = default_manifest()
    lines: list[str] = ["Manifest entries:"]
    for r in m.regressions:
        lines.append(
            f"- slug={r.slug!r} category={r.category!r}: "
            f"{r.expected_observation}"
        )
    return "\n".join(lines)


def _judge_one(
    client: anthropic.Anthropic,
    model: str,
    observation_text: str,
    manifest_block: str,
    judge_system: str,
) -> dict[str, Any]:
    """Call the judge on one observation. Returns the {label, slug, reason}
    dict the judge prompt specifies, or a stub on parse failure."""
    user = (
        manifest_block
        + "\n\nObservation to adjudicate:\n"
        + observation_text
    )
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=judge_system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Try to extract the first JSON object from the response.
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        pass
    return {"label": "noise", "slug": None, "reason": f"judge parse failure: {raw[:200]}"}


def judge_records(
    records_dir: Path,
    repo_root: Path,
    *,
    model: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Re-grade every record under records_dir using the LLM judge.

    Writes a `judged.json` per record file with the original detection list
    + a `judge_verdict` per detection. Returns a mapping
    {record_path: aggregated_record_dict}.

    Idempotent: existing judged files are reused so a re-run only spends
    tokens on missing ones.
    """
    _load_secrets(repo_root)
    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit("ANTHROPIC_API_KEY not set for judge")
    client = anthropic.Anthropic()
    chosen_model = model or os.environ.get("PRAXIS_JUDGE_MODEL") or DEFAULT_MODEL
    judge_system = _load_judge_prompt(repo_root)
    manifest_block = _format_manifest_for_judge(repo_root)

    out: dict[str, dict[str, Any]] = {}
    for record_path in sorted(records_dir.rglob("*.json")):
        if record_path.name == "judged.json":
            continue
        record = json.loads(record_path.read_text())
        judged_path = record_path.with_name(record_path.stem + ".judged.json")
        if judged_path.exists():
            out[str(record_path)] = json.loads(judged_path.read_text())
            continue
        detections = record["summary"]["detections"]
        for det in detections:
            verdict = _judge_one(
                client, chosen_model, det["observation_text"],
                manifest_block, judge_system,
            )
            det["judge_verdict"] = verdict
            # Update matched_manifest based on the judge's call.
            if verdict.get("label") == "match" and verdict.get("slug"):
                det["matched_manifest"] = True
                det["slug"] = verdict["slug"]
            else:
                det["matched_manifest"] = False
                det["slug"] = None
        judged_path.write_text(json.dumps(record, indent=2, default=str))
        out[str(record_path)] = record
    return out


# --- public Executor -------------------------------------------------------


def make_anthropic_executor(*, base_url: str, repo_root: Path,
                            model: str | None = None,
                            max_tokens_per_response: int = 4096):
    """Build an Executor function the harness can call.

    The returned callable matches `harness.Executor`:
        (arm, goal_id, prompt, inputs) -> dict
    """
    _load_secrets(repo_root)
    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit(
            "ANTHROPIC_API_KEY not set. Add it to "
            f"{repo_root / '.claude' / 'secrets.env'} or export it before "
            "running the live experiment.",
        )
    client = anthropic.Anthropic()
    chosen_model = model or os.environ.get("PRAXIS_LIVE_MODEL") or DEFAULT_MODEL

    def _off_path_fraction(visited: list[str],
                            happy_path: list[str]) -> float:
        if not visited:
            return 0.0
        if not happy_path:
            return 1.0
        happy = {u.rstrip("/") for u in happy_path}
        off = sum(1 for u in visited if u.rstrip("/") not in happy)
        return off / len(visited)

    def executor(arm: str, goal_id: str, prompt: str,
                 inputs: dict[str, Any]) -> dict[str, Any]:
        t0 = time.monotonic()
        # The harness-level `prompt` is a one-line stub for operators
        # pasting into Claude Code; the API path needs the full
        # arm-specific construction (README for cold_readme; R-mode +
        # E-mode prompts for memory). Build it here.
        real_prompt = _build_arm_prompt(
            arm=arm,
            goal_id=goal_id,
            base_url=inputs.get("base_url", base_url),
            budget_tokens=int(inputs.get("budget_tokens", 5000)),
            repo_root=repo_root,
        )
        result = _run_one_call(
            client=client,
            model=chosen_model,
            arm=arm,
            goal_id=goal_id,
            prompt=real_prompt,
            base_url=inputs.get("base_url", base_url),
            max_tokens_per_response=max_tokens_per_response,
        )
        elapsed = time.monotonic() - t0
        off_path = _off_path_fraction(
            result.visited_urls,
            inputs.get("happy_path_urls", []),
        )
        return {
            "observations": result.observations,
            "actions_used": result.actions_used,
            "tokens_used": result.tokens_used,
            "off_path_fraction": off_path,
            "visited_urls": result.visited_urls,
            "notes": result.notes,
            "elapsed_seconds": elapsed,
            "model": chosen_model,
            "arm": arm,
            "goal_id": goal_id,
        }

    return executor
