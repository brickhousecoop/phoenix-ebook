"""Loading and backing up the store (#31, ADR 0004). The first part needs no database; the second
uses a real Postgres on this machine and skips without one (see ``postgres_server`` in conftest)."""
from __future__ import annotations

import importlib.util
import json
import stat

import psycopg
import pytest

from conftest import REPO, TEST_PASSWORD, TEST_USER, users_json, web_client
from phoenix_ebook.secrets import SecretStore
from web import auth, forms, store, store_admin
from web.store import Blog, SealedKey

BLOG_A = Blog("A Blog", "ghost", "https://ablog.ghost.io", ("ablog.com", "ablog.ghost.io"))
BLOG_B = Blog("B Blog", "ghost", "https://bblog.ghost.io", ("bblog.com",))
BLOGS = (BLOG_A, BLOG_B)
KEYS = {"ghost": {"ablog.ghost.io": "aaa:111", "bblog.ghost.io": "bbb:222"}}
MASTER = store.new_master_key()


def _script():
    spec = importlib.util.spec_from_file_location("store_admin_script", REPO / "scripts" / "store_admin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def files(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(users_json())
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps(KEYS))
    return users, keys


# ---------------------------------------------------------------- inputs (no database)

def test_inputs_become_sealed_data_and_current_users_get_every_blog(files):
    data = store_admin.data_from_files(BLOGS, files[0], files[1], MASTER)
    assert [blog for blog, _ in data.blogs] == list(BLOGS)
    assert store.unseal(data.blogs[0][1], BLOG_A, MASTER) == "aaa:111"
    assert list(data.users) == [TEST_USER]
    assert data.access == [(TEST_USER, "A Blog"), (TEST_USER, "B Blog")]


def test_without_a_keys_file_keys_come_from_this_computers_secret_store(files):
    SecretStore().set("ghost", "ablog.ghost.io", "from-store:1", prefer_keyring=False)
    SecretStore().set("ghost", "bblog.ghost.io", "from-store:2", prefer_keyring=False)
    data = store_admin.data_from_files(BLOGS, files[0], None, MASTER)
    assert store.unseal(data.blogs[1][1], BLOG_B, MASTER) == "from-store:2"


def test_everything_missing_is_named_together(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"ghost": {"ablog.ghost.io": "aaa:111"}}))
    with pytest.raises(store_admin.StoreAdminError) as err:
        store_admin.data_from_files(BLOGS, tmp_path / "no-users.json", keys, MASTER)
    message = str(err.value)
    assert "Nothing was written" in message
    assert "no-users.json" in message
    assert "B Blog" in message
    assert "A Blog" not in message
    assert "aaa:111" not in message


@pytest.mark.parametrize("content, says", [
    ("not json", "isn't valid JSON"),
    ("{}", "has no users"),
    ('{"a@b.org": "plain text password"}', "no password hash for a@b.org"),
])
def test_a_bad_user_list_is_named(tmp_path, files, content, says):
    users = tmp_path / "bad.json"
    users.write_text(content)
    with pytest.raises(store_admin.StoreAdminError, match=says) as err:
        store_admin.data_from_files(BLOGS, users, files[1], MASTER)
    assert "plain text password" not in str(err.value)


def test_a_missing_input_stops_the_script_before_it_asks_for_any_connection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("connected"))
    monkeypatch.setattr("getpass.getpass", lambda prompt: pytest.fail("asked for the connection"))
    assert _script().main(["import", "--users-file", str(tmp_path / "none.json")]) == 1
    assert "Nothing was written" in capsys.readouterr().err


def test_an_export_keeps_keys_encrypted_and_reads_back(files):
    data = store_admin.data_from_files(BLOGS, files[0], files[1], MASTER)
    text = store_admin.to_export(data)
    assert "aaa:111" not in text and "bbb:222" not in text and MASTER.split(".")[1] not in text
    back = store_admin.from_export(text)
    assert back == data
    assert store.unseal(back.blogs[0][1], BLOG_A, MASTER) == "aaa:111"


@pytest.mark.parametrize("text", ["not json", "{}", '{"version": 1}', '{"version": 1, "blogs": [1], "users": {}, "access": []}'])
def test_something_else_isnt_taken_for_an_export(text):
    with pytest.raises(store_admin.StoreAdminError, match="isn't a store export"):
        store_admin.from_export(text)


def test_an_export_from_a_later_version_is_refused():
    with pytest.raises(store_admin.StoreAdminError, match="version 2"):
        store_admin.from_export('{"version": 2}')


