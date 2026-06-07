"""`praxis` CLI dispatcher.

Six verbs: init, learn, regress, explore, review, status. Each is a thin
glue between a discovered project context (`.praxis/` upward from cwd) and
the runtime-agnostic core (model + store + merge + oracle + runner).

The CLI never drives a browser. For live runs, regress / explore print the
agent-facing prompt and accept the resulting observations as JSON (file
or stdin), matching the human-in-the-loop seam documented in
`experiments/regression_recall/LOCAL_RUN.md`.

Stdlib argparse + pyyaml only (AGENTS.md: ask before adding deps).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..adapters import BrowserUseAdapter
from ..model import KnowledgeFile, Target, dump, load
from ..runner import (
    ExplorationRunner,
    RegressionRunner,
    RegressionVerdict,
    write_junit_xml,
    write_markdown_report,
)
from ..store import FileEventStore, ObservedSignal


# --- project discovery ----------------------------------------------------


CONFIG_NAME = "config.yaml"
PROJECT_DIR = ".praxis"


class ProjectContext:
    """Resolved layout for one `.praxis/` project: paths + config.

    Source of truth for where seeds live, where events go, what base URL
    the runners reference. Discovered by walking up from cwd to the first
    directory containing `.praxis/config.yaml`.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / PROJECT_DIR
        self.config_path = self.dir / CONFIG_NAME
        self.knowledge_dir = self.dir / "knowledge"
        self.events_dir = self.dir / "events"
        self.reports_dir = self.dir / "reports"
        self.config = yaml.safe_load(self.config_path.read_text()) or {}

    @property
    def base_url(self) -> str:
        return self.config.get("base_url", "http://127.0.0.1:8000")

    @property
    def app(self) -> str:
        return self.config.get("app", "praxis-target")

    @property
    def environment(self) -> str | None:
        return self.config.get("environment")

    @property
    def agent_id(self) -> str:
        return self.config.get("agent_id", "praxis-cli")

    @property
    def observed_app_version(self) -> str | None:
        return self.config.get("observed_app_version")

    def target(self) -> Target:
        return Target(app=self.app, environment=self.environment)

    def store(self) -> FileEventStore:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        return FileEventStore(str(self.events_dir))

    def seeds(self) -> dict[str, KnowledgeFile]:
        out: dict[str, KnowledgeFile] = {}
        if not self.knowledge_dir.exists():
            return out
        for path in sorted(self.knowledge_dir.glob("*.knowledge.yaml")):
            kf = load(path)
            out[kf.goal_id] = kf
        return out

    def adapter(self) -> BrowserUseAdapter:
        return BrowserUseAdapter(
            self.store(),
            target=self.target(),
            seeds=self.seeds(),
            current_version=self.observed_app_version,
        )


