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
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .claude_brain import make_claude_brain

import yaml

from ..adapters import BrowserUseAdapter
from ..auth_session import AUTH_DIRNAME
from ..merge import contested_candidates, project_candidates
from ..model import KnowledgeFile, Target, dump, load
from ..resources import iter_skill_files, skills_root
from ..runner import (
    aggregate_run_failed,
    color_agg_verdict,
    explore_aggregate_engine,
    explore_engine,
    format_console_summary,
    format_single_console_summary,
    group_candidates_by_trigger,
    regress_aggregate_engine,
    regress_engine,
    regress_failed,
    write_aggregate_markdown,
    write_candidate_markdown,
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

# The ignore lines `praxis init` appends to the repo root `.gitignore`
# (ADR-0021 decisions 5 and 6; ADR-0026 decisions 2 and 3). `runs/` is the
# gitignored, regenerable per-machine log; `.praxis.secrets` is the credentials
# channel that must never be committed; `.praxis.auth/` is the authenticated-
# session secret channel (a sibling of `.praxis.secrets`) that holds the saved
# Playwright storageState and must never be committed either. All are appended
# idempotently (never duplicated on re-init).
GITIGNORE_RUNS_LINE = f"{PROJECT_DIR}/{RUNS_SUBDIR}/"
GITIGNORE_SECRETS_LINE = SECRETS_FILE
GITIGNORE_AUTH_LINE = f"{AUTH_DIRNAME}/"


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

    @property
    def mcp_config(self) -> str | None:
        """Default Playwright MCP config path for the claude -p console brain
        (ADR-0027), resolved absolute against the project root so it works from
        any cwd. A `--mcp-config` flag overrides this. None when the project
        declares no default (a run then needs the flag, or uses --from-file)."""
        raw = self.config.get("mcp_config")
        if not raw:
            return None
        p = Path(raw)
        return str(p if p.is_absolute() else (self.root / p))

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
    BEFORE any secret could be written (ADR-0021 decisions 5 and 6); the
    `.praxis.auth/` ignore line is written the same way so the saved
    authenticated session is gitignored BEFORE any session could be written
    (ADR-0026 decisions 2 and 3, the gitignore-before-write guarantee).
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
    skill (for example `praxis-teach/SKILL.md`) is preserved under
    `.claude/skills/`. The packaged tree is FLAT, one directory per skill named
    by the skill's frontmatter name, because Claude Code discovers a project
    skill only as `.claude/skills/<name>/SKILL.md`; a nested
    `.claude/skills/praxis/<skill>/` layout is NOT discovered as a slash
    command. Returns the number of files written.
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
        # Default Playwright MCP config for the claude -p console brain
        # (ADR-0027). Set this to a JSON file path (relative to the project root)
        # so `praxis regress` / `praxis explore` need no --mcp-config flag; the
        # flag overrides it. None until the project wires its browser MCP.
        "mcp_config": getattr(args, "mcp_config", None),
    }
    (pdir / CONFIG_NAME).write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    praxisignore = pdir / PRAXISIGNORE_NAME
    if not praxisignore.exists():
        praxisignore.write_text(_PRAXISIGNORE_TEMPLATE, encoding="utf-8")

    # Gitignore the per-machine run logs, the secrets file, and the auth-session
    # directory. This runs on every init (idempotent), so a re-init never
    # duplicates the lines and both `.praxis.secrets` and `.praxis.auth/` are
    # gitignored BEFORE any secret or saved session could be written (ADR-0021
    # decisions 5 and 6; ADR-0026 decisions 2 and 3).
    added = _append_gitignore_lines(
        root, [GITIGNORE_RUNS_LINE, GITIGNORE_SECRETS_LINE, GITIGNORE_AUTH_LINE]
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
        print("  .gitignore: already covers runs/ + secrets + auth session "
              "(no change)")
    print(f"  skills:     {root / SKILLS_INSTALL_DIR}/  ({n_skills} file(s) scaffolded)")
    print()
    print("Credentials go in a gitignored .praxis.secrets at the repo root "
          "(KEY=value), never inside .praxis/ (ADR-0021 decision 6).")
    print(f"Saved auth sessions go in a gitignored {AUTH_DIRNAME}/ at the repo "
          "root (one storageState JSON per role), never inside .praxis/ "
          "(ADR-0026 decisions 2 and 3).")
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


def _select_console_brain(
    args: argparse.Namespace, *, default_mcp_config: str | None = None,
    progress: "Callable[[], tuple[str, str] | None] | None" = None,
) -> Any:
    """Pick the brain that drives a console regress / explore run (ADR-0027
    decision 7).

    Precedence: an explicit `--from-file` wins (deterministic; what the tests
    and the regression-recall harness drive). Otherwise the local `claude -p`
    brain drives the run headless on the user's subscription with NO API key
    (ADR-0027 decisions 3, 5), which is the new DEFAULT replacing the retired
    paste-on-stdin prompt (Finding A). When neither a `--from-file` nor a
    `claude` binary is available, FAIL LOUDLY with an actionable message instead
    of hanging on stdin: a console run with no brain is an error, not a wait.
    The CI API-key brain (ADR-0019, ADR-0024) is unchanged and is wired by CI
    through its own path, not here.

    The Playwright MCP config resolves `--mcp-config` first, else the project's
    `mcp_config` default (`default_mcp_config`, from `.praxis/config.yaml`), so a
    project can declare its MCP once and runs need no flag.
    """
    if args.from_file:
        return _executor_from_file(Path(args.from_file))
    if shutil.which("claude") is None:
        raise SystemExit(
            "no brain available: `claude` is not on PATH and no --from-file was "
            "given. Install Claude Code (the local console brain runs headless on "
            "your subscription, no API key), or pass --from-file PATH with agent "
            "observations."
        )
    return make_claude_brain(
        headed=getattr(args, "headed", False),
        timeout_seconds=args.budget_wall_seconds,
        mcp_config_path=getattr(args, "mcp_config", None) or default_mcp_config,
        progress=progress,
    )


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

    # Live progress label the claude -p spinner reads so the running line reads
    # pytest-style `[i/total] (spin) Running   <goal>   <clock>`. The CLI owns
    # this mutable holder; `on_goal_start` (sequential only) sets the goal, the
    # getter returns None until a goal starts so the brain falls back to its
    # generic line for the from-file / concurrent paths.
    _prog: dict[str, Any] = {"idx": 0, "total": 0, "label": None}

    def _progress_label() -> "tuple[str, str] | None":
        if _prog["label"] is None:
            return None
        return (f"[{_prog['idx']}/{_prog['total']}]", str(_prog["label"]))

    brain = _select_console_brain(
        args, default_mcp_config=proj.mcp_config, progress=_progress_label,
    )

    # Default-all aggregate (ADR-0023 decision 2): no `--goal` runs EVERY
    # believed goal under .praxis/knowledge/ and emits ONE aggregate
    # break-vs-drift report. `praxis regress --goal <name>` keeps the
    # single-goal pass/fail path below.
    if not args.goal:
        goals = sorted(proj.seeds().keys())
        if not goals:
            print("no goals to regress (no seeds in .praxis/knowledge/). "
                  "Run `praxis learn ...` first.", file=sys.stderr)
            return 2
        # Pytest-style: announce the run before driving the goals, then print a
        # live per-goal progress line as each completes (ADR-0027 decision 6).
        # The callback fires in the calling thread (engine -> run_partitioned),
        # so the lines never interleave even under `--jobs > 1`.
        total = len(goals)
        concurrent = bool(args.jobs and args.jobs > 1)
        if concurrent:
            print(f"running {total} goal(s) ({args.jobs} at a time)...")
        else:
            print(f"running {total} goal(s)...")
        progress = {"done": 0, "passed": 0}
        _prog["total"] = total
        use_color = sys.stdout.isatty()

        def _on_goal_start(gid: str) -> None:
            _prog["idx"] += 1
            _prog["label"] = gid

        def _on_goal_done(r: Any) -> None:
            progress["done"] += 1
            if r.verdict.is_ok:
                progress["passed"] += 1
            verdict_tag = color_agg_verdict(
                r.verdict, f"{r.verdict.value:<10}", color=use_color)
            print(f"  [{progress['done']}/{total}] {verdict_tag} "
                  f"{r.goal_id}  ({progress['passed']}/{progress['done']} passed)")

        # The console surface drives the SAME aggregate engine a skill driver
        # calls (ADR-0019 decision 4 + ADR-0023 decision 1). The brain seam
        # carries the file/claude-p executor here; the verdicts come back
        # identical for the same store + brain output. The per-goal budget
        # slice (ADR-0023 decision 7) is applied per goal, not as one shared
        # pool: an exhausted slice is a loud ERROR for that goal. `--jobs` caps
        # concurrency (ADR-0027 decision 4; default 1 = sequential).
        reports = regress_aggregate_engine(
            adapter, brain, goals,
            agent_id=proj.agent_id,
            observed_app_version=proj.observed_app_version,
            budget_tokens_per_goal=args.budget_tokens,
            budget_actions_per_goal=args.budget_actions,
            budget_wall_seconds_per_goal=args.budget_wall_seconds,
            jobs=args.jobs,
            # The rich single in-place running line only makes sense
            # sequentially; under `--jobs > 1` concurrent goals would clobber it,
            # so the label stays off and the brain shows its generic line.
            on_goal_start=None if concurrent else _on_goal_start,
            on_goal_done=_on_goal_done,
        )
        run_dir = proj.run_dir()
        report_md = run_dir / "regress-aggregate.md"
        write_aggregate_markdown(reports, report_md)
        # Per-goal verdict lines were printed live by `_on_goal_done` as each
        # goal completed (ADR-0027 decision 6); the named signal for a non-OK
        # goal is in the final summary below and the markdown report.
        # Pytest-style final summary: the loud PASSED / FAILED banner, the
        # `N passed, N failed, N stale` tally, and every goal that needs action
        # named with its evidence (ADR-0027 decision 6). Verdicts and exit code
        # are unchanged; this is presentation only.
        print(format_console_summary(reports, color=use_color))
        print(f"report: {report_md}")
        # A REGRESSED or ERROR goal fails the run loudly; STALE alone does not
        # (the app changed on purpose, the fix is a human re-seed).
        failed = aggregate_run_failed(reports)
        return 1 if failed else 0

    # Single-goal pass/fail path (unchanged verdict contract).
    goals = [args.goal]
    print(f"running 1 goal: {args.goal}...")
    # One goal: set the running label directly (no aggregate loop / start hook),
    # so the spinner reads `[1/1] Running <goal>` like the aggregate path.
    _prog["total"] = 1
    _prog["idx"] = 1
    _prog["label"] = args.goal
    # The console surface drives the SAME engine a skill driver calls
    # (ADR-0019 decision 4); the only difference is which brain the seam
    # carries (here: the file/claude-p executor; in a skill: the local Claude
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
    # Show the verdict ON THE CONSOLE (ADR-0027 decision 6): a human should see
    # whether the goal passed without opening the markdown report. believed_total
    # is the count the verdict needed all of (ADR-0009); read it from the
    # projection so the line reads "M/N success signals matched".
    kf = adapter.read_knowledge(args.goal)
    believed_total = sum(
        1 for s in (kf.success_signals if kf else [])
        if s.status.value == "believed"
    )
    if results:
        print(format_single_console_summary(
            results[0], believed_total=believed_total,
            color=sys.stdout.isatty()))
    print(f"report: {last_md}")
    return 1 if regress_failed(results) else 0


def _explore_aggregate(
    proj: "ProjectContext",
    brain: Any,
    *,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
    budget_wall_seconds: float | None = None,
    jobs: int = 1,
) -> int:
    """Default-all explore: hunt off-happy-path across EVERY believed goal,
    write candidate files on the committed tree, and emit ONE trigger-grouped
    candidate report (ADR-0023 decisions 2 + 8).

    The console surface drives the SAME aggregate engine a skill driver calls
    (ADR-0019 decision 4 + ADR-0023 decision 1); the brain seam carries the
    paste/file executor here, the local Claude session in a skill. The mirror to
    the committed tree runs inside the engine as the committed sink, so a skill
    driver gets the same committed count.
    """
    goals = sorted(proj.seeds().keys())
    if not goals:
        print("no goals to explore (no seeds in .praxis/knowledge/). "
              "Run `praxis learn ...` first.", file=sys.stderr)
        return 2

    adapter = proj.adapter()
    store = proj.store()
    # Snapshot the candidate event ids already in the per-machine log per goal,
    # so the mirror copies ONLY this run's newly written candidate events into
    # the committed tree (ADR-0021 decision 4), exactly as the single-goal path.
    before_ids = {
        gid: {ev.event_id for ev in store.read_candidates(gid)} for gid in goals
    }

    def _commit_new_candidates(goal: str) -> list[Path]:
        new_events = [
            ev for ev in store.read_candidates(goal)
            if ev.event_id not in before_ids.get(goal, set())
        ]
        return proj.candidate_files().write_all(new_events)

    # Forward the per-goal budget slice (ADR-0023 decision 7): the engine
    # accepts and per-goal-slices these, matching the single-goal explore path
    # and the regress aggregate path. Without this, `praxis explore
    # --budget-tokens N` (no --goal) would silently drop the flag. The wall-time
    # slice is the second budget dimension the regress aggregate also enforces:
    # a goal that exceeds either slice is a loud per-goal ERROR (decision 7).
    outcome = explore_aggregate_engine(
        adapter, brain, goals,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        budget_tokens_per_goal=budget_tokens,
        budget_actions_per_goal=budget_actions,
        budget_wall_seconds_per_goal=budget_wall_seconds,
        committed_sink=_commit_new_candidates,
        jobs=jobs,
    )

    # Build the trigger-grouped report from the committed candidate tree, the
    # shared store `praxis review` also reads (ADR-0021 decision 4). Reading the
    # committed tree (not the per-machine log) keeps the report consistent with
    # what a teammate sees after `git pull`.
    candidate_files = proj.candidate_files()
    seeds = proj.seeds()
    groups: list[Any] = []
    for gid in goals:
        events = candidate_files.read(gid)
        if events:
            groups.extend(
                group_candidates_by_trigger(events, seed=seeds.get(gid))
            )

    off_path = {
        oc.goal_id: oc.result.off_path_fraction
        for oc in outcome.outcomes if oc.ok and oc.result is not None
    }
    errors = {
        oc.goal_id: oc.error
        for oc in outcome.outcomes if oc.error is not None
    }

    run_dir = proj.run_dir()
    report_md = run_dir / "explore-candidates.md"
    write_candidate_markdown(
        groups, report_md, off_path_fractions=off_path, errors=errors,
    )

    n_believed = sum(1 for g in groups if g.believed)
    print(f"\ngoals explored:        {len(goals) - len(errors)}/{len(goals)}")
    print(f"committed candidates:  {len(outcome.committed_paths)}  "
          f"(.praxis/candidates/<goal>/, one file per observation)")
    print(f"findings (by trigger): {len(groups)}  "
          f"({n_believed} believed / {len(groups) - n_believed} contested)")
    for gid in sorted(off_path):
        print(f"  off_path_fraction[{gid}]: {off_path[gid]:.2f}")
    for gid in sorted(errors):
        print(f"  ERROR {gid}: {errors[gid]}", file=sys.stderr)
    print(f"\nreport: {report_md}")
    # A goal that errored (the brain threw, the app would not load) is a loud
    # non-OK ERROR that fails the run, never a silent skip (ADR-0023 forbidden
    # alternatives + decision 4). This mirrors the sibling regress aggregate's
    # `return 1 if failed else 0` loud-failure contract; explore must not be more
    # lenient than the single-goal path, which lets brain exceptions crash.
    if errors:
        named = ", ".join(sorted(errors))
        print(f"ERROR: {named} could not be explored and fail the run.",
              file=sys.stderr)
        return 1
    return 0


def _cmd_explore(args: argparse.Namespace) -> int:
    proj = discover_project()
    adapter = proj.adapter()
    brain = _select_console_brain(args, default_mcp_config=proj.mcp_config)

    # Default-all aggregate (ADR-0023 decision 2): no `--goal` hunts off-happy-
    # path across EVERY believed goal under .praxis/knowledge/, writes candidate
    # files on the committed tree, and emits ONE report grouped by trigger.
    if not args.goal:
        return _explore_aggregate(
            proj, brain,
            budget_tokens=args.budget_tokens,
            budget_actions=args.budget_actions,
            budget_wall_seconds=args.budget_wall_seconds,
            jobs=args.jobs,
        )

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
    init.add_argument("--mcp-config", default=None,
                       help="Default Playwright MCP config path for the claude -p "
                            "console brain (ADR-0027); a run's --mcp-config "
                            "overrides it.")
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
    regress.add_argument("--goal", help="Run only this goal (default: aggregate "
                                        "over all seeds).")
    regress.add_argument("--budget-tokens", type=int, default=None,
                          help="Per-goal token slice in aggregate mode "
                               "(ADR-0023 decision 7).")
    regress.add_argument("--budget-actions", type=int, default=None)
    regress.add_argument("--budget-wall-seconds", type=float, default=None,
                          help="Per-goal wall-time slice (s) in aggregate mode; "
                               "a goal that exceeds it is a loud ERROR.")
    regress.add_argument("--jobs", type=int, default=1,
                          help="How many goals to run concurrently in aggregate "
                               "mode (ADR-0027 decision 4; default 1 = "
                               "sequential). Auth-subject goals run serially.")
    regress.add_argument("--headed", action="store_true",
                          help="Show the browser the claude -p brain drives "
                               "(default headless, ADR-0027 decision 5).")
    regress.add_argument("--mcp-config",
                          help="Path to the Playwright MCP config the claude -p "
                               "brain uses to drive the browser (ADR-0027).")
    regress.add_argument("--stop-on-fail", action="store_true")
    regress.add_argument("--from-file",
                          help="Read agent observations from this JSON file "
                               "instead of prompting on stdin.")
    regress.set_defaults(func=_cmd_regress)

    explore = sub.add_parser(
        "explore", help="Run E-mode against believed knowledge (default: all goals).",
    )
    explore.add_argument("--goal", required=False,
                          help="Explore only this goal (default: aggregate over "
                               "all seeds, one report grouped by trigger).")
    explore.add_argument("--budget-tokens", type=int, default=None,
                          help="Per-goal token slice in aggregate mode "
                               "(ADR-0023 decision 7).")
    explore.add_argument("--budget-actions", type=int, default=None)
    explore.add_argument("--budget-wall-seconds", type=float, default=None,
                          help="Per-goal wall-time slice (s) in aggregate mode; "
                               "a goal that exceeds it is a loud ERROR.")
    explore.add_argument("--jobs", type=int, default=1,
                          help="How many goals to run concurrently in aggregate "
                               "mode (ADR-0027 decision 4; default 1 = "
                               "sequential). Auth-subject goals run serially.")
    explore.add_argument("--headed", action="store_true",
                          help="Show the browser the claude -p brain drives "
                               "(default headless, ADR-0027 decision 5).")
    explore.add_argument("--mcp-config",
                          help="Path to the Playwright MCP config the claude -p "
                               "brain uses to drive the browser (ADR-0027).")
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
