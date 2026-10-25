"""HTML to readable text with the standard library only.

Drops script/style/template/svg/noscript content, keeps the ``<title>``, and turns block elements into
line breaks so changelog structure (headings, list items, table rows) survives as lines. The output is
untrusted page text: it may contain prompt-injection attempts and must be treated as data downstream.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = frozenset({"script", "style", "template", "svg", "noscript", "iframe", "object", "canvas", "head"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl", "dt", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th",
        "thead", "tr", "ul",
    }
)  # fmt: skip
_VOID = frozenset(
    {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "wbr"}
)
_SPACES = re.compile(r"[ \t\f\v ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.script_count = 0
        self._skip_depth = 0
        self._in_title = False
        self._pre_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.script_count += 1
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIP and tag not in _VOID:
            self._skip_depth += 1
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _SKIP and tag not in _VOID:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._pre_depth:
            self.parts.append(data)
        else:
            self.parts.append(data.replace("\n", " "))


def normalise_text(text: str) -> str:
    lines = (
        _SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> tuple[str | None, str, int]:
    """Return ``(title, text, script_tag_count)`` for an HTML document."""
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: S110 - HTMLParser is lenient; keep whatever was extracted so far
        pass
    title = normalise_text(" ".join(parser.title_parts)) or None
    return title, normalise_text("".join(parser.parts)), parser.script_count
