"""`praxis` CLI dispatcher.

Six verbs: init, learn, regress, explore, review, status. Each is a thin
glue between a discovered project context (`.praxis/` upward from cwd) and
the runtime-agnostic core (model + store + merge + oracle + runner).

The CLI itself never drives a browser; the brain does, through a Playwright
MCP. regress / explore are self-driving console test runners: by default they
shell out to the local Claude Code CLI headless (`claude -p`) on the user's
subscription with no API key (ADR-0027), so a bare `praxis regress` runs every
believed goal and prints a pytest-style OK / REGRESSED / STALE / AUTH-EXPIRED
summary (ADR-0023, ADR-0026). `--from-file PATH` feeds agent observations as
JSON instead (deterministic; what the tests and the regression-recall harness
drive). The same brain seam is what CI wires its API-key agent into (ADR-0024).

Stdlib argparse + pyyaml only (AGENTS.md: ask before adding deps).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
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
    AggregateVerdict,
    aggregate_run_failed,
    classify_goal,
    color_agg_verdict,
    explore_aggregate_engine,
    explore_engine,
    format_console_summary,
    format_single_console_summary,
    group_candidates_by_trigger,
    regress_aggregate_engine,
    regress_engine,
    regress_failed,
    write_aggregate_junit_xml,
    write_aggregate_markdown,
    write_candidate_markdown,
    write_junit_xml,
    write_markdown_report,
)
from ..runner.report import format_environment_annotation
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

# `praxis init` scaffolds a default Playwright MCP config at the repo root so the
# console brain (`praxis regress` / `praxis explore` via claude -p) drives a
# browser with NO manual MCP setup after `pip install` (ADR-0027). It is plain
# project config (no secret), so it is committed, not gitignored. A run's
# `--mcp-config` or an existing file always wins; init never overwrites one.
MCP_CONFIG_NAME = "playwright-mcp.json"
_MCP_CONFIG_TEMPLATE = (
    "{\n"
    '  "mcpServers": {\n'
    '    "playwright": {\n'
    '      "type": "stdio",\n'
    '      "command": "npx",\n'
    '      "args": ["-y", "@playwright/mcp@latest", "--headless"],\n'
    '      "env": {}\n'
    "    }\n"
    "  }\n"
    "}\n"
)

# The ignore lines `praxis init` appends to the repo root `.gitignore`
# (ADR-0021 decisions 5 and 6; ADR-0026 decisions 2 and 3). `runs/` is the
# gitignored, regenerable per-machine log; `.praxis.secrets` is the credentials
# channel that must never be committed; `.praxis.auth/` is the authenticated-
# session secret channel (a sibling of `.praxis.secrets`) that holds the saved
# Playwright storageState and must never be committed either (the directory
# pattern covers the per-env `.praxis.auth/<env>/` subdirs of ADR-0035
# decision 7 too); `.praxis.secrets.*` covers the per-environment secrets
# overlay files `.praxis.secrets.<env>` (ADR-0035 decision 7) so a per-env
# credential can never be committed either. All are appended idempotently
# (never duplicated on re-init), so re-running init on a pre-ADR-0035 repo
# adds exactly the missing overlay line.
GITIGNORE_RUNS_LINE = f"{PROJECT_DIR}/{RUNS_SUBDIR}/"
GITIGNORE_SECRETS_LINE = SECRETS_FILE
GITIGNORE_SECRETS_OVERLAYS_LINE = f"{SECRETS_FILE}.*"
GITIGNORE_AUTH_LINE = f"{AUTH_DIRNAME}/"

# The env-var surface of the brain-model pin (ADR-0034). For CI: the runner
# exports it and every `praxis regress` / `praxis explore` in that pipeline runs
# the claude -p brain with that model, mirroring the env-over-file precedence of
# the ADR-0021 secrets channel. The model name is NOT a secret; it is an
# operational input to the brain and never enters knowledge.
BRAIN_MODEL_ENV = "PRAXIS_BRAIN_MODEL"

# The env-var surface of the per-run environment selection (ADR-0035). For CI:
# a job matrix exports PRAXIS_ENV per leg and every `praxis regress` /
# `praxis explore` in that leg runs against that declared environment,
# mirroring the ADR-0034 flag > env var > committed config precedence. The
# environment NAME is operational provenance, never knowledge: it is stamped
# onto run records at run time and never enters an assertion.
PRAXIS_ENV_VAR = "PRAXIS_ENV"

# A declared project's run directory is `runs/<timestamp>__<env>/` (ADR-0035
# decision 8): the sortable timestamp prefix is unchanged and the suffix makes
# "the last prod run" findable with `ls`. The env name in the DIRECTORY NAME is
# sanitized conservatively: declared names come from the config map, but a
# filesystem-hostile character in one (slash, colon, space) must not break or
# escape the runs/ tree, so anything outside [A-Za-z0-9._-] maps to "-" here.
# Reports, banners, and events carry the environment name verbatim; only the
# dirname is sanitized.
_ENV_DIRNAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _env_dirname(name: str) -> str:
    """The run-dir-safe form of a declared environment name (see above)."""
    return _ENV_DIRNAME_UNSAFE.sub("-", name)


# What `praxis init --environment NAME=URL` accepts as a NAME. Stricter than
# `_env_dirname` on purpose: a declared name enters file paths
# (`runs/<ts>__<env>/`, `.praxis.auth/<env>/`, `.praxis.secrets.<env>`) AND
# env-var names (`PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, the name uppercased), and
# only [A-Za-z0-9_] round-trips through BOTH channels (`-` and `.` survive a
# path but are not legal in a POSIX env-var name). Rejecting at init time
# keeps a non-round-tripping name from ever entering a committed config; a
# hand-edited config is still sanitized per-channel at use time.
_ENV_NAME_OK = re.compile(r"^[A-Za-z0-9_]+$")

# `praxis init` scaffolds the `brain_model` key COMMENTED OUT (ADR-0034): the
# pin is discoverable in the committed config without forcing a model choice,
# and no model name is ever hardcoded as a default (the default stays whatever
# the claude CLI defaults to; model names rot). The block is pure YAML comments,
# so a parsed config has NO `brain_model` key until a human uncomments it.
_BRAIN_MODEL_CONFIG_COMMENT = (
    "# brain_model: pin the model the claude -p console brain runs with, so\n"
    "# every teammate and CI regress with the same brain capability. Pin\n"
    "# deliberately: a cheaper/faster model can mis-navigate the app and\n"
    "# false-alarm (see docs/examples/ci.md). The --model flag and the\n"
    "# PRAXIS_BRAIN_MODEL env var override it per run; unset = the claude CLI\n"
    "# default (ADR-0034). Uncomment and set to enable:\n"
    "# brain_model: <model-name>\n"
)

# `praxis init` (without `--environment`) scaffolds the `environments` map
# COMMENTED OUT, the ADR-0034 `brain_model` discoverability pattern: the
# multi-env mechanism is visible in every committed config without changing
# any undeclared-project behavior. Pure YAML comments, so a parsed config has
# NO `environments` key until a human uncomments it (and an undeclared
# project stays byte-identical in behavior, the ADR-0035 zero-ceremony bar).
_ENVIRONMENTS_CONFIG_COMMENT = (
    "# environments: declare the deployments this product runs on, so ONE set\n"
    "# of goal YAMLs runs against each of them (ADR-0035). Pick one per run\n"
    "# with --env on regress/explore/status, the PRAXIS_ENV env var, or the\n"
    "# committed default_env. Declaring a map shadows the top-level base_url /\n"
    "# environment / observed_app_version keys; per-env credentials go in\n"
    "# .praxis.secrets.<env>, per-env sessions in .praxis.auth/<env>/.\n"
    "# Uncomment and edit to enable:\n"
    "# environments:\n"
    "#   dev2:\n"
    "#     base_url: https://dev2.example.com\n"
    "#     observed_app_version: 2.6.0\n"
    "#   prod:\n"
    "#     base_url: https://example.com\n"
    "# default_env: dev2\n"
)


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
        # The environment THIS invocation runs against (ADR-0035), pinned by
        # `select_environment`. None until selected, and always None on an
        # undeclared project, so every env-aware property below degenerates to
        # today's single-deployment reads.
        self._selected_env: str | None = None
        # One run id per CLI invocation: every `store()` call on this context
        # writes into the SAME `runs/<run_id>/` subtree, while reads fold across
        # all run subtrees. Lazily assigned on the first `store()` call.
        self._run_id: str | None = None
        # The run DIRECTORY name: the run id, suffixed `__<env>` when an
        # environment is selected (ADR-0035 decision 8). Pinned on first use so
        # the run dir and the store write target can never diverge within one
        # invocation.
        self._run_dirname: str | None = None
        self._store: RunsEventStore | None = None
        self._candidate_files: CandidateFileStore | None = None

    @property
    def environments(self) -> dict[str, dict[str, Any]] | None:
        """The committed per-deployment map (ADR-0035 decision 1), or None on
        an undeclared project. Shape: name -> {base_url: <str>, optionally
        observed_app_version: <str>}. A malformed map fails loudly at read
        time instead of mid-run: silently treating it as undeclared would
        flip the project back to single-env behavior without a trace."""
        raw = self.config.get("environments")
        if not raw:
            return None
        if not isinstance(raw, dict):
            raise SystemExit(
                f"{self.config_path}: `environments` must be a map of "
                "name -> {base_url: <url>, ...} (ADR-0035)."
            )
        out: dict[str, dict[str, Any]] = {}
        for name, entry in raw.items():
            if not isinstance(entry, dict) or not entry.get("base_url"):
                raise SystemExit(
                    f"{self.config_path}: environments.{name} must be a map "
                    "with a non-empty base_url (ADR-0035)."
                )
            out[str(name)] = entry
        return out

    @property
    def default_env(self) -> str | None:
        """The committed per-project environment default teammates share
        (ADR-0035 decision 2). Empty string counts as unset."""
        raw = self.config.get("default_env")
        return str(raw) if raw else None

    @property
    def legacy_env(self) -> str | None:
        """Which declared environment pre-declaration events (those with
        `environment: None`) are attributed to, as a projection INPUT - no
        event file is ever rewritten (ADR-0035 decision 4, the ADR-0013
        caller-supplied-anchor posture). Parsed here; the read-side use lives
        in the adapter partition. Empty string counts as unset."""
        raw = self.config.get("legacy_env")
        return str(raw) if raw else None

    def select_environment(
        self, flag: str | None = None, *, unresolved_ok: bool = False,
    ) -> tuple[str | None, str | None]:
        """Resolve and pin THIS invocation's environment (ADR-0035 decision 2).

        Returns `(name, source)` exactly like `_resolve_brain_model`;
        `(None, None)` on an undeclared project. Once a name is selected,
        `base_url`, `environment`, and `observed_app_version` read from the
        selected entry of the declared map; the now-shadowed top-level keys
        are ignored in declared mode. Loud-error cases live in `_resolve_env`;
        `unresolved_ok` is the read-only `status` posture (see there).
        """
        name, source = _resolve_env(
            flag, self.environments, self.default_env,
            unresolved_ok=unresolved_ok,
        )
        self._selected_env = name
        return name, source

    @property
    def base_url(self) -> str:
        if self._selected_env is not None:
            envs = self.environments
            assert envs is not None
            return str(envs[self._selected_env]["base_url"])
        return self.config.get("base_url", "http://127.0.0.1:8000")

    @property
    def app(self) -> str:
        return self.config.get("app", "praxis-target")

    @property
    def environment(self) -> str | None:
        if self._selected_env is not None:
            return self._selected_env
        return self.config.get("environment")

    @property
    def agent_id(self) -> str:
        return self.config.get("agent_id", "praxis-cli")

    @property
    def observed_app_version(self) -> str | None:
        if self._selected_env is not None:
            envs = self.environments
            assert envs is not None
            raw = envs[self._selected_env].get("observed_app_version")
            return str(raw) if raw else None
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

    @property
    def brain_model(self) -> str | None:
        """The committed per-project model pin for the claude -p console brain
        (ADR-0034), or None when the key is absent / empty (the claude CLI's
        own default; no `--model` is appended). The `--model` flag and the
        `PRAXIS_BRAIN_MODEL` env var override it per run. The value is passed
        through verbatim: the claude CLI is the authority on model names."""
        raw = self.config.get("brain_model")
        return str(raw) if raw else None

    def target(self) -> Target:
        return Target(app=self.app, environment=self.environment)

    def _run_dir_name(self) -> str:
        """The current invocation's run directory NAME, pinned on first use.

        With an environment selected the name is `<timestamp>__<env>` (ADR-0035
        decision 8: the sortable timestamp prefix keeps run dirs ordered, the
        suffix makes "the last prod run" findable with `ls`; the env name is
        sanitized for the dirname only, see `_env_dirname`). On an undeclared
        project (no selection) the name is the bare timestamp, byte-identical
        to pre-ADR-0035. Pinning on first use means the run dir and the store
        write target always agree within one invocation.
        """
        if self._run_id is None:
            self._run_id = new_run_id()
        if self._run_dirname is None:
            if self._selected_env is not None:
                self._run_dirname = (
                    f"{self._run_id}__{_env_dirname(self._selected_env)}"
                )
            else:
                self._run_dirname = self._run_id
        return self._run_dirname

    def run_dir(self) -> Path:
        """The current invocation's `runs/<timestamp>/` directory (suffixed
        `__<env>` when an environment is selected, ADR-0035 decision 8),
        created lazily.

        Holds the per-run raw event log plus any per-run artifacts (reports).
        The directory is gitignored (init writes the ignore line); the run id is
        fixed for the lifetime of this context so all writes in one CLI
        invocation share one subtree.
        """
        rd = self.runs_dir / self._run_dir_name()
        rd.mkdir(parents=True, exist_ok=True)
        return rd

    def store(self) -> RunsEventStore:
        """Per-machine append-only event log spread over `runs/<timestamp>/`.

        Writes land in this invocation's run subtree; reads fold across every
        run subtree - suffixed (`<timestamp>__<env>`) and unsuffixed alike,
        since `RunsEventStore` treats run-dir names opaquely (ADR-0035
        decision 8) - so the believed-state projection sees the whole log even
        across separate CLI invocations (ADR-0021 decision 3).
        """
        if self._store is None:
            # Touch the current run dir so the store has a stable write target.
            self.run_dir()
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self._store = RunsEventStore(str(self.runs_dir), self._run_dir_name())
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
            # ADR-0035 decisions 4 + 5: evidence partitions by the SELECTED
            # environment (the `select_environment` pin; None on an undeclared
            # project = no filter, today's reads exactly), NOT the legacy
            # single-env `environment` config label, which is a prompt label
            # and never a partition key - mirroring how session reuse scopes
            # by the selected name (decision 7).
            environment=self._selected_env,
            legacy_env=self.legacy_env,
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


def _parse_init_environments(
    specs: list[str], default_env: str | None,
) -> dict[str, dict[str, str]]:
    """Parse the repeated `--environment NAME=URL` flags into the committed
    `environments` map (ADR-0035 decisions 1 and 9).

    Every mistake is loud at init time, BEFORE anything touches disk: a spec
    without `NAME=URL` shape, a name that would not round-trip through the
    per-env file paths and env-var names (see `_ENV_NAME_OK`), a duplicate
    name (including two names that collide once uppercased into
    `PRAXIS_AUTH_STATE_<ENV>_<ROLE>`), and a `--default-env` that does not
    name a declared `--environment`. A single entry with no `--default-env`
    is fine: the single-entry map auto-selects at run time (decision 2).
    """
    if not specs:
        raise SystemExit(
            "praxis init: --default-env requires at least one "
            "--environment NAME=URL (ADR-0035)."
        )
    envs: dict[str, dict[str, str]] = {}
    seen_upper: dict[str, str] = {}
    for spec in specs:
        name, sep, url = spec.partition("=")
        if not sep or not name or not url:
            raise SystemExit(
                f"praxis init: --environment expects NAME=URL "
                f"(e.g. dev2=https://dev2.example.com), got {spec!r}."
            )
        if not _ENV_NAME_OK.match(name):
            raise SystemExit(
                f"praxis init: invalid environment name {name!r}: the name "
                f"enters file paths (runs/<ts>__<env>/, {AUTH_DIRNAME}/<env>/, "
                f"{SECRETS_FILE}.<env>) and env-var names "
                f"(PRAXIS_AUTH_STATE_<ENV>_<ROLE>), so only letters, digits, "
                f"and underscores round-trip ([A-Za-z0-9_])."
            )
        prior = seen_upper.get(name.upper())
        if prior == name:
            raise SystemExit(
                f"praxis init: environment {name!r} is declared twice."
            )
        if prior is not None:
            raise SystemExit(
                f"praxis init: environment names {prior!r} and {name!r} "
                f"collide once uppercased into the "
                f"PRAXIS_AUTH_STATE_<ENV>_<ROLE> env-var channel; rename one."
            )
        seen_upper[name.upper()] = name
        envs[name] = {"base_url": url}
    if default_env and default_env not in envs:
        raise SystemExit(
            f"praxis init: --default-env {default_env!r} is not a declared "
            f"--environment. Declared environments: {', '.join(envs)}."
        )
    return envs


def _cmd_init(args: argparse.Namespace) -> int:
    # The two scaffold styles are mutually exclusive (ADR-0035 decision 9):
    # `--environment NAME=URL` (repeatable) + `--default-env` declare the
    # multi-env map; the legacy `--env` / `--base-url` pair writes the
    # single-deployment keys. Mixing them would scaffold a config whose
    # top-level keys are silently shadowed by the map (decision 1), so the
    # combination errors loudly before anything touches disk. (`getattr`,
    # like `mcp_config` below: callers that drive `_cmd_init` with a partial
    # args object stay on the legacy path.)
    env_specs: list[str] | None = getattr(args, "environment", None)
    default_env: str | None = getattr(args, "default_env", None)
    declared_style = bool(env_specs) or bool(default_env)
    if declared_style and (args.env is not None or args.base_url is not None):
        raise SystemExit(
            "praxis init: --environment/--default-env (the multi-environment "
            "scaffold, ADR-0035) cannot be combined with the legacy "
            "single-env --env/--base-url pair. Declare every deployment with "
            "--environment NAME=URL (repeatable) + --default-env, or keep "
            "the single-env flags."
        )
    environments = (
        _parse_init_environments(env_specs or [], default_env)
        if declared_style else None
    )
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
    # Make the project browser-ready with NO manual MCP setup: when the user
    # passes no --mcp-config, scaffold a default Playwright MCP config at the repo
    # root and point the config at it, so `praxis regress` / `praxis explore`
    # drive a browser out of the box after `pip install` (the "don't touch the
    # MCP" onboarding). An explicit --mcp-config wins; an existing file is never
    # overwritten.
    mcp_config_value = getattr(args, "mcp_config", None)
    mcp_scaffolded = False
    if not mcp_config_value:
        mcp_path = root / MCP_CONFIG_NAME
        if not mcp_path.exists():
            mcp_path.write_text(_MCP_CONFIG_TEMPLATE, encoding="utf-8")
            mcp_scaffolded = True
        mcp_config_value = MCP_CONFIG_NAME
    config: dict[str, Any]
    if environments is not None:
        # Declared scaffold (ADR-0035 decision 9): the committed map +
        # default_env, and NO top-level base_url / environment /
        # observed_app_version keys - those are shadowed in declared mode
        # (decision 1) and scaffolding dead keys invites drift.
        config = {
            "app": args.app or root.name,
            "agent_id": args.agent_id,
            "mcp_config": mcp_config_value,
            "environments": environments,
        }
        if default_env:
            config["default_env"] = default_env
    else:
        config = {
            "base_url": args.base_url or "http://127.0.0.1:8000",
            "app": args.app or root.name,
            "environment": args.env,
            "agent_id": args.agent_id,
            "observed_app_version": None,
            # Default Playwright MCP config for the claude -p console brain
            # (ADR-0027): a JSON file path (relative to the project root) so
            # `praxis regress` / `praxis explore` need no --mcp-config flag; the
            # flag overrides it. Scaffolded above so a fresh project is
            # browser-ready.
            "mcp_config": mcp_config_value,
        }
    # The brain-model pin ships COMMENTED OUT below the live keys (ADR-0034):
    # discoverable per project without forcing a choice, and never a hardcoded
    # model-name default. Comments only, so the parsed config has no
    # `brain_model` key until a human uncomments it. An UNdeclared scaffold
    # additionally carries the `environments` map commented out the same way
    # (ADR-0035 decision 9): discoverable, zero behavior change; a declared
    # scaffold has the real map instead.
    config_text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    config_text += _BRAIN_MODEL_CONFIG_COMMENT
    if environments is None:
        config_text += _ENVIRONMENTS_CONFIG_COMMENT
    (pdir / CONFIG_NAME).write_text(config_text, encoding="utf-8")
    praxisignore = pdir / PRAXISIGNORE_NAME
    if not praxisignore.exists():
        praxisignore.write_text(_PRAXISIGNORE_TEMPLATE, encoding="utf-8")

    # Gitignore the per-machine run logs, the secrets file (and its per-env
    # `.praxis.secrets.<env>` overlays), and the auth-session directory (whose
    # directory pattern already covers the per-env `.praxis.auth/<env>/`
    # subdirs). This runs on every init (idempotent), so a re-init never
    # duplicates the lines and every secret channel is gitignored BEFORE any
    # secret or saved session could be written (ADR-0021 decisions 5 and 6;
    # ADR-0026 decisions 2 and 3; ADR-0035 decision 7).
    added = _append_gitignore_lines(
        root,
        [
            GITIGNORE_RUNS_LINE,
            GITIGNORE_SECRETS_LINE,
            GITIGNORE_SECRETS_OVERLAYS_LINE,
            GITIGNORE_AUTH_LINE,
        ],
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
    if mcp_scaffolded:
        print(f"  mcp config: {root / MCP_CONFIG_NAME}  (Playwright MCP, browser-ready; "
              "console regress/explore need no setup)")
    else:
        print(f"  mcp config: {mcp_config_value}  (in use)")
    print()
    print("Credentials go in a gitignored .praxis.secrets at the repo root "
          "(KEY=value), never inside .praxis/ (ADR-0021 decision 6).")
    print(f"Saved auth sessions go in a gitignored {AUTH_DIRNAME}/ at the repo "
          "root (one storageState JSON per role), never inside .praxis/ "
          "(ADR-0026 decisions 2 and 3).")
    if environments is not None:
        # The env workflow, named only when a map was declared: an undeclared
        # init prints exactly what it printed before (ADR-0035 zero-ceremony).
        names = ", ".join(environments)
        print(f"Environments declared: {names}. Pick one per run with "
              f"`praxis regress --env <name>` (or `explore` / `status`), the "
              f"{PRAXIS_ENV_VAR} env var, or the committed default_env "
              "(ADR-0035).")
        print(f"Per-environment credentials overlay the base file as "
              f"{SECRETS_FILE}.<env>; per-environment sessions live in "
              f"{AUTH_DIRNAME}/<env>/<role>.json (both gitignored).")
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


class _GoalAuthContext:
    """Per-goal `auth_state` holder the claude -p brain reads to reuse a session.

    The brain seam is `brain(prompt)` with no goal id (ADR-0019), so the CLI must
    tell the brain WHICH goal it is about to run. This holds a `goal_id ->
    auth_state` map from the seeds, plus a THREAD-LOCAL "current goal" set just
    before each goal runs. The brain reads `current()` at the start of each call
    and resolves that goal's saved session (ADR-0026, ADR-0027 decision 2).

    Thread-local is correct under `--jobs > 1`: `run_partitioned` calls
    `on_goal_start` in the SAME worker thread that then runs the brain, so each
    worker sets and reads its own goal with no cross-goal contention. The
    sequential and single-goal paths set the goal in the one thread that runs it.
    """

    def __init__(self, auth_states: dict[str, Any]) -> None:
        self._auth_states = auth_states
        self._local = threading.local()

    def set_current(self, goal_id: str) -> None:
        self._local.goal_id = goal_id

    def current(self) -> Any:
        """The current goal's `auth_state`, or None when none is set / known."""
        goal_id = getattr(self._local, "goal_id", None)
        if goal_id is None:
            return None
        return self._auth_states.get(goal_id)


