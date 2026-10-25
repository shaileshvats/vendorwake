"""Repositories: API reads/writes and the worker's analysis persistence. Always on a tenant session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vendorwake.core.exceptions import NotFoundError
from vendorwake.db.models import Integration, Repository
from vendorwake.domain.contracts import RepoRef, RepositoryInventory
from vendorwake.services.schemas import RepositoryOut


def canonical_key(owner: str, name: str, ref: str, subpath: str) -> str:
    """Stable identity of an analysis target (GitHub names are case-insensitive; refs and paths are not)."""
    key = f"{owner.lower()}/{name.lower()}@{ref}:{subpath}"
    return key[:255]


def repo_ref(repo: Repository) -> RepoRef:
    owner, _, name = (repo.repository_name or "").partition("/")
    return RepoRef(owner=repo.owner or owner, name=repo.name or name, ref=repo.ref, subpath=repo.subpath)


def html_url(repo: Repository) -> str:
    ref = repo_ref(repo)
    base = f"https://github.com/{ref.owner}/{ref.name}"
    return f"{base}/tree/{ref.ref}/{ref.subpath}" if ref.subpath else base


def to_out(repo: Repository) -> RepositoryOut:
    ref = repo_ref(repo)
    integrations = (repo.inventory or {}).get("integrations") or []
    return RepositoryOut(
        id=repo.id,
        provider=repo.provider,
        owner=ref.owner,
        name=ref.name,
        ref=ref.ref,
        subpath=ref.subpath,
        html_url=html_url(repo),
        analysis_status=repo.analysis_status,  # type: ignore[arg-type]
        last_analyzed_at=repo.last_analyzed_at,
        commit_sha=repo.commit_sha,
        integration_count=len(integrations),
        created_at=repo.created_at,
    )


def create_or_get(
    session: Session, user_id: uuid.UUID, *, owner: str, name: str, ref: str, subpath: str
) -> Repository:
    key = canonical_key(owner, name, ref, subpath)
    row_id = session.execute(
        text(
            "INSERT INTO repositories (user_id, provider, external_repository_id, repository_name, "
            "default_branch, owner, name, ref, subpath) "
            "VALUES (:u, 'github', :k, :rn, :ref, :o, :n, :ref, :sp) "
            "ON CONFLICT (user_id, provider, external_repository_id) DO UPDATE SET updated_at = now() "
            "RETURNING id"
        ),
        {"u": user_id, "k": key, "rn": f"{owner}/{name}", "ref": ref, "o": owner, "n": name, "sp": subpath},
    ).scalar_one()
    session.flush()
    return get(session, row_id)


def list_all(session: Session) -> list[Repository]:
    return list(session.scalars(select(Repository).order_by(Repository.created_at.desc())))


def get(session: Session, repository_id: uuid.UUID) -> Repository:
    repo = session.get(Repository, repository_id, populate_existing=True)
    if repo is None:  # RLS makes another tenant's repository indistinguishable from a missing one
        raise NotFoundError("Repository not found")
    return repo


def inventory(session: Session, repository_id: uuid.UUID) -> RepositoryInventory:
    repo = get(session, repository_id)
    if repo.inventory is None:
        raise NotFoundError("Repository has not been analysed yet", code="not_analyzed")
    return RepositoryInventory.model_validate(repo.inventory)


# ---------------------------------------------------------------------------------------------- worker
def set_status(session: Session, repository_id: uuid.UUID, status: str, error: str | None = None) -> None:
    repo = get(session, repository_id)
    repo.analysis_status = status
    repo.analysis_error = error[:255] if error else None


def save_inventory(session: Session, repository_id: uuid.UUID, inv: RepositoryInventory) -> None:
    repo = get(session, repository_id)
    repo.inventory = inv.model_dump(mode="json")
    repo.commit_sha = inv.commit_sha
    repo.analysis_status = "completed"
    repo.analysis_error = None
    repo.last_analyzed_at = datetime.now(UTC)
    _sync_integrations(session, repo, inv)


def _sync_integrations(session: Session, repo: Repository, inv: RepositoryInventory) -> None:
    """Mirror detected integrations into ``integrations`` for vendors that exist in ``vendors``."""
    names = {i.vendor for i in inv.integrations}
    if not names:
        return
    vendor_ids = dict(
        session.execute(text("SELECT name, id FROM vendors WHERE name = ANY(:n)"), {"n": list(names)}).all()
    )
    now = datetime.now(UTC)
    for finding in inv.integrations:
        vendor_id = vendor_ids.get(finding.vendor)
        if vendor_id is None:
            continue
        packages = finding.packages or []
        package_name = packages[0].package if packages else finding.vendor
        files = sorted({r.file_path for r in finding.references} | {p.manifest_path for p in packages})
        existing = session.scalar(
            select(Integration).where(
                Integration.repository_id == repo.id,
                Integration.vendor_id == vendor_id,
                Integration.package_name == package_name,
            )
        )
        if existing is None:
            existing = Integration(
                user_id=repo.user_id, repository_id=repo.id, vendor_id=vendor_id, package_name=package_name
            )
            session.add(existing)
        existing.detected_version = finding.detected_version
        existing.source_files = files
        existing.confidence = finding.confidence
        existing.last_analyzed_at = now
