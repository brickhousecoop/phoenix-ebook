"""scripts/add_user.py: adding, resetting and removing website users (#34, ADR 0003)."""
from __future__ import annotations

import importlib.util
import io
import json
import stat

import pytest

from conftest import REPO
from web import auth

spec = importlib.util.spec_from_file_location("add_user", REPO / "scripts" / "add_user.py")
add_user = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_user)


def password_from(out: str) -> str:
    return out.split("send it to them privately):\n  ", 1)[1].split("\n", 1)[0]


def users_value_from(out: str) -> dict:
    return json.loads(out.rstrip().rsplit("\n", 1)[1])


def test_adds_a_user_to_a_new_private_file(tmp_path, capsys, monkeypatch):
    path = tmp_path / "users.json"
    assert add_user.main(["Ana@Example.org", "--users-file", str(path)]) == 0
    out = capsys.readouterr().out
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    password = password_from(out)
    assert len(password) >= 20
    assert password not in path.read_text()
    # The printed value is what Vercel gets, and the website reads it back as the same user.
    value = users_value_from(out)
    assert value == json.loads(path.read_text())
    monkeypatch.setenv(auth.USERS_ENV, json.dumps(value))
    assert auth.check_password("ana@example.org", password) == "ana@example.org"


def test_running_it_again_resets_the_password(tmp_path, capsys, monkeypatch):
    path = tmp_path / "users.json"
    add_user.main(["ana@example.org", "--users-file", str(path)])
    first = password_from(capsys.readouterr().out)
    add_user.main(["ANA@example.org", "--users-file", str(path)])
    second = password_from(capsys.readouterr().out)
    users = json.loads(path.read_text())
    assert list(users) == ["ana@example.org"]
    monkeypatch.setenv(auth.USERS_ENV, json.dumps(users))
    assert auth.check_password("ana@example.org", second)
    assert not auth.check_password("ana@example.org", first)


def test_remove(tmp_path, capsys):
    path = tmp_path / "users.json"
    add_user.main(["ana@example.org", "--users-file", str(path)])
    add_user.main(["bo@example.org", "--users-file", str(path)])
    capsys.readouterr()
    assert add_user.main(["ana@example.org", "--remove", "--users-file", str(path)]) == 0
    assert list(json.loads(path.read_text())) == ["bo@example.org"]
    assert "Removed ana@example.org" in capsys.readouterr().out
    assert add_user.main(["ana@example.org", "--remove", "--users-file", str(path)]) == 1
    assert "isn't a user" in capsys.readouterr().err


def test_default_file_is_the_shared_phoenix_one(capsys):
    assert 'Path.home() / ".phoenix_users.json"' in (REPO / "web" / "auth.py").read_text()
    add_user.main(["ana@example.org"])  # the test setup points USERS_FILE at a temporary file
    capsys.readouterr()
    assert "ana@example.org" in json.loads(auth.USERS_FILE.read_text())


def test_standard_input_to_standard_output(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"bo@example.org": "h"})))
    assert add_user.main(["ana@example.org", "--users-file", "-"]) == 0
    captured = capsys.readouterr()
    users = json.loads(captured.out)
    assert set(users) == {"ana@example.org", "bo@example.org"}
    assert password_from(captured.err)


def test_a_broken_list_is_an_error_not_overwritten(tmp_path, capsys):
    path = tmp_path / "users.json"
    path.write_text("[1, 2]")
    assert add_user.main(["ana@example.org", "--users-file", str(path)]) == 1
    assert path.read_text() == "[1, 2]"
    assert "isn't a JSON object" in capsys.readouterr().err


def test_help_warns_the_local_file_is_a_copy(capsys):
    with pytest.raises(SystemExit):
        add_user.main(["--help"])
    help_text = capsys.readouterr().out
    assert "holds the real list" in help_text and "never contacts Vercel" in help_text
