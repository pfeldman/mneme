"""Live Phase-1 regression-recall driver.

Spins up testapp, plants the 7 release-compatible regressions (excludes
t1_login_500 per the t1/s1 release-incompatibility documented in
ADR-0009), runs the harness through the Anthropic API Executor, computes
the verdict.

Cost ceiling: ~$2-10 at Sonnet 4.6 across 45 runs. The script prints token
totals after each run so you can abort if anything looks wrong.

Honest about its limits:
- Single model (Sonnet 4.6); same-model caveat per ADR-0005 documented in HANDOFF.md.
- HTTP-only probing; no real browser. testapp is plain http.server so this
  is the moral equivalent of "what an agent driving a browser would see at
  the HTTP layer", but real browsers (Playwright, CDP) could observe more.
- cold_readme README authored by the same Claude session that wrote the
  manifest; an independent reviewer should re-author or endorse before
  treating this run's verdict as the moat-falsification headline.

Usage:
    python experiments/regression_recall/run_live.py --n-seeds 5

Optional:
    --port 8765           (default; pick something free)
    --release phase-1-r1  (used for the release id)
    --dry-run             (sets up testapp + plants but does not call the API)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "experiments" / "ui-mutation"))
sys.path.insert(0, str(ROOT / "src"))

# Imports come after sys.path tweak (E402 silenced for the bootstrap).
from threading import Thread  # noqa: E402

from http.server import ThreadingHTTPServer  # noqa: E402

from regression_recall.exec_anthropic import (  # noqa: E402
    judge_records, make_anthropic_executor,
)
from regression_recall.harness import (  # noqa: E402
    build_default_plan, report, run_plan,
)
from regression_recall.metrics import Detection, RunSummary  # noqa: E402

import testapp  # noqa: E402


# Phase-1 release plant list (excludes t1_login_500 which structurally
# hides s1_oracle_lies; see ADR-0009).
PLANT_SET = [
    "t2_search_blank",
    "k1_save10_at_49",
    "k2_stack_codes",
    "k3_double_order",
    "k4_admin_settings",
    "k5_filter_lost",
    "s1_oracle_lies",
]


def _pick_free_port(start: int = 8765) -> int:
    for offset in range(100):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {start}..{start + 100}")


def _start_testapp(port: int) -> ThreadingHTTPServer:
    testapp.MUTATIONS.clear()
    testapp.BROKEN.clear()
    testapp._clear_planted_state()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), testapp.Handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:  # noqa: S310
        return json.loads(r.read())


def _setup_release(base: str) -> list[str]:
    _get(base, "/_unplant")
    for slug in PLANT_SET:
        _get(base, f"/_plant?set={slug}")
    return sorted(_get(base, "/_planted")["planted"])


def _reset_state_between_arms(base: str) -> None:
    """Clear cart/order/session state without touching PLANTED. The harness
    walks arm-major (cold -> cold_readme -> memory); each arm starts on a
    clean side-effect baseline so coupon stacking and idempotency stays
    deterministic across arms."""
    _get(base, "/_reset_state")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n-seeds", type=int, default=3,
                    help="Number of seeds per arm (default 3 = 27 runs).")
    p.add_argument("--release", default="phase-1-r1")
    p.add_argument("--port", type=int, default=None,
                    help="testapp port; default picks the first free port from 8765.")
    p.add_argument("--budget-tokens", type=int, default=5000,
                    help="Per-goal token budget hint passed to the prompt.")
    p.add_argument("--out-dir", default=None,
                    help="Output directory; default: experiments/regression_recall/runs/<release>-<ts>")
    p.add_argument("--dry-run", action="store_true",
                    help="Plant + verify only, do not call the API.")
    p.add_argument("--skip-judge", action="store_true",
                    help="Skip the LLM-judge re-grade step (only use the "
                          "deterministic Jaccard pre-filter). The pre-"
                          "registration names the judge as the official "
                          "matcher; this flag is for debugging only.")
    args = p.parse_args(argv)

    port = args.port or _pick_free_port()
    base = f"http://127.0.0.1:{port}"
    httpd = _start_testapp(port)
    print(f"testapp running on {base}")

    try:
        planted = _setup_release(base)
        print(f"planted slugs ({len(planted)}): {planted}")
        if args.dry_run:
            print("dry-run: skipping API calls.")
            return 0

        # Build the standard plan + Anthropic executor.
        plan = build_default_plan(release=args.release, n_seeds=args.n_seeds,
                                   budget_tokens_per_goal=args.budget_tokens)
        executor = make_anthropic_executor(base_url=base, repo_root=ROOT)

        # Wrap the executor to reset side-effect state between arms while
        # holding the PLANTED set constant. The harness iterates arm-major,
        # so we reset when we see a new arm.
        seen_arm: dict[str, str | None] = {"arm": None}

        def wrapped(arm, goal_id, prompt, inputs):
            if seen_arm["arm"] != arm:
                _reset_state_between_arms(base)
                seen_arm["arm"] = arm
            return executor(arm, goal_id, prompt, inputs)

        ts = int(time.time())
        out_dir = Path(args.out_dir) if args.out_dir else (
            ROOT / "experiments" / "regression_recall" / "runs"
            / f"{args.release}-{ts}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"out: {out_dir}")

        # Pin shas for the run manifest (pre-registration discipline).
        repo_sha = os.popen("cd " + str(ROOT) + " && git rev-parse HEAD").read().strip()
        manifest_sha = os.popen(
            "cd " + str(ROOT) + " && git hash-object experiments/regression_recall/manifest.json",
        ).read().strip()
        prompts_sha = os.popen(
            "cd " + str(ROOT) + " && git hash-object src/praxis/runner/prompts.py",
        ).read().strip()
        (out_dir / "run_manifest.json").write_text(json.dumps({
            "run_id": f"{args.release}-{ts}",
            "release": args.release,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "praxis_git_sha": repo_sha,
            "manifest_sha": manifest_sha,
            "prompts_py_sha": prompts_sha,
            "model": os.environ.get("PRAXIS_LIVE_MODEL", "claude-sonnet-4-6"),
            "model_provider": "anthropic-api",
            "budget_tokens_per_goal": args.budget_tokens,
            "n_seeds": args.n_seeds,
            "arms": list(plan.arms),
            "planted": planted,
            "port": port,
            "caveats": [
                "single-model (same-model independence boundary per ADR-0005)",
                "HTTP-only probing (no browser; testapp is http.server)",
                "cold_readme README authored by same session as manifest",
                "phase-1-r1 release excludes t1_login_500 (t1/s1 occlusion)",
            ],
        }, indent=2), encoding="utf-8")

        # Run.
        t0 = time.monotonic()
        records = run_plan(plan, wrapped, base_url=base,
                           out_dir=out_dir / "summaries")
        elapsed = time.monotonic() - t0

        # LLM-judge re-grade (the official matcher per pre_registration.md).
        # The Jaccard pre-filter is cheap but misses cross-vocabulary
        # paraphrase (manifest uses behavioral phrasing, agents use
        # HTTP-level evidence). The judge prompt is sha-pinned in the run
        # manifest.
        if not args.skip_judge:
            print()
            print("LLM judge re-grading observations against manifest...")
            judged = judge_records(out_dir / "summaries", ROOT)
            print(f"  judged {len(judged)} records")
            # Re-build per-record summaries from the judged dicts and
            # patch the in-memory `records` so report() sees the corrected
            # detections.
            judged_by_path: dict[tuple[str, int, str], dict] = {}
            for path_str, doc in judged.items():
                key = (doc["arm"], int(doc["seed"]), doc["goal_id"])
                judged_by_path[key] = doc
            for r in records:
                key = (r.arm, r.seed, r.goal_id)
                if key not in judged_by_path:
                    continue
                doc = judged_by_path[key]
                new_dets = [
                    Detection(
                        arm=r.arm, seed=r.seed,
                        slug=det["slug"],
                        observation_text=det["observation_text"],
                        matched_manifest=det["matched_manifest"],
                    )
                    for det in doc["summary"]["detections"]
                ]
                r.summary = RunSummary(
                    arm=r.arm, seed=r.seed, release=r.release,
                    tokens_used=doc["summary"]["tokens_used"],
                    actions_used=doc["summary"]["actions_used"],
                    detections=new_dets,
                    off_path_fraction=doc["summary"].get("off_path_fraction"),
                )

        # Verdict + reports.
        verdict = report(records, plan, out_dir=out_dir)
        total_tokens = sum(r.summary.tokens_used for r in records)
        total_actions = sum(r.summary.actions_used for r in records)
        print()
        print(f"=== completed in {elapsed:.1f}s ===")
        print(f"records:     {len(records)}")
        print(f"total tokens: {total_tokens:,}")
        print(f"total actions: {total_actions:,}")
        print(f"verdict:     {verdict.upper()}")
        print(f"results.md:  {out_dir / 'results.md'}")
        print(f"results.json: {out_dir / 'results.json'}")

        return 0 if verdict == "continue" else 1
    finally:
        try:
            _get(base, "/_unplant")
        except Exception:
            pass
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
