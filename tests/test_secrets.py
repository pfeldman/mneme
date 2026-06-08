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