def discover_project(start: Path | None = None) -> ProjectContext:
    """Walk up from `start` (or cwd) to the first `.praxis/` directory.

    Raises a CLI-friendly error when none exists; the user is expected to
    `praxis init` first.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / PROJECT_DIR / CONFIG_NAME).exists():
            return ProjectContext(candidate)
    raise SystemExit(
        f"no praxis project found (walked up from {cur}). "
        f"Run `praxis init` in your project root."
    )


# --- verbs ---------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path or Path.cwd()).resolve()
    pdir = root / PROJECT_DIR
    if pdir.exists() and not args.force:
        print(f"{pdir} already exists. Re-run with --force to overwrite the "
              f"config (knowledge + events are preserved).", file=sys.stderr)
        return 2
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "knowledge").mkdir(exist_ok=True)
    (pdir / "events").mkdir(exist_ok=True)
    (pdir / "reports").mkdir(exist_ok=True)
    config = {
        "base_url": args.base_url,
        "app": args.app or root.name,
        "environment": args.env,
        "agent_id": args.agent_id,
        "observed_app_version": None,
    }
    (pdir / CONFIG_NAME).write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    # .gitignore so secrets/events stay local by default; remove to share.
    (pdir / ".gitignore").write_text("events/\nreports/\n", encoding="utf-8")
    print(f"initialized praxis project at {pdir}")
    print(f"  config: {pdir / CONFIG_NAME}")
    print(f"  knowledge: {pdir / 'knowledge'}/  (seed goals here)")
    print(f"  events:    {pdir / 'events'}/    (append-only store; gitignored)")
    print(f"  reports:   {pdir / 'reports'}/   (run outputs; gitignored)")
    print()
    print("Next: drop a *.knowledge.yaml seed file under "
          f"{pdir / 'knowledge'}/ (one per goal, source_type=human or spec), "
          "or import one with `praxis learn <goal-id> --from-file PATH`.")
    return 0


def _cmd_learn(args: argparse.Namespace) -> int:
    proj = discover_project()
    src = Path(args.from_file).expanduser().resolve()
    if not src.exists():
        print(f"file not found: {src}", file=sys.stderr)
        return 2
    kf = load(src)
    if kf.goal_id != args.goal_id:
        print(
            f"goal_id mismatch: file says {kf.goal_id!r} but you passed "
            f"{args.goal_id!r}. Edit the file or re-run with the correct id.",
            file=sys.stderr,
        )
        return 2
    # Provenance + confidence are mandatory (ADR-0004); load() already
    # validates that. Refuse seeds whose success oracle is agent-sourced -
    # the first oracle for a goal is seeded by a human or spec (ADR-0005).
    for s in kf.success_signals:
        if s.provenance.source_type.value not in ("human", "spec"):
            print(
                f"refusing to seed {args.goal_id!r}: success signal "
                f"{s.value!r} has source_type={s.provenance.source_type.value}. "
                f"Seeds must be human or spec (ADR-0005).",
                file=sys.stderr,
            )
            return 2
    proj.knowledge_dir.mkdir(parents=True, exist_ok=True)
    out = proj.knowledge_dir / f"{args.goal_id}.knowledge.yaml"
    dump(kf, out)
    print(f"seeded {args.goal_id} -> {out}")
    return 0


def _executor_from_paste(prompt: str) -> dict[str, Any]:
    """Default interactive executor: print the prompt, read agent JSON
    back from stdin. The user runs the agent externally (subscription
    path with Claude Code + Playwright MCP) and pastes the resulting
    JSON when prompted.

    Refuses to proceed on empty stdin: a careless Ctrl-D used to silently
    produce an empty observation list, an UNCERTAIN verdict, and a green
    `praxis regress` exit when the agent had not actually run. The CI
    gate has to remain trustworthy; "I forgot to paste" is loud, not
    silent.
    """
    print("\n" + "=" * 78)
    print("PROMPT TO PASTE INTO YOUR AGENT SESSION (run it, collect output):")
    print("=" * 78)
    print(prompt)
    print("=" * 78)
    print(
        "\nPaste the agent's JSON output. End with a blank line then Ctrl-D. "
        "If the agent emitted nothing, pass an explicit "
        "`{\"observations\": [], \"actions\": 0, \"tokens\": null, "
        "\"visited_urls\": []}` so the empty case is intentional."
    )
    chunks: list[str] = []
    try:
        for line in sys.stdin:
            chunks.append(line)
    except EOFError:
        pass
    text = "".join(chunks).strip()
    if not text:
        raise SystemExit(
            "no agent output received on stdin. Refusing to record an empty "
            "observation list silently. Re-run after pasting the agent's "
            "JSON, or pass --from-file PATH."
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"could not parse agent JSON: {e}")


def _executor_from_file(path: Path):
    """Non-interactive executor for tests + scripted runs: read agent
    output from a JSON file. The harness's subscription executor wraps
    this same shape (one paste per (arm, seed, goal)).
    """
    def exe(prompt: str) -> dict[str, Any]:
        _ = prompt  # logged elsewhere; we don't re-emit it here
        return json.loads(Path(path).read_text())
    return exe


def _cmd_regress(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    goals = (
        [args.goal] if args.goal
        else sorted(proj.seeds().keys())
    )
    if not goals:
        print("no goals to regress (no seeds in .praxis/knowledge/). "
              "Run `praxis learn ...` first.", file=sys.stderr)
        return 2

    if args.from_file:
        executor = _executor_from_file(Path(args.from_file))
    else:
        executor = _executor_from_paste

    runner = RegressionRunner(
        adapter, agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
    )
    results = runner.run_all(
        goals, executor,
        budget_tokens=args.budget_tokens,
        budget_actions=args.budget_actions,
        stop_on_fail=args.stop_on_fail,
    )

    proj.reports_dir.mkdir(parents=True, exist_ok=True)
    last_md = proj.reports_dir / "last-regress.md"
    last_xml = proj.reports_dir / "last-regress.xml"
    write_markdown_report(results, last_md)
    write_junit_xml(results, last_xml)
    print(f"\nreport: {last_md}")
    print(f"junit:  {last_xml}")
    has_fail = any(r.verdict == RegressionVerdict.FAIL for r in results)
    return 1 if has_fail else 0


def _cmd_explore(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    if not args.goal:
        print("praxis explore requires --goal (one goal per run)", file=sys.stderr)
        return 2
    if args.from_file:
        executor = _executor_from_file(Path(args.from_file))
    else:
        executor = _executor_from_paste

    runner = ExplorationRunner(
        adapter, agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
    )
    happy = args.happy_path or []
    result = runner.run_one(
        args.goal, executor,
        happy_path_urls=happy,
        budget_tokens=args.budget_tokens,
        budget_actions=args.budget_actions,
    )
    print(f"\ngoal:                  {result.goal_id}")
    print(f"actions:               {result.actions}")
    print(f"tokens:                {result.tokens if result.tokens is not None else '-'}")
    print(f"wall:                  {result.wall_seconds:.2f}s")
    print(f"off_path_fraction:     {result.off_path_fraction:.2f}")
    print(f"candidate_observations: {len(result.candidate_observations)}")
    print(f"new_risks:             {len(result.new_risks)}  (all contested per ADR-0008)")
    print(f"new_uncertainties:     {len(result.new_uncertainties)}")
    for o in result.candidate_observations:
        print(f"  [{o.kind}/{o.type.value}] {o.value}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    seeds = proj.seeds()
    goal_ids = (
        [args.goal] if args.goal else sorted(seeds.keys())
    )
    if not goal_ids:
        print("no goals to review.", file=sys.stderr)
        return 2
    any_contested = False
    for gid in goal_ids:
        kf = adapter.read_knowledge(gid)
        if kf is None:
            continue
        contested_succ = [s for s in kf.success_signals
                           if s.status.value == "contested"]
        contested_fail = [s for s in (kf.failure_signals or [])
                           if s.status.value == "contested"]
        if not (contested_succ or contested_fail):
            continue
        any_contested = True
        print(f"\n## {gid}")
        for s in contested_succ:
            print(f"  [contested success / {s.type.value}] {s.value}  "
                  f"(confidence={s.confidence:.2f}, by {s.provenance.source_id})")
        for s in contested_fail:
            print(f"  [contested failure / {s.type.value}] {s.value}  "
                  f"(confidence={s.confidence:.2f}, by {s.provenance.source_id})")
    if not any_contested:
        print("nothing contested. Nothing to review.")
    else:
        print(
            "\nNote: this is a Phase-1 read-only digest. The accept / "
            "quarantine / ignore workflow lands in Phase 1.5; for now, "
            "edit the seed YAML to promote an observation."
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    seeds = proj.seeds()
    if not seeds:
        print(f"no seeds in {proj.knowledge_dir}", file=sys.stderr)
        return 2
    print(f"project: {proj.root}")
    print(f"app:     {proj.app}  env={proj.environment or '-'}  base_url={proj.base_url}")
    print(f"version: {proj.observed_app_version or '-'}")
    print()
    for gid in sorted(seeds.keys()):
        kf = adapter.read_knowledge(gid)
        if kf is None:
            print(f"  {gid}: (no projection)")
            continue
        n_succ = len(kf.success_signals)
        n_fail = len(kf.failure_signals or [])
        n_risks = len(kf.risks or [])
        n_unc = len(kf.uncertainties or [])
        believed = sum(1 for s in kf.success_signals if s.status.value == "believed")
        print(
            f"  {gid}: success={n_succ} ({believed} believed)  "
            f"failure={n_fail}  risks={n_risks}  uncertainties={n_unc}"
        )
    return 0


# --- dispatcher -----------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="praxis",
        description=(
            "Operational knowledge for QA agents. Two modes share one "
            "store + oracle: `praxis regress` runs pre-deploy regression "
            "checks; `praxis explore` hunts off-happy-path. See "
            "experiments/regression_recall/LOCAL_RUN.md for live runs."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Bootstrap .praxis/ in the current dir.")
    init.add_argument("--path", help="Project root (default: cwd).")
    init.add_argument("--base-url", default="http://127.0.0.1:8000")
    init.add_argument("--app", help="App name (default: cwd directory name).")
    init.add_argument("--env", default=None, help="Environment label.")
    init.add_argument("--agent-id", default="praxis-cli")
    init.add_argument("--force", action="store_true",
                       help="Overwrite an existing config (keeps knowledge + events).")
    init.set_defaults(func=_cmd_init)

    learn = sub.add_parser(
        "learn",
        help="Import a seed *.knowledge.yaml for a goal (Phase 1).",
        description=(
            "Phase 1 ships file-based seeding: author the YAML by hand or with "
            "your tool of choice, then `praxis learn <goal-id> --from-file`. "
            "The interactive natural-language flow is Phase 1.5 polish; the "
            "validator (ADR-0004 + ADR-0005) gates either path."
        ),
    )
    learn.add_argument("goal_id", help="Stable id for the goal.")
    learn.add_argument("--from-file", required=True,
                        help="Path to a *.knowledge.yaml to import.")
    learn.set_defaults(func=_cmd_learn)

    regress = sub.add_parser(
        "regress", help="Run R-mode across known goals (pre-deploy check).",
    )
    regress.add_argument("--goal", help="Run only this goal (default: all seeds).")
    regress.add_argument("--budget-tokens", type=int, default=None)
    regress.add_argument("--budget-actions", type=int, default=None)
    regress.add_argument("--stop-on-fail", action="store_true")
    regress.add_argument("--from-file",
                          help="Read agent observations from this JSON file "
                               "instead of prompting on stdin.")
    regress.set_defaults(func=_cmd_regress)

    explore = sub.add_parser(
        "explore", help="Run E-mode against believed knowledge for one goal.",
    )
    explore.add_argument("--goal", required=False,
                          help="Goal to explore (required at run time).")
    explore.add_argument("--budget-tokens", type=int, default=None)
    explore.add_argument("--budget-actions", type=int, default=None)
    explore.add_argument("--happy-path", nargs="*", default=None,
                          help="URLs the happy path visits "
                               "(for off_path_fraction).")
    explore.add_argument("--from-file",
                          help="Read agent observations from this JSON file "
                               "instead of prompting on stdin.")
    explore.set_defaults(func=_cmd_explore)

    review = sub.add_parser(
        "review",
        help="Show contested observations (read-only in Phase 1).",
    )
    review.add_argument("--goal", help="Restrict to one goal.")
    review.set_defaults(func=_cmd_review)

    status = sub.add_parser(
        "status",
        help="Print a believed-knowledge summary for all goals.",
    )
    status.set_defaults(func=_cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# Used as the `praxis` console-script entry point in pyproject.toml.
if __name__ == "__main__":
    raise SystemExit(main())


# Suppress unused-import warnings for the surface the CLI re-exports as a
# convenience for `python -m praxis.cli ...`:
_ = (datetime, timezone, ObservedSignal)
