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
from ..merge import contested_candidates, project_candidates
from ..model import KnowledgeFile, Target, dump, load
from ..resources import iter_skill_files, skills_root
from ..runner import (
    explore_engine,
    regress_engine,
    regress_failed,
    write_junit_xml,
    write_markdown_report,
)
from ..store import (
    RUNS_SUBDIR,
    CandidateFileStore,
    ObservedSignal,
    RunsEventStore,
    new_run_id,
)


# --- project discovery ----------------------------------------------------


CONFIG_NAME = "config.yaml"
PROJECT_DIR = ".praxis"
SECRETS_FILE = ".praxis.secrets"
PRAXISIGNORE_NAME = ".praxisignore"
SKILLS_INSTALL_DIR = Path(".claude") / "skills"

# The two ignore lines `praxis init` appends to the repo root `.gitignore`
# (ADR-0021 decisions 5 and 6). `runs/` is the gitignored, regenerable
# per-machine log; `.praxis.secrets` is the credentials channel that must never
# be committed. Both are appended idempotently (never duplicated on re-init).
GITIGNORE_RUNS_LINE = f"{PROJECT_DIR}/{RUNS_SUBDIR}/"
GITIGNORE_SECRETS_LINE = SECRETS_FILE


class ProjectContext:
    """Resolved layout for one `.praxis/` project: paths + config.

    Source of truth for where seeds live, where the per-machine event log goes
    (ADR-0021 `runs/<timestamp>/`), what base URL the runners reference.
    Discovered by walking up from cwd to the first directory containing
    `.praxis/config.yaml`.

    The committed tree is `config.yaml`, `knowledge/`, `candidates/`, and
    `.praxisignore`; the per-machine append-only event log lives under the
    gitignored `runs/<timestamp>/` and is the local source of truth that the
    projection folds into believed / contested state (ADR-0001, ADR-0021
    decision 3).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / PROJECT_DIR
        self.config_path = self.dir / CONFIG_NAME
        self.knowledge_dir = self.dir / "knowledge"
        self.candidates_dir = self.dir / "candidates"
        self.runs_dir = self.dir / RUNS_SUBDIR
        self.praxisignore_path = self.dir / PRAXISIGNORE_NAME
        self.config = yaml.safe_load(self.config_path.read_text()) or {}
        # One run id per CLI invocation: every `store()` call on this context
        # writes into the SAME `runs/<run_id>/` subtree, while reads fold across
        # all run subtrees. Lazily assigned on the first `store()` call.
        self._run_id: str | None = None
        self._store: RunsEventStore | None = None
        self._candidate_files: CandidateFileStore | None = None

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

    def run_dir(self) -> Path:
        """The current invocation's `runs/<timestamp>/` directory, created lazily.

        Holds the per-run raw event log plus any per-run artifacts (reports).
        The directory is gitignored (init writes the ignore line); the run id is
        fixed for the lifetime of this context so all writes in one CLI
        invocation share one subtree.
        """
        if self._run_id is None:
            self._run_id = new_run_id()
        rd = self.runs_dir / self._run_id
        rd.mkdir(parents=True, exist_ok=True)
        return rd

    def store(self) -> RunsEventStore:
        """Per-machine append-only event log spread over `runs/<timestamp>/`.

        Writes land in this invocation's run subtree; reads fold across every
        run subtree so the believed-state projection sees the whole log even
        across separate CLI invocations (ADR-0021 decision 3).
        """
        if self._store is None:
            # Touch the current run dir so the store has a stable write target.
            self.run_dir()
            assert self._run_id is not None
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self._store = RunsEventStore(str(self.runs_dir), self._run_id)
        return self._store

    def candidate_files(self) -> CandidateFileStore:
        """The committed candidate tree under `.praxis/candidates/` (ADR-0021).

        This is the shared, git-pulled / git-pushed contested store, distinct
        from the per-machine event log under the gitignored `runs/` tree. One
        file per observation event id keeps concurrent adds across machines
        merge-safe (ADR-0021 decision 4); `praxis explore` writes accepted
        candidates here and `praxis review` reads the aggregate queue from here.
        """
        if self._candidate_files is None:
            self.candidates_dir.mkdir(parents=True, exist_ok=True)
            self._candidate_files = CandidateFileStore(self.candidates_dir)
        return self._candidate_files

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


_PRAXISIGNORE_TEMPLATE = (
    "# Paths Praxis operations must not read or write (analogous to .gitignore).\n"
    "# One pattern per line; the committed exclusion set is shared with the team\n"
    "# (ADR-0021 decision 1).\n"
)


def _append_gitignore_lines(repo_root: Path, lines: list[str]) -> list[str]:
    """Append each ignore line to the repo `.gitignore` exactly once.

    Creates the file if absent. A line already present (after stripping
    whitespace) is never duplicated, so re-running `praxis init` is idempotent.
    Returns the lines that were actually added (empty on a no-op re-init). The
    secrets ignore line is written here so `.praxis.secrets` is gitignored
    BEFORE any secret could be written (ADR-0021 decisions 5 and 6).
    """
    gitignore = repo_root / ".gitignore"
    existing: list[str] = []
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8").splitlines()
    present = {ln.strip() for ln in existing}
    to_add = [ln for ln in lines if ln.strip() not in present]
    if not to_add:
        return []
    # Preserve the trailing-newline shape: append after the existing content.
    body = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if body and not body.endswith("\n"):
        body += "\n"
    if body and not body.endswith("\n\n"):
        body += "\n"
    body += "# Praxis (ADR-0021): per-machine run logs and the local secrets file.\n"
    body += "\n".join(to_add) + "\n"
    gitignore.write_text(body, encoding="utf-8")
    return to_add


def _scaffold_skills(repo_root: Path) -> int:
    """Copy the packaged Praxis skills into the project's `.claude/skills/`.

    ADR-0021 decision 5: `praxis init` unpacks the skills shipped as package
    data (ADR-0020) into the consuming project. The skill files come from
    `praxis.resources.iter_skill_files`, which resolves them both from an
    installed wheel and from the src tree. The package-relative subpath of each
    skill (for example `praxis/teach/SKILL.md`) is preserved under
    `.claude/skills/`. Returns the number of files written.
    """
    dest_root = repo_root / SKILLS_INSTALL_DIR
    src_root = Path(str(skills_root()))
    written = 0
    for src in iter_skill_files():
        rel = src.relative_to(src_root)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        written += 1
    return written


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path or Path.cwd()).resolve()
    pdir = root / PROJECT_DIR
    if pdir.exists() and not args.force:
        print(f"{pdir} already exists. Re-run with --force to overwrite the "
              f"config (knowledge + candidates + runs are preserved).",
              file=sys.stderr)
        return 2
    # The committed tree (ADR-0021 decision 1): config + knowledge + candidates
    # + .praxisignore travel by git; runs/ is the gitignored per-machine log.
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "knowledge").mkdir(exist_ok=True)
    (pdir / "candidates").mkdir(exist_ok=True)
    (pdir / RUNS_SUBDIR).mkdir(exist_ok=True)
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
    praxisignore = pdir / PRAXISIGNORE_NAME
    if not praxisignore.exists():
        praxisignore.write_text(_PRAXISIGNORE_TEMPLATE, encoding="utf-8")

    # Gitignore the per-machine run logs and the secrets file. This runs on
    # every init (idempotent), so a re-init never duplicates the lines and
    # `.praxis.secrets` is gitignored before any secret could be written.
    added = _append_gitignore_lines(
        root, [GITIGNORE_RUNS_LINE, GITIGNORE_SECRETS_LINE]
    )

    # Scaffold the local-brain Claude Code skills from package data.
    n_skills = _scaffold_skills(root)

    print(f"initialized praxis project at {pdir}")
    print(f"  config:     {pdir / CONFIG_NAME}")
    print(f"  knowledge:  {pdir / 'knowledge'}/   (committed; seed goals here)")
    print(f"  candidates: {pdir / 'candidates'}/  (committed; contested queue)")
    print(f"  runs:       {pdir / RUNS_SUBDIR}/        (gitignored per-machine log)")
    print(f"  ignore set: {pdir / PRAXISIGNORE_NAME}")
    if added:
        print(f"  .gitignore: added {', '.join(added)}")
    else:
        print("  .gitignore: already covers runs/ + secrets (no change)")
    print(f"  skills:     {root / SKILLS_INSTALL_DIR}/  ({n_skills} file(s) scaffolded)")
    print()
    print("Credentials go in a gitignored .praxis.secrets at the repo root "
          "(KEY=value), never inside .praxis/ (ADR-0021 decision 6).")
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
        brain = _executor_from_file(Path(args.from_file))
    else:
        brain = _executor_from_paste

    # The console surface drives the SAME engine a skill driver calls
    # (ADR-0019 decision 4); the only difference is which brain the seam
    # carries (here: the paste/file executor; in a skill: the local Claude
    # session). The verdict comes back identical for the same store + brain
    # output.
    results = regress_engine(
        adapter, brain, goals,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        budget_tokens=args.budget_tokens,
        budget_actions=args.budget_actions,
        stop_on_fail=args.stop_on_fail,
    )

    run_dir = proj.run_dir()
    last_md = run_dir / "last-regress.md"
    last_xml = run_dir / "last-regress.xml"
    write_markdown_report(results, last_md)
    write_junit_xml(results, last_xml)
    print(f"\nreport: {last_md}")
    print(f"junit:  {last_xml}")
    return 1 if regress_failed(results) else 0


def _cmd_explore(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    if not args.goal:
        print("praxis explore requires --goal (one goal per run)", file=sys.stderr)
        return 2
    if args.from_file:
        brain = _executor_from_file(Path(args.from_file))
    else:
        brain = _executor_from_paste

    # Snapshot the candidate event ids already in the per-machine log for this
    # goal, so after the run we can mirror ONLY this run's newly written
    # candidate events into the committed tree (ADR-0021 decision 4). The runner
    # / adapter rules are unchanged: they still append to the per-machine run
    # log (the local source of truth); the mirror copies each accepted event,
    # by its content-addressable id, into `.praxis/candidates/<goal>/<id>.yaml`,
    # the shared, git-pushed contested store. The mirror runs inside the engine
    # as the committed sink, so a skill driver gets the same committed count.
    store = proj.store()
    before_ids = {ev.event_id for ev in store.read_candidates(args.goal)}

    def _commit_new_candidates(goal: str) -> list[Path]:
        new_events = [
            ev for ev in store.read_candidates(goal)
            if ev.event_id not in before_ids
        ]
        return proj.candidate_files().write_all(new_events)

    happy = args.happy_path or []
    # The console surface drives the SAME engine a skill driver calls; the seam
    # carries the paste/file executor here, the local Claude session in a skill.
    outcome = explore_engine(
        adapter, brain, args.goal,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        happy_path_urls=happy,
        budget_tokens=args.budget_tokens,
        budget_actions=args.budget_actions,
        committed_sink=_commit_new_candidates,
    )
    result = outcome.result
    n_committed = len(outcome.committed_paths)
    print(f"\ngoal:                  {result.goal_id}")
    print(f"actions:               {result.actions}")
    print(f"tokens:                {result.tokens if result.tokens is not None else '-'}")
    print(f"wall:                  {result.wall_seconds:.2f}s")
    print(f"off_path_fraction:     {result.off_path_fraction:.2f}")
    print(f"candidate_observations: {len(result.candidate_observations)}")
    print(f"new_risks:             {len(result.new_risks)}  (all contested per ADR-0008)")
    print(f"new_uncertainties:     {len(result.new_uncertainties)}")
    print(f"committed_candidates:  {n_committed}  "
          f"(.praxis/candidates/{result.goal_id}/, one file per observation)")
    for o in result.candidate_observations:
        print(f"  [{o.kind}/{o.type.value}] {o.value}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Surface the human review queue (ADR-0014).

    Two surfaces:
      1. Contested SIGNALS in the projection (legacy Phase-1 read-only
         digest, unchanged).
      2. Contested CANDIDATE events (Phase-2 addition): agent-proposed
         risks / uncertainties that have not yet been corroborated by an
         independent source. Each entry shows provenance, the source ids
         involved, and the operator path for promotion - which is to
         append a NEW seed event, never to edit the candidate in place
         (ADR-0001 + ADR-0014 sec 4).
    """
    proj = discover_project()
    adapter = proj.adapter()
    seeds = proj.seeds()
    # The committed candidate tree is the aggregate queue source (ADR-0021
    # decision 4): a teammate's `git pull` brought their candidate files here.
    # Review folds the committed tree, NOT the per-machine run log, so the queue
    # reflects the shared contested store. A goal can have committed candidates
    # without a seed (a discovered finding), so the goal set is the union of
    # seeded goals and goals that already carry committed candidates.
    candidate_files = proj.candidate_files()
    if args.goal:
        goal_ids = [args.goal]
    else:
        goal_ids = sorted(set(seeds.keys()) | set(candidate_files.goals()))
    if not goal_ids:
        print("no goals to review.", file=sys.stderr)
        return 2
    any_contested = False
    for gid in goal_ids:
        contested_succ: list[Any] = []
        contested_fail: list[Any] = []
        kf = adapter.read_knowledge(gid)
        if kf is not None:
            contested_succ = [s for s in kf.success_signals
                              if s.status.value == "contested"]
            contested_fail = [s for s in (kf.failure_signals or [])
                              if s.status.value == "contested"]
        cand_events = candidate_files.read(gid)
        projected = project_candidates(
            cand_events, goal_id=gid, seed=seeds.get(gid),
        )
        contested_cands = contested_candidates(projected)
        if not (contested_succ or contested_fail or contested_cands):
            continue
        any_contested = True
        print(f"\n## {gid}")
        for s in contested_succ:
            print(f"  [contested success / {s.type.value}] {s.value}  "
                  f"(confidence={s.confidence:.2f}, by {s.provenance.source_id})")
        for s in contested_fail:
            print(f"  [contested failure / {s.type.value}] {s.value}  "
                  f"(confidence={s.confidence:.2f}, by {s.provenance.source_id})")
        for pc in contested_cands:
            if pc.risk is not None:
                trig = pc.risk.trigger
                trig_str = (
                    f"{trig.method} {trig.path}" if trig.kind == "http"
                    else f"{trig.n}x {trig.action}"  # SequenceTrigger
                )
                src_list = ", ".join(sorted(pc.distinct_source_ids))
                print(
                    f"  [contested candidate_risk / {trig.kind}] {pc.candidate_id}: "
                    f"{pc.risk.description}\n"
                    f"     trigger: {trig_str}  expect: {trig.expect}\n"
                    f"     confidence={pc.risk.confidence:.2f}  "
                    f"sources={{{src_list}}}  "
                    f"events={len(pc.corroborating_events)}"
                )
            elif pc.uncertainty is not None:
                src_list = ", ".join(sorted(pc.distinct_source_ids))
                print(
                    f"  [contested candidate_uncertainty] {pc.candidate_id}: "
                    f"{pc.uncertainty.question}\n"
                    f"     raised_by={pc.uncertainty.raised_by}  "
                    f"sources={{{src_list}}}  "
                    f"events={len(pc.corroborating_events)}"
                )
    if not any_contested:
        print("nothing contested. Nothing to review.")
    else:
        print(
            "\nPromote a candidate by adding a corresponding seed entry "
            "(same id, source_type human/spec) to the goal's "
            "*.knowledge.yaml. The candidate event itself is immutable "
            "(ADR-0001); seed + candidate together satisfy the "
            "diversity rule (ADR-0008) and the next projection will "
            "promote it to `believed`."
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
