"""Workflow persistence: snapshots hypertable, discovery history, change embeddings, investigation results.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25

Adds:

* ``source_snapshots``: TimescaleDB hypertable on ``retrieved_at`` (global).
* ``discovery_queries``: search-provider query history (global).
* ``change_embeddings``: ``vector(384)`` per vendor change with an HNSW cosine index (global, pgvector).
* ``investigation_steps``: per-step outputs keyed by ``(investigation_id, step)`` (tenant, RLS).
* columns for extraction/grounding/model calls, repository analysis, investigation reports and timelines,
  recommendation patches/verification and Entire session details.
* role ``vendorwake_worker``: writes the global tables (the app role keeps read-only access to them).
  Created here if missing, LOGIN but without a password. If ``VW_WORKER_DB_PASSWORD`` is set in the
  migration's environment, the password is set (or rotated) from it; the value travels as a bind parameter
  and never appears in SQL text.
* function ``vendorwake_set_vendor_monitoring``: SECURITY DEFINER, the only global write the app role
  can make (start/stop monitoring a vendor).

Enumeration values are frozen copies; change them in a new migration, not here.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "vendorwake_app"
WORKER_ROLE = "vendorwake_worker"
WORKER_PASSWORD_ENV = "VW_WORKER_DB_PASSWORD"  # noqa: S105 (env var name, not a password)
EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5; frozen here on purpose

CURRENT_USER_ID = "(SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid)"

NEW_GLOBAL_TABLES = ("source_snapshots", "discovery_queries", "change_embeddings")
ALL_GLOBAL_TABLES = ("vendors", "monitoring_sources", "vendor_changes", *NEW_GLOBAL_TABLES)

# (table, constraint suffix, column, old values, new values, fallback for downgrade)
_OLD_CHANGE_TYPES = ("breaking", "deprecation", "feature", "fix", "security", "documentation", "other")
_NEW_CHANGE_TYPES = (
    *_OLD_CHANGE_TYPES,
    "removal",
    "rename",
    "schema_change",
    "auth_change",
    "rate_limit",
    "sdk_version",
    "behavioral",
    "additive",
)
_OLD_INV_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
_NEW_INV_STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "cancelled")
_OLD_EVIDENCE = (
    "code_reference",
    "dependency_manifest",
    "documentation",
    "changelog",
    "test_result",
    "other",
)
_NEW_EVIDENCE = (
    *_OLD_EVIDENCE,
    "vendor_source",
    "search_result",
    "dependency",
    "version_check",
    "model_inference",
)
_OLD_REC_TYPES = (
    "code_change",
    "dependency_upgrade",
    "configuration_change",
    "monitoring",
    "no_action",
    "other",
)
_NEW_REC_TYPES = (*_OLD_REC_TYPES, "dependency_pin", "review", "test")
_OLD_MONITORING = ("active", "paused", "disabled")
_NEW_MONITORING = ("active", "paused", "off", "disabled")

_ENUM_CHANGES = (
    ("vendor_changes", "change_type", "change_type", _OLD_CHANGE_TYPES, _NEW_CHANGE_TYPES, "other"),
    ("investigations", "status", "status", _OLD_INV_STATUSES, _NEW_INV_STATUSES, "running"),
    ("impact_evidence", "evidence_type", "evidence_type", _OLD_EVIDENCE, _NEW_EVIDENCE, "other"),
    (
        "recommendations",
        "recommendation_type",
        "recommendation_type",
        _OLD_REC_TYPES,
        _NEW_REC_TYPES,
        "other",
    ),
    ("vendors", "monitoring_status", "monitoring_status", _OLD_MONITORING, _NEW_MONITORING, "disabled"),
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _jsonb_list(name: str) -> sa.Column:
    return sa.Column(name, pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False)


def _replace_check(table: str, suffix: str, column: str, values: tuple[str, ...]) -> None:
    name = f"ck_{table}_{suffix}"
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in(column, values))


def _create_worker_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendorwake_worker') THEN
                CREATE ROLE vendorwake_worker LOGIN
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
            END IF;
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO vendorwake_worker', current_database());
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {WORKER_ROLE}")

    password = os.environ.get(WORKER_PASSWORD_ENV, "")
    if password and not context.is_offline_mode():
        bind = op.get_bind()
        # Bind parameter -> transaction-local setting -> format(%L) on the server. The password never
        # appears in statement text (logs, pg_stat_activity), and the setting is cleared right after.
        bind.execute(sa.text("SELECT set_config('vendorwake.worker_password', :pw, true)"), {"pw": password})
        bind.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    EXECUTE format('ALTER ROLE {WORKER_ROLE} PASSWORD %L',
                                   current_setting('vendorwake.worker_password'));
                    PERFORM set_config('vendorwake.worker_password', '', true);
                END
                $$;
                """
            )
        )


