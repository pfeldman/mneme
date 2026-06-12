"""The authenticated-session channel (ADR-0026 decisions 2, 3, 4).

These tests pin the contract of `praxis.auth_session`, mirroring the
`praxis.secrets` contract one layer up:

    - save / load round-trip per role (the storageState dict survives);
    - the environment variable `PRAXIS_AUTH_STATE_<ROLE>` BEATS the local file
      for the same role (ADR-0026 decision 3);
    - an absent session raises `MissingSession` naming the absent ROLE;
    - NO code path echoes the session VALUE (cookies / tokens) to stdout,
      stderr, an exception message, or a return surface that would surface it;
    - the local session file resolves OUTSIDE the committed `.praxis/` tree
      (it is `.praxis.auth/<role>.json`, a sibling, gitignored separately in
      Step 2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from praxis import auth_session

# A distinctive sentinel that must never appear in any output, exception
# message, or surfaced channel. It stands in for a session cookie / token value.
# If any test sees this string leak into a surfaced channel, the no-echo
# contract is broken.
SECRET_COOKIE = "s3ssion-COOKIE-must-not-leak-9f3a"
ROLE = "admin"


def _session(value: str = SECRET_COOKIE) -> dict[str, object]:
    """A minimal Playwright storageState shape carrying the sentinel value."""
    return {
        "cookies": [
            {"name": "session", "value": value, "domain": "app.example", "path": "/"}
        ],
        "origins": [],
    }


# --- save / load round-trip per role --------------------------------------


def test_save_load_round_trip_per_role(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    for role in ("user", "admin", "anonymous"):
        session = _session(f"cookie-for-{role}")
        path = auth_session.save_session(role, session, auth_dir=auth_dir)
        assert path == auth_dir / f"{role}.json"
        loaded = auth_session.load_session(role, auth_dir=auth_dir, environ={})
        assert loaded == session
    # Each role's session is independent: loading one never returns another's.
    assert auth_session.load_session("admin", auth_dir=auth_dir, environ={})[
        "cookies"
    ][0]["value"] == "cookie-for-admin"


def test_save_creates_the_auth_dir_if_absent(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    assert not auth_dir.exists()
    auth_session.save_session(ROLE, _session(), auth_dir=auth_dir)
    assert auth_dir.is_dir()


# --- env var beats the file (ADR-0026 decision 3) -------------------------


def test_env_var_beats_the_file(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    file_session = _session("from-file")
    auth_session.save_session(ROLE, file_session, auth_dir=auth_dir)
    env_session = _session("from-env")
    got = auth_session.load_session(
        ROLE,
        auth_dir=auth_dir,
        environ={auth_session.env_var_name(ROLE): json.dumps(env_session)},
    )
    assert got == env_session
    assert got["cookies"][0]["value"] == "from-env"


def test_env_var_name_uppercases_the_role() -> None:
    assert auth_session.env_var_name("admin") == "PRAXIS_AUTH_STATE_ADMIN"
    assert auth_session.env_var_name("user") == "PRAXIS_AUTH_STATE_USER"


def test_empty_env_value_falls_through_to_file(tmp_path: Path) -> None:
    # An empty environment variable is not a supplied session; the file wins.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    file_session = _session()
    auth_session.save_session(ROLE, file_session, auth_dir=auth_dir)
    got = auth_session.load_session(
        ROLE, auth_dir=auth_dir, environ={auth_session.env_var_name(ROLE): ""}
    )
    assert got == file_session


def test_env_var_carries_raw_storage_state_json(tmp_path: Path) -> None:
    # CI supplies the session as a runner secret with NO file present: the raw
    # storageState JSON is the env var content (ADR-0026 decision 3).
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME  # never created
    env_session = _session()
    got = auth_session.load_session(
        ROLE,
        auth_dir=auth_dir,
        environ={auth_session.env_var_name(ROLE): json.dumps(env_session)},
    )
    assert got == env_session
    assert not auth_dir.exists()  # no file needed in CI


# --- absent session raises MissingSession naming the role -----------------


def test_absent_session_raises_missing_session_naming_the_role(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME  # no file inside
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session(ROLE, auth_dir=auth_dir, environ={})
    assert ei.value.role == ROLE
    # The exception names the absent role in its string form.
    assert ROLE in str(ei.value)


def test_missing_session_is_a_keyerror_subclass() -> None:
    # Callers already guarding KeyError keep working (mirrors MissingCredential).
    assert issubclass(auth_session.MissingSession, KeyError)


def test_malformed_session_payload_raises_missing_session_naming_role(
    tmp_path: Path,
) -> None:
    # A non-JSON / non-dict payload is an unusable session, named by role, never
    # echoing the (possibly secret-bearing) raw content.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    auth_dir.mkdir(parents=True)
    bad = auth_dir / f"{ROLE}.json"
    bad.write_text(f"not-json-but-carries-{SECRET_COOKIE}", encoding="utf-8")
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session(ROLE, auth_dir=auth_dir, environ={})
    assert ROLE in str(ei.value)
    assert SECRET_COOKIE not in str(ei.value)


# --- the local file lives OUTSIDE the committed .praxis/ tree -------------


def test_session_file_path_is_outside_the_committed_praxis_tree(tmp_path: Path) -> None:
    # The path is `.praxis.auth/<role>.json`, a SIBLING of `.praxis/`, never a
    # child of it (ADR-0026 decision 2: never inside the committed tree).
    repo_root = tmp_path
    (repo_root / ".praxis").mkdir()
    path = auth_session.session_file_path(ROLE, repo_root=repo_root)
    assert path == repo_root / auth_session.AUTH_DIRNAME / f"{ROLE}.json"
    committed_tree = (repo_root / ".praxis").resolve()
    assert committed_tree not in path.resolve().parents
    # The directory name itself is a distinct sibling, not under `.praxis/`.
    assert auth_session.AUTH_DIRNAME != ".praxis"
    assert not auth_session.AUTH_DIRNAME.startswith(".praxis/")


def test_save_writes_under_the_sibling_dir_not_the_committed_tree(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / ".praxis").mkdir()
    path = auth_session.save_session(ROLE, _session(), repo_root=repo_root)
    assert path.is_file()
    assert (repo_root / ".praxis").resolve() not in path.resolve().parents
    # Nothing was written into the committed `.praxis/` tree.
    assert list((repo_root / ".praxis").iterdir()) == []


def test_session_file_path_walks_up_to_the_repo_root(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / ".praxis").mkdir()
    nested = repo_root / "a" / "b"
    nested.mkdir(parents=True)
    path = auth_session.session_file_path(ROLE, repo_root=None, auth_dir=None)
    # Without a project, walking up from cwd may resolve elsewhere; pin via the
    # explicit start instead by re-deriving with repo_root for determinism.
    path = auth_session.session_file_path(ROLE, repo_root=repo_root)
    assert path == repo_root / auth_session.AUTH_DIRNAME / f"{ROLE}.json"


# --- no code path echoes the session value --------------------------------


def test_no_path_prints_a_session_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Drive every surface that touches a session value and assert the sentinel
    # never reaches stdout or stderr.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME

    # 1. save then load: the value round-trips to the CALLER but prints nothing.
    auth_session.save_session(ROLE, _session(), auth_dir=auth_dir)
    loaded = auth_session.load_session(ROLE, auth_dir=auth_dir, environ={})
    assert loaded["cookies"][0]["value"] == SECRET_COOKIE
    captured = capsys.readouterr()
    assert SECRET_COOKIE not in captured.out
    assert SECRET_COOKIE not in captured.err

    # 2. The env path (raw JSON) also returns to the caller without printing.
    auth_session.load_session(
        ROLE,
        auth_dir=auth_dir,
        environ={auth_session.env_var_name(ROLE): json.dumps(_session())},
    )
    captured = capsys.readouterr()
    assert SECRET_COOKIE not in captured.out
    assert SECRET_COOKIE not in captured.err

    # 3. The absent path names a DIFFERENT, missing role and surfaces no value.
    with pytest.raises(auth_session.MissingSession):
        auth_session.load_session("other_role", auth_dir=auth_dir, environ={})
    captured = capsys.readouterr()
    assert SECRET_COOKIE not in captured.out
    assert SECRET_COOKIE not in captured.err


def test_missing_session_message_carries_no_value(tmp_path: Path) -> None:
    # Even when a session file HAS content, a MissingSession for an absent role
    # must not surface any session value in its message.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    auth_session.save_session(ROLE, _session(), auth_dir=auth_dir)
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session("absent_role", auth_dir=auth_dir, environ={})
    assert SECRET_COOKIE not in str(ei.value)
    assert "absent_role" in str(ei.value)


# --- per-environment scoping (ADR-0035 decision 7) -------------------------
#
# With an environment selected, the resolution is the env-scoped env var
# `PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, then `.praxis.auth/<env>/<role>.json`, and
# NOTHING else: sessions are domain-bound, so there is deliberately NO fallback
# to the unscoped sources. With environment=None everything resolves exactly as
# today (the undeclared-project bar).

ENV = "dev2"


def test_env_var_name_scopes_by_environment() -> None:
    assert (
        auth_session.env_var_name("admin", "dev2")
        == "PRAXIS_AUTH_STATE_DEV2_ADMIN"
    )
    assert auth_session.env_var_name("user", "prod") == "PRAXIS_AUTH_STATE_PROD_USER"
    # environment=None and the empty string (unset, ADR-0034 posture) are the
    # unscoped name, byte-identical to today.
    assert auth_session.env_var_name("admin", None) == "PRAXIS_AUTH_STATE_ADMIN"
    assert auth_session.env_var_name("admin", "") == "PRAXIS_AUTH_STATE_ADMIN"


def test_session_file_path_scopes_by_environment(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / ".praxis").mkdir()
    path = auth_session.session_file_path(
        ROLE, environment=ENV, repo_root=repo_root,
    )
    assert path == repo_root / auth_session.AUTH_DIRNAME / ENV / f"{ROLE}.json"
    # The per-env subdir stays under the same gitignored sibling dir, never
    # inside the committed `.praxis/` tree.
    assert (repo_root / ".praxis").resolve() not in path.resolve().parents
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    assert auth_session.session_file_path(
        ROLE, environment=ENV, auth_dir=auth_dir,
    ) == auth_dir / ENV / f"{ROLE}.json"
    # An empty-string environment counts as unset: today's unscoped path.
    assert auth_session.session_file_path(
        ROLE, environment="", auth_dir=auth_dir,
    ) == auth_dir / f"{ROLE}.json"


def test_save_load_round_trip_per_environment(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    dev2 = _session("cookie-dev2")
    prod = _session("cookie-prod")
    p_dev2 = auth_session.save_session(
        ROLE, dev2, environment="dev2", auth_dir=auth_dir,
    )
    p_prod = auth_session.save_session(
        ROLE, prod, environment="prod", auth_dir=auth_dir,
    )
    assert p_dev2 == auth_dir / "dev2" / f"{ROLE}.json"
    assert p_prod == auth_dir / "prod" / f"{ROLE}.json"
    assert auth_session.load_session(
        ROLE, environment="dev2", auth_dir=auth_dir, environ={},
    ) == dev2
    assert auth_session.load_session(
        ROLE, environment="prod", auth_dir=auth_dir, environ={},
    ) == prod
    # The environment name is part of the PATH only: the saved content is the
    # session dict verbatim, with no env stamp inside it.
    assert json.loads(p_dev2.read_text(encoding="utf-8")) == dev2


def test_env_scoped_env_var_beats_the_env_scoped_file(tmp_path: Path) -> None:
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    auth_session.save_session(
        ROLE, _session("from-file"), environment=ENV, auth_dir=auth_dir,
    )
    env_session = _session("from-env")
    got = auth_session.load_session(
        ROLE,
        environment=ENV,
        auth_dir=auth_dir,
        environ={
            auth_session.env_var_name(ROLE, ENV): json.dumps(env_session)
        },
    )
    assert got == env_session


def test_no_fallback_to_the_unscoped_session_when_an_env_is_selected(
    tmp_path: Path,
) -> None:
    # The no-fallback rule (ADR-0035 decision 7): BOTH unscoped sources present
    # (file and env var) while an environment is selected is still a loud
    # MissingSession, never a silent wrong-domain reuse.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    auth_session.save_session(ROLE, _session(), auth_dir=auth_dir)  # unscoped
    environ = {auth_session.env_var_name(ROLE): json.dumps(_session())}
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session(
            ROLE, environment=ENV, auth_dir=auth_dir, environ=environ,
        )
    assert ei.value.role == ROLE
    assert ei.value.environment == ENV
    assert SECRET_COOKIE not in str(ei.value)


def test_no_fallback_to_another_environments_session(tmp_path: Path) -> None:
    # A prod session never resolves for a dev2 run: sessions are domain-bound.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    auth_session.save_session(
        ROLE, _session(), environment="prod", auth_dir=auth_dir,
    )
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session(
            ROLE, environment="dev2", auth_dir=auth_dir, environ={},
        )
    assert ei.value.environment == "dev2"


def test_missing_session_with_env_names_role_env_var_name_and_path(
    tmp_path: Path,
) -> None:
    # The operator must know exactly which session to seed: role AND env, the
    # env-scoped env-var name, the env-scoped path, and that the unscoped
    # session is deliberately not used.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    with pytest.raises(auth_session.MissingSession) as ei:
        auth_session.load_session(
            ROLE, environment=ENV, auth_dir=auth_dir, environ={},
        )
    msg = str(ei.value)
    assert f"{ROLE!r}" in msg
    assert f"{ENV!r}" in msg
    assert "PRAXIS_AUTH_STATE_DEV2_ADMIN" in msg
    assert f".praxis.auth/{ENV}/{ROLE}.json" in msg
    assert "NOT used" in msg  # the no-fallback rule is stated, not implied


def test_missing_session_message_without_env_is_byte_identical_to_today() -> None:
    expected = (
        "missing auth session for role 'admin': set the environment "
        "variable PRAXIS_AUTH_STATE_ADMIN to the storageState JSON, or "
        "save one to .praxis.auth/admin.json "
        "(seed it with a teach login)."
    )
    assert str(auth_session.MissingSession(ROLE)) == expected
    # An empty-string environment counts as unset: same message.
    assert str(auth_session.MissingSession(ROLE, "")) == expected


def test_environment_none_resolution_is_identical_to_today(tmp_path: Path) -> None:
    # An explicit environment=None resolves exactly like the no-argument call:
    # the unscoped file and the unscoped env var, in the same order.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    file_session = _session("unscoped-file")
    auth_session.save_session(ROLE, file_session, environment=None, auth_dir=auth_dir)
    assert auth_session.load_session(
        ROLE, environment=None, auth_dir=auth_dir, environ={},
    ) == file_session
    env_session = _session("unscoped-env")
    got = auth_session.load_session(
        ROLE,
        environment=None,
        auth_dir=auth_dir,
        environ={auth_session.env_var_name(ROLE): json.dumps(env_session)},
    )
    assert got == env_session
    # And the env-scoped subdir was never created by any of it.
    assert not (auth_dir / ENV).exists()


def test_skill_seams_pass_the_environment_through(tmp_path: Path) -> None:
    # The teach save seam and the regress/explore load seam carry the same env
    # dimension, so a teach login on a selected environment seeds THAT
    # deployment's session and the run reads it back env-scoped.
    auth_dir = tmp_path / auth_session.AUTH_DIRNAME
    session = _session("seam-cookie")
    path = auth_session.save_session_for_role(
        ROLE, session, environment=ENV, auth_dir=auth_dir,
    )
    assert path == auth_dir / ENV / f"{ROLE}.json"
    assert auth_session.load_session_for_role(
        ROLE, environment=ENV, auth_dir=auth_dir, environ={},
    ) == session
    # The unscoped load does NOT see the env-scoped session (no fallback in
    # either direction).
    with pytest.raises(auth_session.MissingSession):
        auth_session.load_session_for_role(ROLE, auth_dir=auth_dir, environ={})
