#!/usr/bin/env python3
"""Bootstrap a Tiger Cloud (TimescaleDB) service for Vendorwake. Run once per service, before the
first migration; safe to re-run.

As the service owner (``tsdbadmin``, from VW_MIGRATION_DATABASE_URL) it:
  1. creates the extensions ``timescaledb`` and ``vector`` if missing;
  2. creates the login roles ``vendorwake_app`` (API) and ``vendorwake_worker`` (Temporal worker) if
     missing: NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT, so row-level
     security applies to them; grants CONNECT on the database and USAGE on schema public
     (table privileges come from the Alembic migrations);
  3. checks each role can log in with its password and is not BYPASSRLS;
  4. writes VW_DATABASE_URL and VW_WORKER_DATABASE_URL into .env (mode 600) and prints their shape
     without passwords.

Passwords come from VW_APP_DB_PASSWORD / VW_WORKER_DB_PASSWORD. If one is empty or still the
.env.example placeholder, a random one is generated and written to .env first. Passwords are sent to
the server already hashed (SCRAM-SHA-256 via libpq's PQencryptPasswordConn), so they never appear in
server statement logs, and are never printed.

An existing role keeps its password unless --rotate-passwords is given (rotating breaks running
services until their VW_DATABASE_URL is updated and redeployed).

Tiger Cloud notes (verified 2026-10-25):
  * "Tiger Cloud does not provide superuser access. tsdbadmin is not a superuser", but it can create
    roles with standard PostgreSQL commands:
    https://www.tigerdata.com/docs/use-timescale/latest/security/read-only-role
  * Extensions are added as tsdbadmin with CREATE EXTENSION IF NOT EXISTS:
    https://www.tigerdata.com/docs/mst/latest/extensions
  * NOBYPASSRLS is PostgreSQL's default and needs no privilege; *granting* BYPASSRLS needs a role
    that has it (https://www.postgresql.org/docs/current/sql-createrole.html), so never plan on it.
  * Free plan: shared compute, 750 MiB storage per service (read-only beyond that); verify on
    https://www.tigerdata.com/docs/about/latest/pricing-and-account-management

Usage:
  python scripts/tiger_bootstrap.py --dry-run
  python scripts/tiger_bootstrap.py [--rotate-passwords]
"""

from __future__ import annotations

import argparse
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envfile  # noqa: E402

EXTENSIONS = ("timescaledb", "vector")
ROLE_ATTRS = "LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
FREE_PLAN_STORAGE_BYTES = 750 * 1024 * 1024


@dataclass(frozen=True)
class RoleSpec:
    name: str
    password_key: str
    url_key: str


ROLES = (
    RoleSpec("vendorwake_app", "VW_APP_DB_PASSWORD", "VW_DATABASE_URL"),
    RoleSpec("vendorwake_worker", "VW_WORKER_DB_PASSWORD", "VW_WORKER_DATABASE_URL"),
)


@dataclass(frozen=True)
class DbUrl:
    scheme: str
    user: str
    password: str
    host: str
    port: int | None
    database: str
    query: str

    @classmethod
    def parse(cls, url: str) -> DbUrl:
        u = urlsplit(url)
        if not u.scheme.startswith("postgres"):
            raise ValueError("not a postgres URL")
        return cls(
            scheme=u.scheme,
            user=unquote(u.username or ""),
            password=unquote(u.password or ""),
            host=u.hostname or "",
            port=u.port,
            database=(u.path or "/").lstrip("/") or "postgres",
            query=u.query,
        )

    def with_credentials(self, user: str, password: str) -> DbUrl:
        return DbUrl(self.scheme, user, password, self.host, self.port, self.database, self.query)

    def with_sslmode_require(self) -> DbUrl:
        if "sslmode=" in self.query or self.host in ("localhost", "127.0.0.1", "postgres"):
            return self
        q = f"{self.query}&sslmode=require" if self.query else "sslmode=require"
        return DbUrl(self.scheme, self.user, self.password, self.host, self.port, self.database, q)

    def render(self, *, hide_password: bool = False, scheme: str | None = None) -> str:
        pw = "<password>" if hide_password else quote(self.password, safe="")
        netloc = f"{quote(self.user, safe='')}:{pw}@{self.host}" + (f":{self.port}" if self.port else "")
        return urlunsplit((scheme or self.scheme, netloc, f"/{self.database}", self.query, ""))

    def libpq(self) -> str:
        """Plain postgresql:// URL for psycopg.connect (SQLAlchemy's +psycopg suffix removed)."""
        return self.render(scheme="postgresql")


def ensure_passwords(env: dict[str, str]) -> dict[str, str]:
    """Return {password_key: new_password} for every role password that is unset or a placeholder."""
    return {r.password_key: secrets.token_urlsafe(32) for r in ROLES if not envfile.is_set(env, r.password_key)}


def plan_sql(db: str) -> list[str]:
    """Statements with the password elided, for --dry-run output."""
    out = [f"CREATE EXTENSION IF NOT EXISTS {e}" for e in EXTENSIONS]
    for r in ROLES:
        out.append(f"CREATE ROLE {r.name} {ROLE_ATTRS} PASSWORD '<scram-sha-256 verifier>'  -- if missing")
        out.append(f"GRANT CONNECT ON DATABASE {db} TO {r.name}")
        out.append(f"GRANT USAGE ON SCHEMA public TO {r.name}")
    return out


