"""Small value types shared by pipeline steps."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SourceRef(BaseModel):
    """Where a change came from: enough to cite it (URL + content hash), without the full text."""

    url: str
    content_sha256: str
    title: str | None = None


def source_ref(source: Any) -> SourceRef:
    """Accept a SourceDocument, a SourceRef, or a mapping with ``url`` and ``content_sha256``."""
    if isinstance(source, SourceRef):
        return source
    if isinstance(source, dict):
        return SourceRef(
            url=source["url"], content_sha256=source["content_sha256"], title=source.get("title")
        )
    return SourceRef(
        url=source.url, content_sha256=source.content_sha256, title=getattr(source, "title", None)
    )