def test_an_export_file_is_readable_only_by_its_owner(tmp_path):
    path = tmp_path / "backups" / "x.json"
    store_admin.write_private(path, "secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_login_address_keeps_the_host_and_database():
    assert store_admin.login_url("postgresql://me:pw@host-pooler.example:5433/db?sslmode=require", "site", "p/w") == \
        "postgresql://site:p%2Fw@host-pooler.example:5433/db?sslmode=require"


def test_a_connection_that_isnt_an_address_is_refused():
    with pytest.raises(store_admin.StoreAdminError, match="must be an address"):
        store_admin.login_url("host=x dbname=y", "site", "pw")


# ---------------------------------------------------------------- real Postgres

@pytest.fixture
def postgres(postgres_server):
    """A clean database and no logins of ours."""
    conn = psycopg.connect(postgres_server, autocommit=True)
    with conn:
        conn.execute("DROP TABLE IF EXISTS access, users, blogs")
        for login in store_admin.READ_ONLY_LOGINS:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone():
                conn.execute(f"DROP OWNED BY {login}")
                conn.execute(f"DROP ROLE {login}")
    return postgres_server


def _load(url, data, new_passwords=False) -> dict[str, str]:
    with psycopg.connect(url) as conn:
        store_admin.create_tables(conn)
        store_admin.load(conn, data)
        return store_admin.set_up_logins(conn, url, new_passwords=new_passwords)


def _tables(url) -> store_admin.StoreData:
    with psycopg.connect(url) as conn:
        return store_admin.read_all(conn)


@pytest.fixture
def loaded(postgres, files):
    data = store_admin.data_from_files(BLOGS, files[0], files[1], MASTER)
    return postgres, data, _load(postgres, data)


def test_the_website_code_reads_what_the_import_wrote(loaded, monkeypatch):
    _, _, logins = loaded
    monkeypatch.setenv(store.URL_ENV, logins[store_admin.SITE_LOGIN])
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    connected = store.active()
    assert connected.blogs() == BLOGS
    assert forms.current_blogs() == BLOGS
    assert store.blog_key(connected, BLOG_B) == "bbb:222"
    assert auth.check_password(TEST_USER, TEST_PASSWORD) == TEST_USER
    assert web_client().get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("login", store_admin.READ_ONLY_LOGINS)
@pytest.mark.parametrize("statement", [
    "INSERT INTO users (email, password_hash) VALUES ('x@y.org', 'h')",
    "UPDATE users SET password_hash = 'h'",
    "DELETE FROM users",
    "UPDATE blogs SET key_ciphertext = NULL",
    "DELETE FROM access",
    "DROP TABLE access",
    "ALTER TABLE users ADD COLUMN extra text",
    "CREATE TABLE extra (id int)",
    "TRUNCATE users",
])
def test_the_read_only_logins_are_refused_every_write(loaded, login, statement):
    _, _, logins = loaded
    with psycopg.connect(logins[login], autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_re_running_makes_no_duplicates_and_keeps_login_passwords(loaded):
    url, data, _ = loaded
    assert _load(url, data) == {}
    assert _tables(url) == data


def test_new_login_passwords_replace_the_old_ones(loaded):
    url, data, logins = loaded
    new = _load(url, data, new_passwords=True)
    with psycopg.connect(new[store_admin.SITE_LOGIN]) as conn:
        assert conn.execute("SELECT 1").fetchone()
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(logins[store_admin.SITE_LOGIN])


def test_re_running_makes_the_store_match_the_inputs(loaded):
    url, data, _ = loaded
    changed = store_admin.StoreData(
        blogs=[(BLOG_A, store.seal("aaa:new", BLOG_A, MASTER))],
        users={"new@example.org": "$argon2id$new"},
        access=[("new@example.org", "A Blog")],
    )
    _load(url, changed)
    assert _tables(url) == changed
    with psycopg.connect(url) as conn:
        assert not conn.execute("SELECT 1 FROM users WHERE email = %s", (TEST_USER,)).fetchone()


def test_a_failed_run_leaves_nothing_half_loaded(loaded):
    url, data, _ = loaded
    broken = store_admin.StoreData(
        blogs=[(BLOG_A, data.blogs[0][1])],
        users={"new@example.org": "$argon2id$new"},
        access=[("new@example.org", "No Such Blog")],
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _load(url, broken)
    assert _tables(url) == data


def test_export_wipe_restore_gives_back_identical_tables_and_working_keys(loaded):
    url, data, _ = loaded
    text = store_admin.to_export(_tables(url))
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM access")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM blogs")
    assert _tables(url).users == {}
    restored = store_admin.from_export(text)
    _load(url, restored)
    assert _tables(url) == data
    assert store.unseal(_tables(url).blogs[0][1], BLOG_A, MASTER) == "aaa:111"


def test_a_key_copied_between_rows_doesnt_decrypt(loaded):
    url, _, _ = loaded
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("UPDATE blogs SET key_ciphertext = (SELECT key_ciphertext FROM blogs WHERE name = 'A Blog') "
                     "WHERE name = 'B Blog'")
    sealed: SealedKey = _tables(url).blogs[1][1]
    with pytest.raises(store.StoreKeyProblem):
        store.unseal(sealed, BLOG_B, MASTER)


def _run_import(monkeypatch, url, files, *extra):
    monkeypatch.setenv(_script().ADMIN_URL_ENV, url)
    monkeypatch.setattr(forms, "BLOGS", BLOGS)
    script = _script()
    script.BLOGS = BLOGS
    return script.main(["import", "--users-file", str(files[0]), "--keys-file", str(files[1]), *extra])


def test_the_first_run_shows_the_master_key_and_logins_once(postgres, files, monkeypatch, capsys):
    monkeypatch.delenv(store.MASTER_KEY_ENV, raising=False)
    assert _run_import(monkeypatch, postgres, files) == 0
    shown = capsys.readouterr().out
    assert "Master key" in shown
    assert store_admin.SITE_LOGIN in shown and store_admin.CHECK_LOGIN in shown
    assert "aaa:111" not in shown


def test_a_second_run_without_the_master_key_is_refused_and_changes_nothing(postgres, files, monkeypatch, capsys):
    monkeypatch.delenv(store.MASTER_KEY_ENV, raising=False)
    assert _run_import(monkeypatch, postgres, files) == 0
    before = _tables(postgres)
    capsys.readouterr()
    assert _run_import(monkeypatch, postgres, files) == 1
    assert store.MASTER_KEY_ENV in capsys.readouterr().err
    assert _tables(postgres) == before


def test_the_export_command_uses_a_read_only_login_and_the_import_command_restores(loaded, tmp_path, monkeypatch, capsys):
    url, data, logins = loaded
    script = _script()
    monkeypatch.delenv(script.ADMIN_URL_ENV, raising=False)
    monkeypatch.setenv(store.URL_ENV, logins[store_admin.CHECK_LOGIN])
    path = tmp_path / "backup.json"
    assert script.main(["export", "--to", str(path)]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM blogs")
    monkeypatch.setenv(script.ADMIN_URL_ENV, url)
    assert script.main(["import", "--from-export", str(path)]) == 0
    assert _tables(url) == data
    assert "Master key" not in capsys.readouterr().out


# ---------------------------------------------------------------- access (#41)

def _run(monkeypatch, url, tmp_path, *argv):
    script = _script()
    monkeypatch.setenv(script.ADMIN_URL_ENV, url)
    monkeypatch.setattr(script, "BACKUP_DIR", tmp_path / "backups")
    return script, script.main(list(argv))


def test_revoke_and_grant_change_what_the_website_allows_and_are_backed_up(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    monkeypatch.setenv(store.URL_ENV, logins[store_admin.SITE_LOGIN])
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    assert forms.blogs_for(TEST_USER)[1] == BLOGS

    _, code = _run(monkeypatch, url, tmp_path, "revoke", TEST_USER.upper(), "bblog")
    assert code == 0
    assert "can no longer build from B Blog" in capsys.readouterr().out
    assert forms.blogs_for(TEST_USER) == (BLOGS, (BLOG_A,))
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert store_admin.from_export(backups[0].read_text()).access == [(TEST_USER, "A Blog")]

    _, code = _run(monkeypatch, url, tmp_path, "grant", TEST_USER, "B Blog")
    assert code == 0 and "can now build from B Blog" in capsys.readouterr().out
    assert forms.blogs_for(TEST_USER)[1] == BLOGS


def test_granting_twice_changes_nothing_and_makes_no_backup(loaded, tmp_path, monkeypatch, capsys):
    url, data, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, "grant", TEST_USER, "ablog.com")
    assert code == 0 and "could already build from A Blog" in capsys.readouterr().out
    assert not (tmp_path / "backups").exists()
    assert _tables(url) == data


@pytest.mark.parametrize("argv, says", [
    (["grant", TEST_USER, "nope"], "No blog called 'nope'. The blogs are: A Blog, B Blog"),
    (["revoke", "stranger@example.org", "ablog"], "stranger@example.org isn't a user"),
])
def test_an_unknown_blog_or_user_is_named_and_nothing_changes(loaded, tmp_path, monkeypatch, capsys, argv, says):
    url, data, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, *argv)
    assert code == 1 and says in capsys.readouterr().err
    assert _tables(url) == data


def test_users_lists_each_users_blogs(loaded, tmp_path, monkeypatch, capsys):
    url, _, _ = loaded
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("INSERT INTO users VALUES ('zed@example.org', '$argon2id$x')")
    _, code = _run(monkeypatch, url, tmp_path, "users")
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[0].split() == [TEST_USER, "A", "Blog,", "B", "Blog"]
    assert lines[1].split() == ["zed@example.org", "(no", "blogs)"]


def test_re_importing_keeps_each_users_blogs_and_new_users_start_with_none(postgres, files, tmp_path, monkeypatch):
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    assert _run_import(monkeypatch, postgres, files) == 0
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute("DELETE FROM access WHERE blog = 'B Blog'")
    users = json.loads(files[0].read_text()) | {"new@example.org": "$argon2id$new"}
    files[0].write_text(json.dumps(users))
    assert _run_import(monkeypatch, postgres, files) == 0
    assert _tables(postgres).access == [(TEST_USER, "A Blog")]
