"""The credentials channel (ADR-0021 decision 6, ADR-0019 surface split).

These tests pin the contract of `praxis.secrets`:

    - an environment variable BEATS the `.praxis.secrets` file for the same key;
    - the file supplies a key the environment does not;
    - an absent key raises `MissingCredential` naming the absent key;
    - the console / CI helper (`require_credential`) exits non-zero and names the
      key, with no interactive prompt;
    - NO code path echoes a secret VALUE to stdout, stderr, or an exception
      message (only key NAMES and the `<value>` placeholder ever leave the
      module).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from praxis import secrets

# A distinctive sentinel value that must never appear in any output, exception
# message, prompt, or append command this module produces. If any test sees this
# string leak into a surfaced channel, the no-echo contract is broken.
SECRET_VALUE = "s3cr3t-VALUE-must-not-leak-9f3a"
KEY = "APP_PASSWORD"


def _write_secrets(path: Path, **pairs: str) -> Path:
    lines = [f"{k}={v}" for k, v in pairs.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- env beats file -------------------------------------------------------


def test_env_var_beats_the_file(tmp_path: Path) -> None:
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: "from-file"})
    got = secrets.get_credential(
        KEY,
        secrets_path=secrets_path,
        environ={KEY: "from-env"},
    )
    assert got == "from-env"


def test_file_supplies_a_key_absent_from_env(tmp_path: Path) -> None:
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    got = secrets.get_credential(KEY, secrets_path=secrets_path, environ={})
    assert got == SECRET_VALUE


def test_empty_env_value_falls_through_to_file(tmp_path: Path) -> None:
    # An empty environment variable is not a supplied secret; the file wins.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    got = secrets.get_credential(KEY, secrets_path=secrets_path, environ={KEY: ""})
    assert got == SECRET_VALUE


def test_value_may_contain_equals_signs(tmp_path: Path) -> None:
    # Split on the FIRST '=' so a base64 token or a connection string survives.
    token = "abc=def==ghi"
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: token})
    assert secrets.get_credential(KEY, secrets_path=secrets_path, environ={}) == token


def test_file_parser_skips_blanks_and_comments(tmp_path: Path) -> None:
    p = tmp_path / secrets.SECRETS_FILENAME
    p.write_text(
        "# a comment\n\n   \n" + f"{KEY}={SECRET_VALUE}\n" + "# trailing comment\n",
        encoding="utf-8",
    )
    parsed = secrets.load_secrets_file(p)
    assert parsed == {KEY: SECRET_VALUE}


def test_missing_file_is_empty_not_an_error(tmp_path: Path) -> None:
    assert secrets.load_secrets_file(tmp_path / secrets.SECRETS_FILENAME) == {}


# --- absent key raises MissingCredential naming the key -------------------


def test_absent_key_raises_missing_credential_naming_the_key(tmp_path: Path) -> None:
    secrets_path = tmp_path / secrets.SECRETS_FILENAME  # does not exist
    with pytest.raises(secrets.MissingCredential) as ei:
        secrets.get_credential(KEY, secrets_path=secrets_path, environ={})
    assert ei.value.key == KEY
    # The exception names the absent key in its string form.
    assert KEY in str(ei.value)


def test_missing_credential_is_a_keyerror_subclass() -> None:
    # Callers already guarding KeyError keep working.
    assert issubclass(secrets.MissingCredential, KeyError)


# --- console / CI helper: exits non-zero, names the key, no prompt --------


def test_require_credential_returns_value_when_present(tmp_path: Path) -> None:
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    assert secrets.require_credential(KEY, secrets_path=secrets_path, environ={}) == SECRET_VALUE


def test_require_credential_exits_nonzero_and_names_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_path = tmp_path / secrets.SECRETS_FILENAME  # absent
    with pytest.raises(SystemExit) as ei:
        secrets.require_credential(KEY, secrets_path=secrets_path, environ={})
    # Non-zero exit (loud failure, no prompt).
    assert ei.value.code != 0
    err = capsys.readouterr().err
    # The loud message names the absent key and how to set it.
    assert KEY in err
    assert secrets.SECRETS_FILENAME in err


def test_require_credential_does_not_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No interactive prompt: reading stdin must never be attempted on the
    # console surface. Force any stdin read to blow up so a prompt would fail
    # the test loudly rather than hang.
    def _boom() -> str:
        raise AssertionError("require_credential must not read stdin (no prompt)")

    monkeypatch.setattr("sys.stdin.readline", _boom)
    secrets_path = tmp_path / secrets.SECRETS_FILENAME
    with pytest.raises(SystemExit):
        secrets.require_credential(KEY, secrets_path=secrets_path, environ={})


# --- the skill-surface hook asks and offers the append command ------------


def test_ask_prompt_offers_exact_append_command() -> None:
    text = secrets.ask_prompt(KEY)
    assert KEY in text
    assert secrets.SECRETS_FILENAME in text
    # The exact append command, with the placeholder, not a real value.
    assert f'! echo "{KEY}=<value>" >> {secrets.SECRETS_FILENAME}' in text


def test_append_command_uses_placeholder_not_a_value() -> None:
    cmd = secrets.append_command(KEY)
    assert cmd == f'! echo "{KEY}=<value>" >> {secrets.SECRETS_FILENAME}'


# --- no code path echoes a secret value -----------------------------------


def test_no_path_prints_a_secret_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Drive every surface that could plausibly touch a value and assert the
    # sentinel never reaches stdout, stderr, an exception string, a prompt, or
    # the append command.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})

    # 1. Successful read returns the value to the CALLER but prints nothing.
    assert secrets.get_credential(KEY, secrets_path=secrets_path, environ={}) == SECRET_VALUE
    assert secrets.require_credential(KEY, secrets_path=secrets_path, environ={}) == SECRET_VALUE
    captured = capsys.readouterr()
    assert SECRET_VALUE not in captured.out
    assert SECRET_VALUE not in captured.err

    # 2. The user-facing hooks (prompt + append command) never embed the value,
    #    even though the value is present in the file they reference.
    assert SECRET_VALUE not in secrets.ask_prompt(KEY)
    assert SECRET_VALUE not in secrets.append_command(KEY)

    # 3. The loud failure path (a DIFFERENT, absent key) names the key, never a
    #    value, and prints nothing that contains the present secret either.
    with pytest.raises(SystemExit):
        secrets.require_credential(
            "OTHER_KEY", secrets_path=secrets_path, environ={}
        )
    err = capsys.readouterr().err
    assert "OTHER_KEY" in err
    assert SECRET_VALUE not in err


def test_missing_credential_message_carries_no_value(tmp_path: Path) -> None:
    # Even when the file HAS other secrets, a MissingCredential for an absent key
    # must not surface any present value in its message.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    with pytest.raises(secrets.MissingCredential) as ei:
        secrets.get_credential("ABSENT_KEY", secrets_path=secrets_path, environ={})
    assert SECRET_VALUE not in str(ei.value)
    assert "ABSENT_KEY" in str(ei.value)


# --- per-environment overlay (ADR-0035 decision 7) -------------------------

ENV = "dev2"
OVERLAY_VALUE = "overlay-s3cr3t-must-not-leak-7c1b"


def _write_overlay(tmp_path: Path, env: str, **pairs: str) -> Path:
    return _write_secrets(tmp_path / secrets.overlay_filename(env), **pairs)


def test_overlay_filename_is_base_plus_env_suffix() -> None:
    assert secrets.overlay_filename(ENV) == f"{secrets.SECRETS_FILENAME}.{ENV}"


def test_overlay_wins_over_base_for_a_key_it_defines(tmp_path: Path) -> None:
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: "from-base"})
    _write_overlay(tmp_path, ENV, **{KEY: OVERLAY_VALUE})
    got = secrets.get_credential(KEY, environment=ENV, secrets_path=secrets_path, environ={})
    assert got == OVERLAY_VALUE


def test_key_absent_from_overlay_falls_through_to_base(tmp_path: Path) -> None:
    # It is an OVERLAY, not a replacement: shared keys live once in the base.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    _write_overlay(tmp_path, ENV, OTHER_KEY="overlay-only")
    got = secrets.get_credential(KEY, environment=ENV, secrets_path=secrets_path, environ={})
    assert got == SECRET_VALUE


def test_env_var_wins_over_overlay_and_base(tmp_path: Path) -> None:
    # The KEY environment variable stays the absolute winner (ADR-0021 dec. 6).
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: "from-base"})
    _write_overlay(tmp_path, ENV, **{KEY: "from-overlay"})
    got = secrets.get_credential(
        KEY, environment=ENV, secrets_path=secrets_path, environ={KEY: "from-env"}
    )
    assert got == "from-env"


def test_missing_overlay_file_falls_through_to_base(tmp_path: Path) -> None:
    # An environment selected but no overlay file written: base resolves, no error.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    got = secrets.get_credential(KEY, environment=ENV, secrets_path=secrets_path, environ={})
    assert got == SECRET_VALUE


def test_environment_none_never_consults_the_overlay(tmp_path: Path) -> None:
    # Undeclared projects are byte-identical to today: a present overlay file is
    # invisible when no environment is selected.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    _write_overlay(tmp_path, ENV, **{KEY: "poison-if-read"})
    got = secrets.get_credential(KEY, secrets_path=secrets_path, environ={})
    assert got == SECRET_VALUE


def test_empty_string_environment_counts_as_unset(tmp_path: Path) -> None:
    # ADR-0034 posture: an empty string at any level counts as unset.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    _write_overlay(tmp_path, "", **{KEY: "poison-if-read"})
    got = secrets.get_credential(KEY, environment="", secrets_path=secrets_path, environ={})
    assert got == SECRET_VALUE


def test_missing_credential_message_unchanged_without_environment(tmp_path: Path) -> None:
    # The env=None message is pinned byte-for-byte: backward compatibility is a
    # hard requirement (ADR-0035 zero-ceremony bar).
    with pytest.raises(secrets.MissingCredential) as ei:
        secrets.get_credential(KEY, secrets_path=tmp_path / secrets.SECRETS_FILENAME, environ={})
    assert str(ei.value) == (
        f"missing credential {KEY!r}: set it as an environment "
        f"variable or add it to {secrets.SECRETS_FILENAME} "
        f"(KEY=value, one per line)."
    )
    assert ei.value.environment is None


def test_missing_credential_with_environment_names_the_overlay(tmp_path: Path) -> None:
    with pytest.raises(secrets.MissingCredential) as ei:
        secrets.get_credential(
            KEY,
            environment=ENV,
            secrets_path=tmp_path / secrets.SECRETS_FILENAME,
            environ={},
        )
    assert ei.value.key == KEY
    assert ei.value.environment == ENV
    msg = str(ei.value)
    assert KEY in msg
    assert secrets.overlay_filename(ENV) in msg
    assert secrets.SECRETS_FILENAME in msg


def test_require_credential_reads_the_overlay(tmp_path: Path) -> None:
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: "from-base"})
    _write_overlay(tmp_path, ENV, **{KEY: OVERLAY_VALUE})
    got = secrets.require_credential(
        KEY, environment=ENV, secrets_path=secrets_path, environ={}
    )
    assert got == OVERLAY_VALUE


def test_require_credential_loud_failure_names_the_overlay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_path = tmp_path / secrets.SECRETS_FILENAME  # absent, overlay absent too
    with pytest.raises(SystemExit) as ei:
        secrets.require_credential(KEY, environment=ENV, secrets_path=secrets_path, environ={})
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert KEY in err
    assert secrets.SECRETS_FILENAME in err
    assert secrets.overlay_filename(ENV) in err


def test_require_credential_loud_failure_message_unchanged_without_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The env=None loud message is pinned byte-for-byte (no overlay line).
    with pytest.raises(SystemExit):
        secrets.require_credential(
            KEY, secrets_path=tmp_path / secrets.SECRETS_FILENAME, environ={}
        )
    err = capsys.readouterr().err
    assert err == (
        f"ERROR: missing credential {KEY!r}.\n"
        f"  set it as an environment variable:  export {KEY}=...\n"
        f"  or add it to {secrets.SECRETS_FILENAME}:        "
        f'echo "{KEY}=<value>" >> {secrets.SECRETS_FILENAME}\n'
        f"  ({secrets.SECRETS_FILENAME} is gitignored; the value is never "
        f"committed or logged.)\n"
    )


def test_no_overlay_path_prints_a_secret_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The no-echo contract extends to the overlay channel: a value read from
    # the overlay never reaches stdout, stderr, or an exception message.
    secrets_path = _write_secrets(tmp_path / secrets.SECRETS_FILENAME, **{KEY: SECRET_VALUE})
    _write_overlay(tmp_path, ENV, **{KEY: OVERLAY_VALUE})

    got = secrets.get_credential(KEY, environment=ENV, secrets_path=secrets_path, environ={})
    assert got == OVERLAY_VALUE
    captured = capsys.readouterr()
    assert OVERLAY_VALUE not in captured.out
    assert OVERLAY_VALUE not in captured.err

    # The loud failure for a DIFFERENT, absent key surfaces neither the base
    # nor the overlay value.
    with pytest.raises(SystemExit):
        secrets.require_credential(
            "ABSENT_KEY", environment=ENV, secrets_path=secrets_path, environ={}
        )
    err = capsys.readouterr().err
    assert "ABSENT_KEY" in err
    assert OVERLAY_VALUE not in err
    assert SECRET_VALUE not in err
