"""Loading, backing up and managing the store (#31, #40, #41, ADR 0004). The first part needs no
database; the rest uses a real Postgres on this machine and skips without one (see
``postgres_server`` in conftest)."""
from __future__ import annotations

import importlib.util
import json
import stat
from dataclasses import replace

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
    assert [blog for blog, _ in data.blogs] == [replace(BLOG_A, short_name="ablog"), replace(BLOG_B, short_name="bblog")]
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
        blogs=[(replace(BLOG_A, short_name="a"), store.seal("aaa:new", BLOG_A, MASTER))],
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
    assert "already has users or blogs" in capsys.readouterr().err
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
    (["grant", TEST_USER, "nope"], "No blog called 'nope'. The blogs are: A Blog (ablog), B Blog (bblog)"),
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


def test_importing_the_lists_again_is_refused_even_with_the_master_key(postgres, files, monkeypatch, capsys):
    """It would remove users and blogs added with the commands since (#40)."""
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    assert _run_import(monkeypatch, postgres, files) == 0
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute("INSERT INTO users VALUES ('added@example.org', '$argon2id$x')")
    before = _tables(postgres)
    capsys.readouterr()
    assert _run_import(monkeypatch, postgres, files) == 1
    assert "import --from-export" in capsys.readouterr().err
    assert _tables(postgres) == before


# ---------------------------------------------------------------- users and blogs (#40)

def _password(out: str) -> str:
    return out.split("privately):\n", 1)[1].split()[0]


def _site(monkeypatch, logins):
    monkeypatch.setenv(store.URL_ENV, logins[store_admin.SITE_LOGIN])
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)


def test_user_add_then_grant_lets_them_sign_in_and_use_the_blog(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    _site(monkeypatch, logins)
    _, code = _run(monkeypatch, url, tmp_path, "user", "add", " Ana@Example.org ")
    out = capsys.readouterr().out
    assert code == 0 and "can't build from any blog until you grant one" in out
    password = _password(out)
    assert auth.check_password("ana@example.org", password) == "ana@example.org"
    assert forms.blogs_for("ana@example.org")[1] == ()
    assert len(list((tmp_path / "backups").iterdir())) == 1

    _run(monkeypatch, url, tmp_path, "grant", "ana@example.org", "ablog")
    client = web_client(None)
    signed_in = client.post("/sign-in", data={"email": "ana@example.org", "password": password},
                            headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert signed_in.status_code == 303
    page = " ".join(client.get("/").text.split())
    assert "Turns a list of posts from A Blog into an EPUB" in page


def test_adding_a_user_twice_is_refused(loaded, tmp_path, monkeypatch, capsys):
    url, data, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, "user", "add", TEST_USER)
    assert code == 1 and "already a user" in capsys.readouterr().err
    assert _tables(url) == data


def test_new_password_replaces_the_old_one(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    _site(monkeypatch, logins)
    _, code = _run(monkeypatch, url, tmp_path, "user", "new-password", TEST_USER)
    password = _password(capsys.readouterr().out)
    assert code == 0
    assert auth.check_password(TEST_USER, TEST_PASSWORD) is None
    assert auth.check_password(TEST_USER, password) == TEST_USER


def test_user_remove_takes_their_blogs_too(loaded, tmp_path, monkeypatch, capsys):
    url, _, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, "user", "remove", TEST_USER)
    assert code == 0 and "signed out at their next page" in capsys.readouterr().out
    tables = _tables(url)
    assert tables.users == {} and tables.access == []


def _with_key(monkeypatch, key):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": key if prompt.startswith("Key for") else "")


def test_blog_add_stores_its_key_encrypted_and_nobody_has_it_yet(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    _site(monkeypatch, logins)
    _with_key(monkeypatch, "ccc:333")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "add", "C Blog", "--url", "https://cblog.ghost.io/",
                   "--hosts", "www.cblog.com, cblog.com")
    out = capsys.readouterr().out
    assert code == 0 and "ccc:333" not in out
    c_blog = Blog("C Blog", "ghost", "https://cblog.ghost.io", ("www.cblog.com", "cblog.com"))
    assert forms.blogs_for(TEST_USER) == ((BLOG_A, BLOG_B, c_blog), BLOGS)
    assert store.blog_key(store.active(), c_blog) == "ccc:333"
    backup = next((tmp_path / "backups").iterdir()).read_text()
    assert "ccc:333" not in backup


def test_blog_add_without_hosts_uses_the_api_host(loaded, tmp_path, monkeypatch):
    url, _, _ = loaded
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    _with_key(monkeypatch, "ccc:333")
    _run(monkeypatch, url, tmp_path, "blog", "add", "C Blog", "--url", "https://cblog.ghost.io")
    assert _tables(url).blogs[-1][0].hosts == ("cblog.ghost.io",)


@pytest.mark.parametrize("argv, says", [
    (["--url", "https://new.ghost.io", "--hosts", "bblog.com"], "bblog.com already belongs to B Blog"),
    (["--url", "https://bblog.ghost.io"], "B Blog already has that name or address"),
    (["--url", "http://new.ghost.io"], "isn't an https address"),
    (["--url", "https://new.ghost.io", "--platform", "myspace"], "unknown platform 'myspace'"),
])
def test_a_blog_that_clashes_or_is_malformed_is_refused(loaded, tmp_path, monkeypatch, capsys, argv, says):
    url, data, _ = loaded
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    _with_key(monkeypatch, "ccc:333")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "add", "New", *argv)
    assert code == 1 and says in capsys.readouterr().err
    assert _tables(url) == data


