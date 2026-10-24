# Local PostgreSQL 17 with TimescaleDB and pgvector, for development and integration tests.
# Production uses Tiger Cloud, which ships TimescaleDB, pgvector and pgvectorscale preinstalled.
#
# Build: docker build -t vendorwake-postgres:dev infra/postgres
FROM postgres:17-bookworm

# Pinned for reproducible builds. Bump deliberately; keep close to the Tiger Cloud version.
ARG TIMESCALEDB_VERSION=2.31.0

# - TimescaleDB comes from Timescale's official packagecloud apt repository.
# - pgvector comes from the PGDG apt repository that the official image already configures.
# - The TimescaleDB package bundles the shared libraries of every previous release (for in-place
#   extension upgrades). A fresh dev database never needs them, so they are removed to shrink the image.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ bookworm main" \
        > /etc/apt/sources.list.d/timescaledb.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "timescaledb-2-loader-postgresql-${PG_MAJOR}=${TIMESCALEDB_VERSION}~debian12*" \
        "timescaledb-2-postgresql-${PG_MAJOR}=${TIMESCALEDB_VERSION}~debian12*" \
        "postgresql-${PG_MAJOR}-pgvector"; \
    find "/usr/lib/postgresql/${PG_MAJOR}/lib" -maxdepth 1 -name 'timescaledb-*.so' ! -name "*-${TIMESCALEDB_VERSION}.so" -delete; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

# TimescaleDB must be preloaded. Editing the sample file means every initdb'd cluster picks it up.
# Telemetry is disabled for local development.
RUN set -eux; \
    sample=/usr/share/postgresql/postgresql.conf.sample; \
    echo "shared_preload_libraries = 'timescaledb'" >> "$sample"; \
    echo "timescaledb.telemetry_level = 'off'" >> "$sample"

# Runs once, on first start with an empty data directory.
COPY initdb/ /docker-entrypoint-initdb.d/