def upgrade() -> None:
    _create_worker_role()

    # ---- enumerations ----------------------------------------------------------------------------
    for table, suffix, column, _old, new, _fallback in _ENUM_CHANGES:
        _replace_check(table, suffix, column, new)

    # ---- global: vendors / vendor_changes --------------------------------------------------------
    op.add_column("vendors", _jsonb_list("official_domains"))
    op.add_column("vendors", sa.Column("schedule_id", sa.String(255), nullable=True))
    op.alter_column("vendors", "monitoring_status", server_default=sa.text("'off'"))
    # 0001 defaulted to 'active' although nothing was scheduled; no schedule existed before this revision.
    op.execute("UPDATE vendors SET monitoring_status = 'off' WHERE monitoring_status = 'active'")

    for column in (
        sa.Column("package", sa.String(255), nullable=True),
        sa.Column("version", sa.String(128), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("snapshot_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_via", sa.String(16), nullable=True),
        sa.Column("browser_session_id", sa.String(255), nullable=True),
        sa.Column("is_official", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("change_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("extraction", pg.JSONB(), nullable=True),
        sa.Column("grounding", pg.JSONB(), nullable=True),
        sa.Column("grounding_ok", sa.Boolean(), nullable=True),
        _jsonb_list("model_calls"),
        _jsonb_list("discovery"),
    ):
        op.add_column("vendor_changes", column)
    op.create_check_constraint(
        op.f("ck_vendor_changes_fetched_via"), "vendor_changes", _in("fetched_via", ("http", "browserbase"))
    )
    op.create_unique_constraint(
        op.f("uq_vendor_changes_vendor_id_content_sha256"), "vendor_changes", ["vendor_id", "content_sha256"]
    )

    # ---- global: source_snapshots (hypertable) ---------------------------------------------------
    op.create_table(
        "source_snapshots",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vendor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("fetched_via", sa.String(16), nullable=False),
        sa.Column("browser_session_id", sa.String(255), nullable=True),
        sa.Column("screenshot_ref", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(16), nullable=True),
        sa.Column("change_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", "retrieved_at", name=op.f("pk_source_snapshots")),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_source_snapshots_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["monitoring_sources.id"],
            name=op.f("fk_source_snapshots_source_id_monitoring_sources"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in("fetched_via", ("http", "browserbase")), name=op.f("ck_source_snapshots_fetched_via")
        ),
        sa.CheckConstraint(
            _in("extraction_status", ("change", "no_change", "failed")),
            name=op.f("ck_source_snapshots_extraction_status"),
        ),
    )
    # No default indexes: the composite indexes below cover every lookup (latest snapshot per URL/vendor).
    op.execute(
        "SELECT create_hypertable('source_snapshots', by_range('retrieved_at', INTERVAL '30 days'), "
        "create_default_indexes => false)"
    )
    op.create_index(op.f("ix_source_snapshots_url_retrieved_at"), "source_snapshots", ["url", "retrieved_at"])
    op.create_index(
        op.f("ix_source_snapshots_vendor_id_retrieved_at"), "source_snapshots", ["vendor_id", "retrieved_at"]
    )

    # ---- global: discovery_queries ---------------------------------------------------------------
    op.create_table(
        "discovery_queries",
        _uuid_pk(),
        sa.Column("vendor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(32), server_default=sa.text("'serpapi'"), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("result_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _jsonb_list("results"),
        sa.Column("workflow_id", sa.String(255), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_queries")),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_discovery_queries_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_discovery_queries_vendor_id_executed_at"), "discovery_queries", ["vendor_id", "executed_at"]
    )

    # ---- global: change_embeddings (pgvector) ----------------------------------------------------
    op.create_table(
        "change_embeddings",
        sa.Column("change_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("change_id", name=op.f("pk_change_embeddings")),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["vendor_changes.id"],
            name=op.f("fk_change_embeddings_change_id_vendor_changes"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_change_embeddings_embedding",
        "change_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # ---- tenant: repositories --------------------------------------------------------------------
    for column in (
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("ref", sa.String(255), server_default=sa.text("'main'"), nullable=False),
        sa.Column("subpath", sa.String(1024), server_default=sa.text("''"), nullable=False),
        sa.Column("analysis_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("analysis_error", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("inventory", pg.JSONB(), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("repositories", column)
    op.create_check_constraint(
        op.f("ck_repositories_analysis_status"),
        "repositories",
        _in("analysis_status", ("pending", "running", "completed", "failed")),
    )

    # ---- tenant: investigations ------------------------------------------------------------------
    for column in (
        sa.Column("report", pg.JSONB(), nullable=True),
        _jsonb_list("timeline"),
        sa.Column("temporal_run_id", sa.String(255), nullable=True),
        sa.Column("sentry_trace_id", sa.String(64), nullable=True),
        sa.Column("backboard_thread_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
    ):
        op.add_column("investigations", column)

    # ---- tenant: impact_evidence / recommendations / agent_sessions ------------------------------
    for column in (
        sa.Column("deterministic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
    ):
        op.add_column("impact_evidence", column)

    for column in (
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("verification", pg.JSONB(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("recommendations", column)
    op.create_unique_constraint(
        op.f("uq_recommendations_investigation_id_position"),
        "recommendations",
        ["investigation_id", "position"],
    )

    for column in (
        sa.Column("checkpoint_id", sa.String(64), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("transcript_excerpt", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("agent_sessions", column)
    op.create_unique_constraint(
        op.f("uq_agent_sessions_investigation_id_provider"),
        "agent_sessions",
        ["investigation_id", "provider"],
    )

    # ---- tenant: investigation_steps -------------------------------------------------------------
    op.create_table(
        "investigation_steps",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", pg.JSONB(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_steps")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_investigation_steps_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id", "user_id"],
            ["investigations.id", "investigations.user_id"],
            name=op.f("fk_investigation_steps_investigation_id_user_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "investigation_id", "step", name=op.f("uq_investigation_steps_investigation_id_step")
        ),
        sa.CheckConstraint(
            _in("status", ("completed", "failed")), name=op.f("ck_investigation_steps_status")
        ),
    )
    op.create_index(op.f("ix_investigation_steps_user_id"), "investigation_steps", ["user_id"])
    op.execute("ALTER TABLE investigation_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE investigation_steps FORCE ROW LEVEL SECURITY")
    predicate = f"user_id = {CURRENT_USER_ID}"
    op.execute(
        f"CREATE POLICY tenant_isolation ON investigation_steps USING ({predicate}) WITH CHECK ({predicate})"
    )

    # ---- privileges ------------------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON investigation_steps TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON {', '.join(NEW_GLOBAL_TABLES)} TO {APP_ROLE}")
    # The worker role writes global data only. It gets no access to tenant tables at all: tenant writes from
    # the worker go through tenant_session() on the RLS-restricted app role.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(ALL_GLOBAL_TABLES)} TO {WORKER_ROLE}")

    # The one global write the API may make: start/stop monitoring (upserting the vendor row from the
    # catalog if it does not exist yet). Inputs are validated; nothing else about vendors can be changed.
    op.execute(
        """
        CREATE FUNCTION vendorwake_set_vendor_monitoring(
            p_name text, p_status text, p_schedule_id text,
            p_category text, p_website text, p_official_domains jsonb
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF p_status IS NULL OR p_status NOT IN ('active', 'paused') THEN
                RAISE EXCEPTION 'invalid monitoring status' USING ERRCODE = '22023';
            END IF;
            IF p_name IS NULL OR p_name !~ '^[a-z0-9][a-z0-9_.-]{0,63}$' THEN
                RAISE EXCEPTION 'invalid vendor name' USING ERRCODE = '22023';
            END IF;
            IF p_official_domains IS NOT NULL AND jsonb_typeof(p_official_domains) <> 'array' THEN
                RAISE EXCEPTION 'official domains must be a JSON array' USING ERRCODE = '22023';
            END IF;
            INSERT INTO public.vendors
                (name, category, website, official_domains, monitoring_status, schedule_id)
            VALUES (p_name, left(p_category, 64), left(p_website, 2048),
                    COALESCE(p_official_domains, '[]'::jsonb), p_status, left(p_schedule_id, 255))
            ON CONFLICT (name) DO UPDATE SET
                monitoring_status = EXCLUDED.monitoring_status,
                schedule_id = COALESCE(EXCLUDED.schedule_id, public.vendors.schedule_id),
                category = COALESCE(public.vendors.category, EXCLUDED.category),
                website = COALESCE(public.vendors.website, EXCLUDED.website),
                official_domains = CASE WHEN public.vendors.official_domains = '[]'::jsonb
                                        THEN EXCLUDED.official_domains
                                        ELSE public.vendors.official_domains END,
                updated_at = now();
        END
        $$;
        """
    )
    signature = "vendorwake_set_vendor_monitoring(text, text, text, text, text, jsonb)"
    op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS vendorwake_set_vendor_monitoring(text, text, text, text, text, jsonb)"
    )

    op.drop_table("investigation_steps")

    op.drop_constraint(op.f("uq_agent_sessions_investigation_id_provider"), "agent_sessions", type_="unique")
    for column in ("checkpoint_id", "commit_sha", "model", "transcript_excerpt", "url", "ended_at"):
        op.drop_column("agent_sessions", column)

    op.drop_constraint(
        op.f("uq_recommendations_investigation_id_position"), "recommendations", type_="unique"
    )
    for column in ("position", "file_path", "patch", "verification", "decided_at"):
        op.drop_column("recommendations", column)

    for column in ("deterministic", "line", "quote", "position"):
        op.drop_column("impact_evidence", column)

    for column in (
        "report",
        "timeline",
        "temporal_run_id",
        "sentry_trace_id",
        "backboard_thread_id",
        "started_at",
        "error",
    ):
        op.drop_column("investigations", column)

    op.drop_constraint(op.f("ck_repositories_analysis_status"), "repositories", type_="check")
    for column in (
        "owner",
        "name",
        "ref",
        "subpath",
        "analysis_status",
        "analysis_error",
        "commit_sha",
        "inventory",
        "last_analyzed_at",
    ):
        op.drop_column("repositories", column)

    op.drop_table("change_embeddings")
    op.drop_table("discovery_queries")
    op.drop_table("source_snapshots")  # drops the hypertable's chunks too

    op.drop_constraint(op.f("uq_vendor_changes_vendor_id_content_sha256"), "vendor_changes", type_="unique")
    op.drop_constraint(op.f("ck_vendor_changes_fetched_via"), "vendor_changes", type_="check")
    for column in (
        "package",
        "version",
        "content_sha256",
        "snapshot_id",
        "retrieved_at",
        "fetched_via",
        "browser_session_id",
        "is_official",
        "change_count",
        "requires_human_review",
        "extraction",
        "grounding",
        "grounding_ok",
        "model_calls",
        "discovery",
    ):
        op.drop_column("vendor_changes", column)

    op.alter_column("vendors", "monitoring_status", server_default=sa.text("'active'"))
    op.drop_column("vendors", "schedule_id")
    op.drop_column("vendors", "official_domains")

    # Values introduced by 0002 do not exist in 0001: map them to the closest 0001 value (lossy by nature).
    for table, suffix, column, old, _new, fallback in _ENUM_CHANGES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = :fallback WHERE NOT ({_in(column, old)})"  # noqa: S608
            ).bindparams(fallback=fallback)
        )
        _replace_check(table, suffix, column, old)

    # The role is cluster-wide and may be shared, so it stays; only this database's privileges are removed.
    op.execute(f"REVOKE ALL ON vendors, monitoring_sources, vendor_changes FROM {WORKER_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {WORKER_ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {WORKER_ROLE}', current_database());
        END
        $$;
        """
    )
