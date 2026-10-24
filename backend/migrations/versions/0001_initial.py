"""Initial schema: extensions, core tables, row-level security and app-role grants.

Revision ID: 0001
Revises:
Create Date: 2026-10-25

Runs as the schema owner (VW_MIGRATION_DATABASE_URL). Requires the runtime role ``vendorwake_app`` to
exist already (created by infra/postgres/initdb locally; manually on Tiger Cloud, see migrations/README.md).

Enumeration values are frozen copies; change them in a new migration, not here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "vendorwake_app"

# Tenant tables in dependency order (users first). Policy column: users -> id, others -> user_id.
TENANT_TABLES = (
    "users",
    "repositories",
    "integrations",
    "investigations",
    "impact_evidence",
    "recommendations",
    "agent_sessions",
)
GLOBAL_TABLES = ("vendors", "monitoring_sources", "vendor_changes")

# NULLIF: once a transaction-local setting has been used on a connection, current_setting() returns ''
# (not NULL) in later transactions; ''::uuid would raise instead of matching nothing.
CURRENT_USER_ID = "(SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
CURRENT_AUTH_SUBJECT = "(SELECT NULLIF(current_setting('app.current_auth_subject', true), ''))"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _unit_interval(column: str) -> str:
    return f"{column} >= 0 AND {column} <= 1"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"
    )


def _investigation_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["investigation_id", "user_id"],
        ["investigations.id", "investigations.user_id"],
        name=op.f(f"fk_{table}_investigation_id_user_id_investigations"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendorwake_app') THEN
                RAISE EXCEPTION 'role vendorwake_app does not exist; create it before migrating '
                                '(see backend/migrations/README.md)';
            END IF;
        END
        $$;
        """
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- tenant root -----------------------------------------------------------------------------
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("auth_provider_id", sa.String(255), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("auth_provider_id", name=op.f("uq_users_auth_provider_id")),
    )
    op.create_table(
        "repositories",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), server_default=sa.text("'github'"), nullable=False),
        sa.Column("external_repository_id", sa.String(255), nullable=False),
        sa.Column("repository_name", sa.String(512), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        _user_fk("repositories"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_repository_id",
            name=op.f("uq_repositories_user_id_provider_external_repository_id"),
        ),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_repositories_id_user_id")),
        sa.CheckConstraint(_in("provider", ("github",)), name=op.f("ck_repositories_provider")),
    )

    # ---- global upstream data --------------------------------------------------------------------
    op.create_table(
        "vendors",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("website", sa.String(2048), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("monitoring_status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendors")),
        sa.UniqueConstraint("name", name=op.f("uq_vendors_name")),
        sa.CheckConstraint(
            _in("monitoring_status", ("active", "paused", "disabled")),
            name=op.f("ck_vendors_monitoring_status"),
        ),
    )
    op.create_table(
        "monitoring_sources",
        _uuid_pk(),
        sa.Column("vendor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_content_hash", sa.String(128), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitoring_sources")),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_monitoring_sources_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "vendor_id", "source_url", name=op.f("uq_monitoring_sources_vendor_id_source_url")
        ),
        sa.CheckConstraint(
            _in(
                "source_type",
                (
                    "changelog",
                    "documentation",
                    "release_notes",
                    "rss",
                    "github_releases",
                    "status_page",
                    "other",
                ),
            ),
            name=op.f("ck_monitoring_sources_source_type"),
        ),
    )
    op.create_table(
        "vendor_changes",
        _uuid_pk(),
        sa.Column("vendor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("change_type", sa.String(32), server_default=sa.text("'other'"), nullable=False),
        sa.Column("severity", sa.String(16), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("previous_content_reference", sa.Text(), nullable=True),
        sa.Column("current_content_reference", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_changes")),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_vendor_changes_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["monitoring_sources.id"],
            name=op.f("fk_vendor_changes_source_id_monitoring_sources"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in(
                "change_type",
                ("breaking", "deprecation", "feature", "fix", "security", "documentation", "other"),
            ),
            name=op.f("ck_vendor_changes_change_type"),
        ),
        sa.CheckConstraint(
            _in("severity", ("critical", "high", "medium", "low", "info", "unknown")),
            name=op.f("ck_vendor_changes_severity"),
        ),
    )
    op.create_index(op.f("ix_vendor_changes_source_id"), "vendor_changes", ["source_id"])
    op.create_index(
        op.f("ix_vendor_changes_vendor_id_detected_at"), "vendor_changes", ["vendor_id", "detected_at"]
    )

    # ---- tenant-owned data -----------------------------------------------------------------------
    op.create_table(
        "integrations",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("detected_version", sa.String(128), nullable=True),
        sa.Column("source_files", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        _user_fk("integrations"),
        sa.ForeignKeyConstraint(
            ["repository_id", "user_id"],
            ["repositories.id", "repositories.user_id"],
            name=op.f("fk_integrations_repository_id_user_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendors.id"], name=op.f("fk_integrations_vendor_id_vendors"), ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "repository_id",
            "vendor_id",
            "package_name",
            name=op.f("uq_integrations_repository_id_vendor_id_package_name"),
        ),
        sa.CheckConstraint(_unit_interval("confidence"), name=op.f("ck_integrations_confidence_range")),
    )
    op.create_index(op.f("ix_integrations_user_id"), "integrations", ["user_id"])
    op.create_index(op.f("ix_integrations_vendor_id"), "integrations", ["vendor_id"])

    op.create_table(
        "investigations",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("change_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("impact_status", sa.String(32), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("temporal_workflow_id", sa.String(255), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigations")),
        _user_fk("investigations"),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["vendor_changes.id"],
            name=op.f("fk_investigations_change_id_vendor_changes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id", "user_id"],
            ["repositories.id", "repositories.user_id"],
            name=op.f("fk_investigations_repository_id_user_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_investigations_id_user_id")),
        sa.UniqueConstraint("temporal_workflow_id", name=op.f("uq_investigations_temporal_workflow_id")),
        sa.CheckConstraint(
            _in("status", ("queued", "running", "completed", "failed", "cancelled")),
            name=op.f("ck_investigations_status"),
        ),
        sa.CheckConstraint(
            _in("impact_status", ("confirmed", "potential", "unaffected", "unknown")),
            name=op.f("ck_investigations_impact_status"),
        ),
        sa.CheckConstraint(_unit_interval("confidence"), name=op.f("ck_investigations_confidence_range")),
    )
    op.create_index(op.f("ix_investigations_user_id"), "investigations", ["user_id"])
    op.create_index(op.f("ix_investigations_change_id"), "investigations", ["change_id"])
    op.create_index(op.f("ix_investigations_repository_id"), "investigations", ["repository_id"])

    op.create_table(
        "impact_evidence",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_reference", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_evidence")),
        _user_fk("impact_evidence"),
        _investigation_fk("impact_evidence"),
        sa.CheckConstraint(
            _in(
                "evidence_type",
                (
                    "code_reference",
                    "dependency_manifest",
                    "documentation",
                    "changelog",
                    "test_result",
                    "other",
                ),
            ),
            name=op.f("ck_impact_evidence_evidence_type"),
        ),
        sa.CheckConstraint(_unit_interval("confidence"), name=op.f("ck_impact_evidence_confidence_range")),
    )
    op.create_index(op.f("ix_impact_evidence_user_id"), "impact_evidence", ["user_id"])
    op.create_index(op.f("ix_impact_evidence_investigation_id"), "impact_evidence", ["investigation_id"])

    op.create_table(
        "recommendations",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("proposed_patch_reference", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("approval_status", sa.String(32), server_default=sa.text("'pending'"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
        _user_fk("recommendations"),
        _investigation_fk("recommendations"),
        sa.CheckConstraint(
            _in(
                "recommendation_type",
                (
                    "code_change",
                    "dependency_upgrade",
                    "configuration_change",
                    "monitoring",
                    "no_action",
                    "other",
                ),
            ),
            name=op.f("ck_recommendations_recommendation_type"),
        ),
        sa.CheckConstraint(
            _in(
                "verification_status",
                (
                    "pending",
                    "passed",
                    "failed",
                    "partially_verified",
                    "not_testable",
                    "requires_human_review",
                ),
            ),
            name=op.f("ck_recommendations_verification_status"),
        ),
        sa.CheckConstraint(
            _in("approval_status", ("pending", "approved", "dismissed")),
            name=op.f("ck_recommendations_approval_status"),
        ),
    )
    op.create_index(op.f("ix_recommendations_user_id"), "recommendations", ["user_id"])
    op.create_index(op.f("ix_recommendations_investigation_id"), "recommendations", ["investigation_id"])

    op.create_table(
        "agent_sessions",
        _uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_session_id", sa.String(255), nullable=True),
        sa.Column("metadata", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
        _user_fk("agent_sessions"),
        _investigation_fk("agent_sessions"),
    )
    op.create_index(op.f("ix_agent_sessions_user_id"), "agent_sessions", ["user_id"])
    op.create_index(op.f("ix_agent_sessions_investigation_id"), "agent_sessions", ["investigation_id"])

    # ---- row-level security ----------------------------------------------------------------------
    # FORCE applies the policies to the table owner too (superusers and BYPASSRLS roles still bypass).
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # users: a row is visible when it is the scoped user, or (for first-login upsert) when it matches the
    # verified Auth0 subject the API placed in app.current_auth_subject.
    users_predicate = f"id = {CURRENT_USER_ID} OR auth_provider_id = {CURRENT_AUTH_SUBJECT}"
    op.execute(
        f"CREATE POLICY tenant_isolation ON users USING ({users_predicate}) WITH CHECK ({users_predicate})"
    )
    for table in TENANT_TABLES[1:]:
        predicate = f"user_id = {CURRENT_USER_ID}"
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")

    # ---- privileges for the runtime role ---------------------------------------------------------
    # Explicit per-table grants (no ALTER DEFAULT PRIVILEGES): a future table is unreachable by the app
    # role until its migration grants access and, for tenant data, enables RLS. UUID keys mean there are
    # no sequences to grant. Writes to global tables will come from a separate worker role later.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TENANT_TABLES)} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON {', '.join(GLOBAL_TABLES)} TO {APP_ROLE}")


def downgrade() -> None:
    # Dropping a table drops its policies and grants. Extensions are left in place: they may be shared
    # with other objects, and on Tiger Cloud they are managed by the platform.
    for table in (
        "agent_sessions",
        "recommendations",
        "impact_evidence",
        "investigations",
        "integrations",
        "vendor_changes",
        "monitoring_sources",
        "vendors",
        "repositories",
        "users",
    ):
        op.drop_table(table)
