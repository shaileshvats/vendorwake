"""Vendors and monitoring status.

The vendor catalog (``vendorwake.catalog``) is the source of signatures; the ``vendors`` table holds what
has been monitored or discovered. ``GET /vendors`` merges both. The app role cannot write ``vendors``
directly: start/stop goes through the SECURITY DEFINER function ``vendorwake_set_vendor_monitoring``
(migration 0002); everything else is written by the worker role.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vendorwake.db.models import MonitoringSource, Vendor
from vendorwake.domain.contracts import VendorSignature
from vendorwake.services.schemas import VendorOut, VendorSource

VENDOR_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def normalize_name(name: str) -> str:
    return name.strip().lower()


def api_status(db_status: str | None) -> str:
    return db_status if db_status in ("active", "paused") else "off"


def list_vendors(session: Session, signatures: list[VendorSignature]) -> list[VendorOut]:
    vendors = {v.name: v for v in session.scalars(select(Vendor))}
    sources: dict[uuid.UUID, list[MonitoringSource]] = {}
    for src in session.scalars(select(MonitoringSource).order_by(MonitoringSource.source_url)):
        sources.setdefault(src.vendor_id, []).append(src)
    by_name = {s.vendor: s for s in signatures}
    out: list[VendorOut] = []
    for name in sorted(set(vendors) | set(by_name)):
        row, sig = vendors.get(name), by_name.get(name)
        domains = list(row.official_domains or []) if row is not None else []
        if not domains and sig is not None:
            domains = list(sig.official_domains)
        listed = [
            VendorSource(url=s.source_url, source_type=s.source_type, last_checked_at=s.last_checked_at)
            for s in (sources.get(row.id, []) if row is not None else [])
        ]
        known = {s.url for s in listed}
        for url in sig.changelog_urls if sig is not None else []:
            if url not in known:
                listed.append(VendorSource(url=url, source_type="changelog", last_checked_at=None))
        out.append(
            VendorOut(
                name=name,
                category=(row.category if row is not None else None) or (sig.category if sig else None),
                website=(row.website if row is not None else None) or _website(sig),
                monitoring_status=api_status(row.monitoring_status if row is not None else None),  # type: ignore[arg-type]
                official_domains=domains,
                sources=listed,
            )
        )
    return out


def _website(sig: VendorSignature | None) -> str | None:
    return f"https://{sig.official_domains[0]}" if sig is not None and sig.official_domains else None


def known_vendor(session: Session, name: str, signature: VendorSignature | None) -> bool:
    if signature is not None:
        return True
    return session.scalar(select(Vendor.id).where(Vendor.name == name)) is not None


def set_monitoring(
    session: Session, name: str, status: str, schedule_id: str | None, signature: VendorSignature | None
) -> None:
    """Start/pause monitoring through the SECURITY DEFINER function (works on the RLS app role)."""
    session.execute(
        text(
            "SELECT vendorwake_set_vendor_monitoring(:name, :status, :schedule, :category, :website, "
            "CAST(:domains AS jsonb))"
        ),
        {
            "name": name,
            "status": status,
            "schedule": schedule_id,
            "category": signature.category if signature else None,
            "website": _website(signature),
            "domains": json.dumps(list(signature.official_domains) if signature else []),
        },
    )


def monitoring_statuses(session: Session) -> dict[str, str]:
    return {name: status for name, status in session.execute(select(Vendor.name, Vendor.monitoring_status))}


# ---------------------------------------------------------------------------------------------- worker
def ensure_vendor(wsession: Session, name: str, signature: VendorSignature | None) -> uuid.UUID:
    """Upsert the vendor row (worker role). Never changes monitoring_status."""
    return wsession.execute(
        text(
            "INSERT INTO vendors (name, category, website, official_domains) "
            "VALUES (:n, :c, :w, CAST(:d AS jsonb)) "
            "ON CONFLICT (name) DO UPDATE SET "
            "category = COALESCE(vendors.category, EXCLUDED.category), "
            "website = COALESCE(vendors.website, EXCLUDED.website), "
            "official_domains = CASE WHEN vendors.official_domains = '[]'::jsonb "
            "THEN EXCLUDED.official_domains ELSE vendors.official_domains END "
            "RETURNING id"
        ),
        {
            "n": name,
            "c": signature.category if signature else None,
            "w": _website(signature),
            "d": json.dumps(list(signature.official_domains) if signature else []),
        },
    ).scalar_one()


def upsert_source(
    wsession: Session,
    vendor_id: uuid.UUID,
    url: str,
    source_type: str,
    checked_at: datetime,
    content_hash: str | None,
) -> uuid.UUID:
    return wsession.execute(
        text(
            "INSERT INTO monitoring_sources (vendor_id, source_url, source_type, last_checked_at, "
            "last_content_hash) VALUES (:v, :u, :t, :at, :h) "
            "ON CONFLICT (vendor_id, source_url) DO UPDATE SET last_checked_at = EXCLUDED.last_checked_at, "
            "last_content_hash = COALESCE(EXCLUDED.last_content_hash, monitoring_sources.last_content_hash), "
            "updated_at = now() RETURNING id"
        ),
        {"v": vendor_id, "u": url, "t": source_type, "at": checked_at, "h": content_hash},
    ).scalar_one()
