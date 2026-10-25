"""Known vendors: how to recognise them in a repository and where their authoritative sources live."""

from vendorwake.catalog.signatures import SIGNATURES, get_signature, is_official_url

__all__ = ["SIGNATURES", "get_signature", "is_official_url"]
