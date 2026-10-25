"""Deterministic grounding: every quote and symbol the model reports must literally appear in the source."""

from __future__ import annotations

from vendorwake.domain.contracts import ChangeExtraction, ExtractedChange, GroundingReport

_SYMBOL_STRIP = "`'\" "


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def clean_symbol(symbol: str) -> str:
    return symbol.strip().strip(_SYMBOL_STRIP)


def quote_is_grounded(quote: str, source: str) -> bool:
    quote_n = normalize_whitespace(quote)
    return bool(quote_n) and quote_n in normalize_whitespace(source)


def symbol_is_grounded(symbol: str, source: str) -> bool:
    cleaned = clean_symbol(symbol)
    return bool(cleaned) and normalize_whitespace(cleaned) in normalize_whitespace(source)


def ungrounded_parts(change: ExtractedChange, source: str) -> tuple[bool, list[str]]:
    """(quote grounded?, ungrounded symbols) for one change."""
    return (
        quote_is_grounded(change.evidence_quote, source),
        [s for s in change.affected_symbols if not symbol_is_grounded(s, source)],
    )


def check_grounding(extraction: ChangeExtraction, source: str) -> GroundingReport:
    quotes: list[str] = []
    symbols: set[str] = set()
    for change in extraction.changes:
        quote_ok, bad_symbols = ungrounded_parts(change, source)
        if not quote_ok:
            quotes.append(change.evidence_quote)
        symbols.update(bad_symbols)
    return GroundingReport(ungrounded_quotes=quotes, ungrounded_symbols=sorted(symbols))