def _auth_states_for(proj: "ProjectContext", goals: list[str]) -> dict[str, Any]:
    """Map each goal id to its seed's ADR-0017 `auth_state` (or None).

    Reads the committed seeds, so the brain keys session reuse off the same
    `auth_state` the regress classifier reads (ADR-0026 decision 4: the role a
    session is reused under is the role the verdict expects).
    """
    seeds = proj.seeds()
    return {gid: (seeds[gid].auth_state if gid in seeds else None)
            for gid in goals}


def _resolve_brain_model(
    flag: str | None,
    config_value: str | None,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the model the claude -p console brain pins, and name its source.

    Precedence (ADR-0034, fixed):

      1. the `--model` flag: an explicit per-run override (an A/B run);
      2. the `PRAXIS_BRAIN_MODEL` env var: the CI channel, mirroring the
         env-over-file precedence of the ADR-0021 secrets channel;
      3. the committed `.praxis/config.yaml` `brain_model` key: the per-project
         pin teammates share through git;
      4. unset everywhere -> (None, None): no `--model` is appended and the
         claude CLI's own default runs, exactly today's argv.

    The winning value is passed through VERBATIM: model names rot, so nothing
    here validates against a model-name list; the claude CLI is the authority
    and errors loudly on an unknown model. The model is an OPERATIONAL input to
    the brain, never knowledge: it never enters a knowledge file, a candidate,
    or an assertion (ADR-0034). An empty string at any level counts as unset,
    so an exported-but-blank env var cannot mask the committed pin.

    Returns `(model, source)` where source is a human-readable name of the
    level that won, for the run banner; `(None, None)` when nothing pins.
    """
    if flag:
        return flag, "--model flag"
    env = os.environ if environ is None else environ
    env_value = env.get(BRAIN_MODEL_ENV)
    if env_value:
        return env_value, f"{BRAIN_MODEL_ENV} env"
    if config_value:
        return config_value, "config.yaml brain_model"
    return None, None


def _resolve_env(
    flag: str | None,
    environments: dict[str, dict[str, Any]] | None,
    default_env: str | None,
    *,
    environ: dict[str, str] | None = None,
    unresolved_ok: bool = False,
) -> tuple[str | None, str | None]:
    """Resolve which declared environment this run checks, and name its source.

    Precedence (ADR-0035 decision 2, mirroring the ADR-0034 model pin):

      1. the `--env` flag: an explicit per-run override;
      2. the `PRAXIS_ENV` env var: the CI channel (one job matrix variable);
      3. the committed `.praxis/config.yaml` `default_env` key;
      4. a single-entry `environments` map auto-selects its only entry
         (unambiguous, no ceremony for the one-env project that opted in).

    An empty string at any level counts as unset (the ADR-0034 posture).
    Resolution failures are LOUD, never silent:

      - a name (flag or env var) not in the declared map errors naming the
        declared environments;
      - a `default_env` naming an undeclared environment errors the same way
        (a committed typo must not silently fall through to auto-select);
      - a declared multi-entry map that resolves to nothing errors naming the
        declared environments and the three ways to pick one;
      - `--env` on an UNDECLARED project errors: the user explicitly asked
        for something the config cannot honor;
      - `PRAXIS_ENV` on an undeclared project is ignored with a one-line
        stderr notice (warn-and-ignore), so a pipeline-wide export cannot
        break repos that have not adopted environments.

    `unresolved_ok=True` softens exactly ONE of those cases: a declared
    multi-entry map that resolves to nothing returns `(None, None)` instead
    of raising. That is the read-only `status` posture (a user runs status
    precisely to SEE what environments exist); every explicit-mistake error
    above (unknown name, flag on undeclared, default_env typo) stays loud.

    Returns `(name, source)` where source names the winning level for the
    run banner; `(None, None)` on an undeclared project (today's behavior).
    """
    env = os.environ if environ is None else environ
    flag = flag or None
    env_value = env.get(PRAXIS_ENV_VAR) or None
    default_env = default_env or None
    if not environments:
        if flag:
            raise SystemExit(
                f"--env {flag!r} was given but no environments are declared "
                "in .praxis/config.yaml. Declare an `environments:` map "
                "(ADR-0035) to select one."
            )
        if env_value:
            print(
                f"warning: {PRAXIS_ENV_VAR}={env_value!r} is set but no "
                "environments are declared in .praxis/config.yaml; ignoring "
                "it (ADR-0035).",
                file=sys.stderr,
            )
        return None, None
    declared = ", ".join(sorted(environments))
    if flag:
        if flag not in environments:
            raise SystemExit(
                f"unknown environment {flag!r} (from --env). Declared "
                f"environments: {declared}."
            )
        return flag, "--env flag"
    if env_value:
        if env_value not in environments:
            raise SystemExit(
                f"unknown environment {env_value!r} (from {PRAXIS_ENV_VAR}). "
                f"Declared environments: {declared}."
            )
        return env_value, f"{PRAXIS_ENV_VAR} env"
    if default_env:
        if default_env not in environments:
            raise SystemExit(
                f"config.yaml default_env {default_env!r} is not a declared "
                f"environment. Declared environments: {declared}."
            )
        return default_env, "config.yaml default_env"
    if len(environments) == 1:
        only = next(iter(environments))
        return only, "single declared environment"
    if unresolved_ok:
        return None, None
    raise SystemExit(
        f"no environment selected: this project declares environments "
        f"({declared}) and none was picked. Select one with --env <name>, "
        f"the {PRAXIS_ENV_VAR} env var, or default_env in "
        ".praxis/config.yaml (ADR-0035)."
    )


def _select_environment_for_run(
    proj: "ProjectContext", args: argparse.Namespace, *,
    unresolved_ok: bool = False,
) -> str | None:
    """Resolve the run's environment and print the one-line stderr banner.

    Mirrors the ADR-0034 model banner posture: printed only when an
    environment is resolved, naming the winning source, so a run's output
    records WHICH deployment produced the verdicts. An undeclared project
    prints nothing and the run proceeds byte-identically to pre-ADR-0035.
    `unresolved_ok` (status only) makes an unresolvable declared map proceed
    unselected, with no banner, instead of erroring (see `_resolve_env`).
    """
    name, source = proj.select_environment(
        getattr(args, "env", None), unresolved_ok=unresolved_ok,
    )
    if name is not None:
        print(f"  environment: {name} (from {source})", file=sys.stderr)
    return name


def _select_console_brain(
    args: argparse.Namespace, *, default_mcp_config: str | None = None,
    default_brain_model: str | None = None,
    progress: "Callable[[], tuple[str, str] | None] | None" = None,
    session_for_goal: "Callable[[], Any] | None" = None,
    environment: str | None = None,
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

    `session_for_goal`, when given, is forwarded to the claude -p brain so a
    precondition authenticated goal reuses its saved Playwright storage state via
    `--storage-state` (ADR-0026, ADR-0027 decision 2). The `--from-file` path
    ignores it: a scripted run feeds observations directly and drives no browser.

    `environment` is the run's SELECTED environment (ADR-0035 decision 7, the
    value `_select_environment_for_run` resolved; None on an undeclared
    project). It is forwarded to the claude -p brain so session reuse resolves
    the env-scoped sources (`PRAXIS_AUTH_STATE_<ENV>_<ROLE>`,
    `.praxis.auth/<env>/<role>.json`, no unscoped fallback) and the AUTH-EXPIRED
    note names the role AND the environment. The `--from-file` path ignores it:
    a scripted run loads no session.

    The brain model resolves `--model` flag > `PRAXIS_BRAIN_MODEL` env >
    `default_brain_model` (the project's committed `brain_model` pin) > unset
    (the claude CLI default, no `--model` appended), per ADR-0034. The
    `--from-file` path ignores it too: a scripted run invokes no model.
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
    # Browser MCP preflight: the claude -p brain drives a real browser through a
    # Playwright MCP, so a missing MCP config gets a loud, actionable WARNING here
    # naming the exact fix, instead of a cryptic mid-run failure. A fresh
    # `praxis init` scaffolds playwright-mcp.json, so this only surfaces for a
    # project inited before that or one whose file was removed. It is a warning,
    # not a hard exit: the path may resolve at runtime, and the run still fails
    # loudly downstream if the browser truly never starts.
    mcp = getattr(args, "mcp_config", None) or default_mcp_config
    if not mcp:
        print(
            "warning: no Playwright MCP configured; the console brain needs a "
            f"browser MCP to drive the app. Re-run `praxis init` to scaffold "
            f"{MCP_CONFIG_NAME}, or pass --mcp-config PATH.",
            file=sys.stderr,
        )
    else:
        mcp_path = Path(mcp)
        if not mcp_path.is_absolute():
            mcp_path = Path.cwd() / mcp
        if not mcp_path.exists():
            print(
                f"warning: Playwright MCP config {mcp!r} not found at {mcp_path}. "
                f"Re-run `praxis init` to scaffold {MCP_CONFIG_NAME}, or pass "
                "--mcp-config PATH to point at your browser MCP.",
                file=sys.stderr,
            )
    # Brain model pin (ADR-0034): flag > env > committed config > unset (the
    # claude CLI default). When a pin wins, name it and its source on stderr so
    # a run's output records which brain capability produced the verdicts (the
    # model name is operational, not a secret); when nothing pins, stay silent
    # and append no --model, exactly the pre-ADR-0034 argv.
    model, model_source = _resolve_brain_model(
        getattr(args, "model", None), default_brain_model,
    )
    if model is not None:
        print(f"  brain model: {model} (from {model_source})", file=sys.stderr)
    return make_claude_brain(
        headed=getattr(args, "headed", False),
        timeout_seconds=args.budget_wall_seconds,
        model=model,
        mcp_config_path=getattr(args, "mcp_config", None) or default_mcp_config,
        progress=progress,
        session_for_goal=session_for_goal,
        environment=environment,
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
    # Resolve the environment BEFORE building the adapter (ADR-0035): the
    # selected env supplies base_url / environment / observed_app_version to
    # everything downstream. Undeclared projects resolve to None silently.
    # The SELECTED name (not the legacy single-env `environment` config label)
    # is what scopes session reuse below (ADR-0035 decision 7).
    selected_env = _select_environment_for_run(proj, args)
    # The "App under test:" prompt line carries the base_url ONLY when an
    # environment was selected from a declared map (ADR-0035 decision 3): an
    # undeclared project's scaffolded, possibly-dead top-level base_url is
    # NEVER injected, so its rendered prompts stay byte-identical to today.
    run_base_url = proj.base_url if selected_env is not None else None
    # JUnit suite name tagged by environment (ADR-0035 decision 8): a declared
    # run's suite is `praxis-regress[<env>]` so a CI job matrix over PRAXIS_ENV
    # renders one distinguishable suite per deployment; undeclared keeps
    # today's `praxis-regress` byte-identically. Testcase mapping, counts, and
    # exit codes are untouched.
    junit_suite = (
        f"praxis-regress[{selected_env}]" if selected_env is not None
        else "praxis-regress"
    )
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

    # Per-goal auth context: the claude -p brain reuses a saved session per goal
    # (ADR-0026, ADR-0027 decision 2). Cover every seed so both the aggregate and
    # the single-goal paths can set the current goal before each run.
    all_goals = sorted(proj.seeds().keys())
    auth_ctx = _GoalAuthContext(_auth_states_for(proj, all_goals))

    brain = _select_console_brain(
        args, default_mcp_config=proj.mcp_config,
        default_brain_model=proj.brain_model, progress=_progress_label,
        session_for_goal=auth_ctx.current,
        environment=selected_env,
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
            # Always set the per-goal auth context so the brain reuses that
            # goal's saved session (in the worker thread under --jobs > 1, where
            # this fires; thread-local makes it worker-correct). The rich in-place
            # progress label only makes sense sequentially, so it stays off when
            # goals run concurrently (set below) to avoid clobbering one line.
            auth_ctx.set_current(gid)
            if not concurrent:
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
            base_url=run_base_url,
            budget_tokens_per_goal=args.budget_tokens,
            budget_actions_per_goal=args.budget_actions,
            budget_wall_seconds_per_goal=args.budget_wall_seconds,
            jobs=args.jobs,
            # `_on_goal_start` ALWAYS fires so the brain gets the per-goal auth
            # context (it sets the rich in-place running line only when
            # sequential; under `--jobs > 1` it sets the auth context but leaves
            # the label off, since concurrent goals would clobber one line).
            on_goal_start=_on_goal_start,
            on_goal_done=_on_goal_done,
        )
        run_dir = proj.run_dir()
        report_md = run_dir / "regress-aggregate.md"
        report_xml = run_dir / "regress-aggregate.xml"
        # Reports are tagged by the selected environment (ADR-0035 decision 8):
        # the markdown header names the deployment, the JUnit suite carries it
        # in brackets. Undeclared runs (environment None) stay byte-identical.
        write_aggregate_markdown(reports, report_md, environment=selected_env)
        # The aggregate path is the CI path (ADR-0024: `praxis regress` with no
        # --goal). Emit JUnit XML here too, one testcase per goal, so a CI that
        # renders test reports gets the aggregate run, not only single-goal runs.
        write_aggregate_junit_xml(reports, report_xml, suite_name=junit_suite)
        # Per-goal verdict lines were printed live by `_on_goal_done` as each
        # goal completed (ADR-0027 decision 6); the named signal for a non-OK
        # goal is in the final summary below and the markdown report.
        # Pytest-style final summary: the loud PASSED / FAILED banner, the
        # `N passed, N failed, N stale` tally, and every goal that needs action
        # named with its evidence (ADR-0027 decision 6). Verdicts and exit code
        # are unchanged; this is presentation only.
        print(format_console_summary(reports, color=use_color))
        print(f"report: {report_md}")
        print(f"junit:  {report_xml}")
        # A REGRESSED or ERROR goal fails the run loudly; STALE alone does not
        # (the app changed on purpose, the fix is a human re-seed).
        failed = aggregate_run_failed(reports)
        return 1 if failed else 0

    # Single-goal pass/fail path (unchanged verdict contract).
    goals = [args.goal]
    print(f"running 1 goal: {args.goal}...")
    # One goal: set the running label directly (no aggregate loop / start hook),
    # so the spinner reads `[1/1] Running <goal>` like the aggregate path. Set the
    # auth context too so this goal reuses its saved session if it is a
    # precondition authenticated goal (ADR-0026, ADR-0027 decision 2).
    _prog["total"] = 1
    _prog["idx"] = 1
    _prog["label"] = args.goal
    auth_ctx.set_current(args.goal)
    # The console surface drives the SAME engine a skill driver calls
    # (ADR-0019 decision 4); the only difference is which brain the seam
    # carries (here: the file/claude-p executor; in a skill: the local Claude
    # session). The verdict comes back identical for the same store + brain
    # output.
    results = regress_engine(
        adapter, brain, goals,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        base_url=run_base_url,
        budget_tokens=args.budget_tokens,
        budget_actions=args.budget_actions,
        stop_on_fail=args.stop_on_fail,
    )

    run_dir = proj.run_dir()
    last_md = run_dir / "last-regress.md"
    last_xml = run_dir / "last-regress.xml"
    # Same env tagging as the aggregate artifacts (ADR-0035 decision 8);
    # byte-identical when no environment is selected.
    write_markdown_report(results, last_md, environment=selected_env)
    write_junit_xml(results, last_xml, suite_name=junit_suite)
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
    # AUTH-EXPIRED guard on the single-goal path (ADR-0026 decision 5): the
    # single-goal verdict is the bare RegressionVerdict, which has no AUTH-EXPIRED
    # routing, so a goal that expected an authenticated scope but ran logged out
    # (the saved session missing or expired) would otherwise read UNCERTAIN and
    # NOT fail the run, a silent false green. Re-classify through the same
    # `classify_goal` the aggregate path uses: when it is AUTH-EXPIRED, surface it
    # LOUDLY naming the expired role and fail the run, never a silent green and
    # never a false REGRESSED.
    if kf is not None and results:
        report = classify_goal(
            kf, results[0], current_version=proj.observed_app_version,
        )
        if report.verdict == AggregateVerdict.AUTH_EXPIRED:
            role = report.signals[0] if report.signals else "?"
            print(f"AUTH-EXPIRED: {args.goal} could not authenticate as role "
                  f"{role!r}; refresh the saved session. This is not a "
                  f"regression and not stale knowledge.", file=sys.stderr)
            return 1
    return 1 if regress_failed(results) else 0


def _explore_aggregate(
    proj: "ProjectContext",
    brain: Any,
    *,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
    budget_wall_seconds: float | None = None,
    jobs: int = 1,
    auth_ctx: "_GoalAuthContext | None" = None,
    base_url: str | None = None,
    environment: str | None = None,
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
        base_url=base_url,
        budget_tokens_per_goal=budget_tokens,
        budget_actions_per_goal=budget_actions,
        budget_wall_seconds_per_goal=budget_wall_seconds,
        committed_sink=_commit_new_candidates,
        jobs=jobs,
        # Set the per-goal auth context before each goal runs so the claude -p
        # brain reuses that goal's saved session (ADR-0026, ADR-0027 dec. 2).
        on_goal_start=auth_ctx.set_current if auth_ctx is not None else None,
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
    # The explore report header is tagged by the selected environment exactly
    # as the regress reports are (ADR-0035 decision 8); byte-identical when no
    # environment is selected.
    write_candidate_markdown(
        groups, report_md, off_path_fractions=off_path, errors=errors,
        environment=environment,
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
    # Resolve the environment BEFORE building the adapter (ADR-0035), exactly
    # as regress does; undeclared projects resolve to None silently. The
    # SELECTED name scopes session reuse below (ADR-0035 decision 7).
    selected_env = _select_environment_for_run(proj, args)
    adapter = proj.adapter()

    # The "App under test:" prompt line carries the base_url ONLY when an
    # environment was selected from a declared map (ADR-0035 decision 3); an
    # undeclared project's prompts stay byte-identical to today.
    run_base_url = proj.base_url if selected_env is not None else None

    # Per-goal auth context so the claude -p brain reuses a saved session per
    # goal (ADR-0026, ADR-0027 decision 2). Cover every seed so both the
    # aggregate and the single-goal paths can set the current goal.
    all_goals = sorted(proj.seeds().keys())
    auth_ctx = _GoalAuthContext(_auth_states_for(proj, all_goals))

    brain = _select_console_brain(
        args, default_mcp_config=proj.mcp_config,
        default_brain_model=proj.brain_model,
        session_for_goal=auth_ctx.current,
        environment=selected_env,
    )

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
            auth_ctx=auth_ctx,
            base_url=run_base_url,
            environment=selected_env,
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
    # One goal: set the auth context so this goal reuses its saved session if it
    # is a precondition authenticated goal (ADR-0026, ADR-0027 decision 2).
    auth_ctx.set_current(args.goal)
    # The console surface drives the SAME engine a skill driver calls; the seam
    # carries the paste/file executor here, the local Claude session in a skill.
    outcome = explore_engine(
        adapter, brain, args.goal,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        base_url=run_base_url,
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
            # ADR-0035 decision 6: review is cross-env (the adapter here is
            # built without selecting an environment); each candidate carries
            # an annotation naming the env(s) it was observed on - exactly the
            # datum a human needs to decide product-level vs not-yet-shipped.
            # Display-only: it never changes status or the source count
            # (decision 5). None when every observation is env-less (the pure
            # single-env project), keeping that output byte-identical.
            env_note = format_environment_annotation(pc.environments)
            env_line = f"\n     {env_note}" if env_note is not None else ""
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
                    f"{env_line}"
                )
            elif pc.uncertainty is not None:
                src_list = ", ".join(sorted(pc.distinct_source_ids))
                print(
                    f"  [contested candidate_uncertainty] {pc.candidate_id}: "
                    f"{pc.uncertainty.question}\n"
                    f"     raised_by={pc.uncertainty.raised_by}  "
                    f"sources={{{src_list}}}  "
                    f"events={len(pc.corroborating_events)}"
                    f"{env_line}"
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
    # Resolve the environment first (ADR-0035): status reports the selected
    # env's base_url / version, and a declared project lists its whole map
    # below. Undeclared projects resolve to None and print exactly as today.
    # Resolution is BEST-EFFORT here, unlike regress/explore: status is the
    # read-only discovery command a user runs precisely to SEE what
    # environments exist, so an unresolvable multi-entry map prints the map
    # unselected (no banner) instead of hard-erroring. Explicit mistakes
    # (unknown --env name, --env on an undeclared project) stay loud.
    _select_environment_for_run(proj, args, unresolved_ok=True)
    adapter = proj.adapter()
    seeds = proj.seeds()
    if not seeds:
        print(f"no seeds in {proj.knowledge_dir}", file=sys.stderr)
        return 2
    print(f"project: {proj.root}")
    print(f"app:     {proj.app}  env={proj.environment or '-'}  base_url={proj.base_url}")
    print(f"version: {proj.observed_app_version or '-'}")
    envs = proj.environments
    if envs:
        # A declared project shows its whole committed map plus the default
        # (ADR-0035 decision 2), so a teammate can see every deployment and
        # how the bare command picks one without opening config.yaml.
        print("environments:")
        for name in sorted(envs):
            entry = envs[name]
            ver = entry.get("observed_app_version")
            ver_str = f"  version={ver}" if ver else ""
            marker = "  (default)" if name == proj.default_env else ""
            print(f"  {name}: {entry['base_url']}{ver_str}{marker}")
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
    init.add_argument("--base-url", default=None,
                       help="Single-deployment base URL written to config.yaml "
                            "(default: http://127.0.0.1:8000). Mutually "
                            "exclusive with --environment/--default-env.")
    init.add_argument("--app", help="App name (default: cwd directory name).")
    init.add_argument("--env", default=None,
                       help="Environment label written to the top-level "
                            "config.yaml `environment` key (legacy single-env "
                            "scaffold). Mutually exclusive with "
                            "--environment/--default-env.")
    init.add_argument("--environment", action="append", default=None,
                       metavar="NAME=URL",
                       help="Declare a deployment in the committed config.yaml "
                            "`environments` map (repeatable, ADR-0035). "
                            "Mutually exclusive with the legacy single-env "
                            "--env/--base-url pair.")
    init.add_argument("--default-env", default=None,
                       help="The committed default_env teammates share; must "
                            "name a declared --environment (ADR-0035). "
                            "Optional with a single --environment, which "
                            "auto-selects.")
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
    regress.add_argument("--env", default=None,
                          help="Environment to run against: a name from the "
                               "config.yaml `environments` map; overrides "
                               "PRAXIS_ENV and default_env (ADR-0035).")
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
    regress.add_argument("--model", default=None,
                          help="Model the claude -p console brain runs with for "
                               "THIS run (passed to `claude -p --model` "
                               "verbatim); overrides PRAXIS_BRAIN_MODEL and the "
                               "config.yaml brain_model pin (ADR-0034). Default: "
                               "env, then the committed pin, then the claude "
                               "CLI default.")
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
    explore.add_argument("--env", default=None,
                          help="Environment to run against: a name from the "
                               "config.yaml `environments` map; overrides "
                               "PRAXIS_ENV and default_env (ADR-0035).")
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
    explore.add_argument("--model", default=None,
                          help="Model the claude -p console brain runs with for "
                               "THIS run (passed to `claude -p --model` "
                               "verbatim); overrides PRAXIS_BRAIN_MODEL and the "
                               "config.yaml brain_model pin (ADR-0034). Default: "
                               "env, then the committed pin, then the claude "
                               "CLI default.")
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
    status.add_argument("--env", default=None,
                         help="Environment to report against: a name from the "
                              "config.yaml `environments` map; overrides "
                              "PRAXIS_ENV and default_env (ADR-0035).")
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
