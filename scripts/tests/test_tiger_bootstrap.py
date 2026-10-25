import envfile
import tiger_bootstrap as tb

OWNER = "postgresql+psycopg://tsdbadmin:own%2Fer-pw@abc.xyz.tsdb.cloud.timescale.com:31234/tsdb"


def test_db_url_roundtrip_and_hiding():
    u = tb.DbUrl.parse(OWNER).with_sslmode_require()
    assert u.password == "own/er-pw" and u.database == "tsdb" and u.port == 31234
    assert u.render() == OWNER + "?sslmode=require"
    assert "own" not in u.render(hide_password=True).split("@")[0].split(":")[-1]
    assert u.libpq().startswith("postgresql://")
    app = u.with_credentials("vendorwake_app", "p@ss word")
    assert (
        app.render()
        == "postgresql+psycopg://vendorwake_app:p%40ss%20word@abc.xyz.tsdb.cloud.timescale.com:31234/tsdb?sslmode=require"
    )


def test_placeholder_passwords_are_generated():
    gen = tb.ensure_passwords({"VW_APP_DB_PASSWORD": "change-me-app", "VW_WORKER_DB_PASSWORD": "kept"})
    assert list(gen) == ["VW_APP_DB_PASSWORD"] and len(gen["VW_APP_DB_PASSWORD"]) >= 32


def test_dry_run_prints_shapes_without_passwords(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text(f"VW_MIGRATION_DATABASE_URL={OWNER}\nVW_APP_DB_PASSWORD=app-secret-pw\nVW_WORKER_DB_PASSWORD=\n")
    before = p.read_text()
    assert tb.main(["--env-file", str(p), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "own/er-pw" not in out and "own%2Fer-pw" not in out and "app-secret-pw" not in out
    assert "VW_DATABASE_URL=postgresql+psycopg://vendorwake_app:<password>@abc.xyz" in out
    assert "NOBYPASSRLS" in out and "CREATE EXTENSION IF NOT EXISTS vector" in out
    assert p.read_text() == before


class FakeConn:
    def __init__(self, log, roles, user):
        self.log, self.roles, self.user = log, roles, user
        self.pgconn = self

    def encrypt_password(self, pw, user, algo):
        assert algo == b"scram-sha-256"
        return b"SCRAM-SHA-256$4096:salt$stored:server"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, params=None):
        self.log.append((self.user, repr(query), params))
        q = str(query) if isinstance(query, str) else repr(query)
        rows = []
        if "current_user, rolsuper" in q:
            rows = [("tsdbadmin", False, True)]
        elif "FROM pg_extension" in q:
            rows = [("2.20.0",)] if params == ("timescaledb",) else []
        elif "rolsuper, rolbypassrls, rolcanlogin" in q:
            rows = [self.roles[params[0]]] if params[0] in self.roles else []
        elif "pg_database_size" in q:
            rows = [(10 * 1024 * 1024,)]
        elif "rolbypassrls FROM pg_roles WHERE rolname = current_user" in q:
            rows = [(False,)]
        return type("R", (), {"fetchone": lambda s: rows[0] if rows else None})()


def test_bootstrap_creates_missing_roles_with_hashed_password(tmp_path, capsys):
    log = []
    roles = {"vendorwake_app": (False, False, True)}

    def connect(dsn, **kw):
        user = dsn.split("//")[1].split(":")[0]
        return FakeConn(log, roles, user)

    p = tmp_path / ".env"
    p.write_text(f"VW_MIGRATION_DATABASE_URL={OWNER}\nVW_APP_DB_PASSWORD=app-secret-pw\nVW_WORKER_DB_PASSWORD=\n")
    assert tb.main(["--env-file", str(p)], connect=connect) == 0
    sql = "\n".join(q for _, q, _ in log)
    assert "app-secret-pw" not in sql  # only the SCRAM verifier is sent
    assert "CREATE EXTENSION IF NOT EXISTS" in sql and "'vector'" in sql
    assert "CREATE ROLE" in sql and "'vendorwake_worker'" in sql and "SCRAM-SHA-256$4096" in sql
    assert sql.count("CREATE ROLE") == 1  # vendorwake_app already existed
    assert {u for u, _, _ in log} == {"tsdbadmin", "vendorwake_app", "vendorwake_worker"}
    env = envfile.load(p)
    assert env["VW_DATABASE_URL"].startswith("postgresql+psycopg://vendorwake_app:app-secret-pw@abc.xyz")
    assert env["VW_WORKER_DATABASE_URL"].endswith("/tsdb?sslmode=require")
    assert len(env["VW_WORKER_DB_PASSWORD"]) >= 32
    out = capsys.readouterr().out
    assert "app-secret-pw" not in out and env["VW_WORKER_DB_PASSWORD"] not in out
    assert "1% of the 750 MiB free-plan limit" in out