def bootstrap(admin_url: DbUrl, passwords: dict[str, str], *, rotate: bool, connect=None) -> list[str]:
    """Run the bootstrap. Returns human-readable action lines (no secrets)."""
    import psycopg
    from psycopg import sql

    connect = connect or psycopg.connect
    actions: list[str] = []
    with connect(admin_url.libpq(), autocommit=True, connect_timeout=15) as conn:
        info = conn.execute(
            "SELECT current_user, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        actions.append(f"connected as {info[0]} (superuser={info[1]}, createrole={info[2]})")
        if not info[1] and not info[2]:
            raise RuntimeError(f"{info[0]} has neither SUPERUSER nor CREATEROLE; use the tsdbadmin URL")

        for ext in EXTENSIONS:
            row = conn.execute("SELECT extversion FROM pg_extension WHERE extname = %s", (ext,)).fetchone()
            if row:
                actions.append(f"OK extension {ext} {row[0]}")
            else:
                conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
                actions.append(f"CREATE extension {ext}")

        for r in ROLES:
            pw = passwords[r.password_key]
            verifier = conn.pgconn.encrypt_password(pw.encode(), r.name.encode(), b"scram-sha-256").decode()
            row = conn.execute(
                "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s", (r.name,)
            ).fetchone()
            if row is None:
                conn.execute(
                    sql.SQL("CREATE ROLE {} " + ROLE_ATTRS + " PASSWORD {}").format(
                        sql.Identifier(r.name), sql.Literal(verifier)
                    )
                )
                actions.append(f"CREATE role {r.name}")
            else:
                if row[0] or row[1] or not row[2]:
                    conn.execute(sql.SQL("ALTER ROLE {} " + ROLE_ATTRS).format(sql.Identifier(r.name)))
                    actions.append(f"ALTER role {r.name}: reset attributes (was super={row[0]} bypassrls={row[1]})")
                if rotate:
                    conn.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(r.name), sql.Literal(verifier))
                    )
                    actions.append(f"ALTER role {r.name}: password rotated")
                else:
                    actions.append(f"OK role {r.name} exists (password unchanged)")
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(admin_url.database), sql.Identifier(r.name)
                )
            )
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(r.name)))
            actions.append(f"GRANT CONNECT, USAGE ON SCHEMA public TO {r.name}")

        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        actions.append(
            f"database size {size / 1024 / 1024:.1f} MiB "
            f"({100 * size / FREE_PLAN_STORAGE_BYTES:.0f}% of the 750 MiB free-plan limit)"
        )

    for r in ROLES:
        url = admin_url.with_credentials(r.name, passwords[r.password_key])
        try:
            with connect(url.libpq(), connect_timeout=15) as c:
                bypass = c.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 - report the class only; messages can echo the DSN
            raise RuntimeError(
                f"login as {r.name} failed ({type(exc).__name__}); if the role predates this .env, "
                "re-run with --rotate-passwords"
            ) from None
        if bypass:
            raise RuntimeError(f"{r.name} has BYPASSRLS; row-level security would not apply")
        actions.append(f"VERIFIED login as {r.name} (NOBYPASSRLS)")
    return actions


def main(argv: list[str] | None = None, connect=None) -> int:
    ap = argparse.ArgumentParser(description="Create extensions and RLS-bound roles on Tiger Cloud.")
    ap.add_argument("--env-file", type=Path, default=envfile.default_env_path())
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no connection, no writes")
    ap.add_argument("--rotate-passwords", action="store_true", help="also reset passwords of existing roles")
    args = ap.parse_args(argv)

    env = envfile.load(args.env_file)
    raw = env.get("VW_MIGRATION_DATABASE_URL", "")
    try:
        admin = DbUrl.parse(raw).with_sslmode_require()
    except ValueError:
        print("error: VW_MIGRATION_DATABASE_URL is not a postgres URL", file=sys.stderr)
        return 2
    if admin.host in ("localhost", "127.0.0.1") and not args.dry_run:
        print("note: VW_MIGRATION_DATABASE_URL points at a local database, not Tiger Cloud")

    generated = ensure_passwords(env)
    passwords = {r.password_key: generated.get(r.password_key) or env.get(r.password_key, "") for r in ROLES}

    print(f"Owner connection: {admin.render(hide_password=True)}")
    urls = {r.url_key: admin.with_credentials(r.name, passwords[r.password_key]) for r in ROLES}
    if args.dry_run:
        for stmt in plan_sql(admin.database):
            print(f"  [dry-run] {stmt}")
        if generated:
            print(f"  [dry-run] would generate {', '.join(generated)} and write them to .env")
        for key, url in urls.items():
            print(f"  [dry-run] would write {key}={url.render(hide_password=True)}")
        return 0

    if generated:
        # Persist generated passwords before creating roles, so a crash can't lose them.
        envfile.write_updates(args.env_file, generated, header="Written by scripts/tiger_bootstrap.py")
        print(f"Generated {', '.join(generated)} (stored in {args.env_file})")
    try:
        for line in bootstrap(admin, passwords, rotate=args.rotate_passwords, connect=connect):
            print(f"  {line}")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: database bootstrap failed ({type(exc).__name__})", file=sys.stderr)
        return 1

    updates = {key: url.render() for key, url in urls.items()}
    if admin.render() != raw:
        updates["VW_MIGRATION_DATABASE_URL"] = admin.render()  # adds sslmode=require
    changed = envfile.write_updates(args.env_file, updates, header="Written by scripts/tiger_bootstrap.py")
    for key, url in urls.items():
        print(f"{key}={url.render(hide_password=True)}")
    print(f"Updated {args.env_file} (mode 600): {', '.join(changed) if changed else 'no changes'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
