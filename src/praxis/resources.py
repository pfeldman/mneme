"""Runtime accessors for data the package ships (ADR-0020 decisions 6 and 7).

Two artifacts ride the wheel as package data and must be resolvable at runtime
both from an installed wheel and from the `src/` tree (an editable install or a
plain checkout):

    - the JSON knowledge schema (`schema/knowledge.schema.json`), force-included
      into the wheel under `praxis/_resources/` so an installed Praxis validates
      against the SAME schema the repo tests against (no network fetch, no
      separate download);
    - the Claude Code skills tree (`src/praxis/skills/`), which `praxis init`
      scaffolds into a consuming project's `.claude/skills/`.

The schema lives once at the repo root. From an installed wheel it resolves via
`importlib.resources` (the force-included copy); from the src tree that copy does
not exist, so we fall back to the repo-root file. Both paths return the SAME
bytes, which the packaging test asserts.

This module imports only the standard library, so importing it never pulls a
runtime or a brain (ADR-0003, ADR-0019).
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

__all__ = [
    "SCHEMA_RESOURCE_NAME",
    "SKILLS_PACKAGE",
    "schema_text",
    "schema_bytes",
    "schema_path",
    "skills_root",
    "iter_skill_files",
]

# Name of the schema file as it ships inside the wheel, under the
# `praxis._resources` package (see the force-include rule in pyproject.toml).
SCHEMA_RESOURCE_NAME = "knowledge.schema.json"
_SCHEMA_SUBPACKAGE = "praxis._resources"

# Import path of the packaged skills tree.
SKILLS_PACKAGE = "praxis.skills"

# Repo-root fallback used only when running from the src tree, where the
# force-included wheel copy of the schema does not exist. `parents[2]` walks
# src/praxis/resources.py -> src/praxis -> src -> repo root.
_REPO_ROOT_SCHEMA = Path(__file__).resolve().parents[2] / "schema" / SCHEMA_RESOURCE_NAME


def _packaged_schema() -> Traversable | None:
    """Return the wheel-bundled schema resource, or None when running from src.

    `importlib.resources.files` succeeds whenever the `praxis._resources`
    package is importable; the resource is only present in a built wheel
    (force-included), so an editable install or a checkout returns None and the
    callers fall back to the repo-root file.
    """
    try:
        candidate = resources.files(_SCHEMA_SUBPACKAGE) / SCHEMA_RESOURCE_NAME
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def schema_bytes() -> bytes:
    """Return the packaged JSON schema as raw bytes.

    Resolves the wheel-bundled copy when installed, else the repo-root file.
    """
    packaged = _packaged_schema()
    if packaged is not None:
        return packaged.read_bytes()
    return _REPO_ROOT_SCHEMA.read_bytes()


def schema_text() -> str:
    """Return the packaged JSON schema decoded as UTF-8 text."""
    return schema_bytes().decode("utf-8")


def schema_path() -> Path:
    """Return a filesystem Path to the packaged JSON schema.

    For a force-included resource in an unzipped wheel and for the src-tree
    fallback this is a real file on disk. Callers that only need the bytes
    should prefer `schema_bytes` / `schema_text`, which never touch the
    filesystem path and work even from a zip-imported package.
    """
    packaged = _packaged_schema()
    if packaged is not None:
        # `Traversable` is path-like for an unpacked wheel; resolve concretely.
        return Path(str(packaged))
    return _REPO_ROOT_SCHEMA


def skills_root() -> Traversable:
    """Return the packaged skills tree root as a Traversable.

    Resolves under the installed `praxis.skills` package (wheel) and under
    `src/praxis/skills/` (src tree) identically, because both expose the same
    import path.
    """
    return resources.files(SKILLS_PACKAGE)


def iter_skill_files() -> list[Path]:
    """Return every shipped skill file (`SKILL.md` and any sibling assets).

    Walks the packaged skills tree and returns the non-`__pycache__`,
    non-`.py` files as concrete Paths so `praxis init` can copy them into a
    project's `.claude/skills/`. The list is empty only if no skill ships,
    which the packaging test forbids.
    """
    root = skills_root()
    found: list[Path] = []
    # `Traversable.rglob` is not part of the ABC; resolve to a real directory
    # path and walk it. For both the wheel (unpacked) and the src tree this is
    # a real directory on disk.
    root_path = Path(str(root))
    for entry in sorted(root_path.rglob("*")):
        if not entry.is_file():
            continue
        if entry.suffix == ".py":
            continue
        if "__pycache__" in entry.parts:
            continue
        found.append(entry)
    return found