def test_a_different_master_key_is_refused(loaded, tmp_path, monkeypatch, capsys):
    url, data, _ = loaded
    monkeypatch.setenv(store.MASTER_KEY_ENV, store.new_master_key())
    _with_key(monkeypatch, "ccc:333")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "set-key", "ablog")
    assert code == 1 and "Use the one in your password manager" in capsys.readouterr().err
    assert _tables(url) == data


def test_set_key_replaces_the_key(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    _site(monkeypatch, logins)
    _with_key(monkeypatch, "aaa:new")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "set-key", "ablog.com")
    assert code == 0 and "aaa:new" not in capsys.readouterr().out
    assert store.blog_key(store.active(), BLOG_A) == "aaa:new"


def test_new_login_passwords_is_its_own_command(loaded, tmp_path, monkeypatch, capsys):
    url, _, logins = loaded
    _, code = _run(monkeypatch, url, tmp_path, "new-login-passwords")
    assert code == 0 and store_admin.SITE_LOGIN in capsys.readouterr().out
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(logins[store_admin.SITE_LOGIN])


# ---------------------------------------------------------------- short names

OLD_TABLES = """
CREATE TABLE blogs (name text PRIMARY KEY, platform text NOT NULL, url text NOT NULL UNIQUE, hosts text[] NOT NULL,
                    key_ciphertext bytea, key_master_id text);
CREATE TABLE users (email text PRIMARY KEY, password_hash text NOT NULL);
CREATE TABLE access (email text NOT NULL REFERENCES users ON DELETE CASCADE,
                     blog text NOT NULL REFERENCES blogs ON DELETE CASCADE ON UPDATE CASCADE, PRIMARY KEY (email, blog));
"""


def test_a_store_from_before_short_names_gets_them_on_its_first_change(postgres, tmp_path, monkeypatch, capsys):
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(OLD_TABLES)
        conn.execute("INSERT INTO blogs VALUES ('Welcome to Hell World', 'ghost', 'https://hellworld.ghost.io', "
                     "'{www.welcometohellworld.com}', NULL, NULL)")
        conn.execute("INSERT INTO users VALUES (%s, '$argon2id$x')", (TEST_USER,))
    _, code = _run(monkeypatch, postgres, tmp_path, "blog", "list")  # reading works before the upgrade
    assert code == 0 and capsys.readouterr().out.startswith("-")
    assert _run(monkeypatch, postgres, tmp_path, "export", "--to", str(tmp_path / "old.json"))[1] == 0
    _, code = _run(monkeypatch, postgres, tmp_path, "grant", TEST_USER, "hellworld")
    assert code == 0, capsys.readouterr().err
    assert _tables(postgres).blogs[0][0].short_name == "hellworld"
    assert _tables(postgres).access == [(TEST_USER, "Welcome to Hell World")]


def test_blog_add_takes_a_short_name_and_grant_uses_it(loaded, tmp_path, monkeypatch, capsys):
    url, _, _ = loaded
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    _with_key(monkeypatch, "ccc:333")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "add", "Defector", "--url", "https://lede-admin.defector.com",
                   "--hosts", "defector.com", "--short-name", "Defector")
    assert code == 0 and "(defector)" in capsys.readouterr().out
    _, code = _run(monkeypatch, url, tmp_path, "grant", TEST_USER, "defector")
    assert code == 0 and (TEST_USER, "Defector") in _tables(url).access


@pytest.mark.parametrize("short_name, says", [("bblog", "B Blog already has the short name bblog"),
                                              ("no spaces", "can't be a short name")])
def test_a_taken_or_malformed_short_name_is_refused(loaded, tmp_path, monkeypatch, capsys, short_name, says):
    url, data, _ = loaded
    monkeypatch.setenv(store.MASTER_KEY_ENV, MASTER)
    _with_key(monkeypatch, "ccc:333")
    _, code = _run(monkeypatch, url, tmp_path, "blog", "add", "New", "--url", "https://new.ghost.io",
                   "--short-name", short_name)
    assert code == 1 and says in capsys.readouterr().err
    _, code = _run(monkeypatch, url, tmp_path, "blog", "short-name", "ablog", short_name)
    assert code == 1 and says in capsys.readouterr().err
    assert _tables(url) == data


def test_changing_a_short_name(loaded, tmp_path, monkeypatch, capsys):
    url, _, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, "blog", "short-name", "ablog", "first")
    assert code == 0 and "A Blog is now first" in capsys.readouterr().out
    _, code = _run(monkeypatch, url, tmp_path, "revoke", TEST_USER, "first")
    assert code == 0
    _, code = _run(monkeypatch, url, tmp_path, "revoke", TEST_USER, "ablog")
    assert code == 1 and "No blog called 'ablog'" in capsys.readouterr().err


def test_blog_list_shows_short_names_hosts_and_whether_a_key_is_stored(loaded, tmp_path, monkeypatch, capsys):
    url, _, _ = loaded
    _, code = _run(monkeypatch, url, tmp_path, "blog", "list")
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[0].split() == ["ablog", "A", "Blog", "(ablog.com,", "ablog.ghost.io;", "key:", "stored)"]


def test_an_export_from_before_short_names_still_restores(loaded):
    url, data, _ = loaded
    raw = json.loads(store_admin.to_export(data))
    assert raw["blogs"][0]["short_name"] == "ablog"
    for blog in raw["blogs"]:
        del blog["short_name"]
    assert store_admin.from_export(json.dumps(raw)) == data
