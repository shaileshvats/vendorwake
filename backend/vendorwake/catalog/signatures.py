"""Vendor signatures. Only facts we are sure of; changelog URLs were checked to return HTTP 200 (2026-10-25).

``api_hosts`` entries starting with "." are suffix matches (Auth0 tenants: ``<tenant>.auth0.com``).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from vendorwake.domain.contracts import VendorSignature

STRIPE = VendorSignature(
    vendor="stripe",
    category="payments",
    packages={"pypi": ["stripe"], "npm": ["stripe"]},
    import_prefixes=["stripe"],
    api_hosts=["api.stripe.com"],
    env_vars=["STRIPE_SECRET_KEY", "STRIPE_API_KEY"],
    official_domains=["stripe.com", "docs.stripe.com", "github.com/stripe"],
    changelog_urls=["https://raw.githubusercontent.com/stripe/stripe-python/master/CHANGELOG.md"],
)

OPENAI = VendorSignature(
    vendor="openai",
    category="ai",
    packages={"pypi": ["openai"], "npm": ["openai"]},
    import_prefixes=["openai"],
    api_hosts=["api.openai.com"],
    env_vars=["OPENAI_API_KEY"],
    official_domains=["openai.com", "platform.openai.com", "github.com/openai"],
    changelog_urls=["https://raw.githubusercontent.com/openai/openai-python/main/CHANGELOG.md"],
)

TWILIO = VendorSignature(
    vendor="twilio",
    category="communications",
    packages={"pypi": ["twilio"], "npm": ["twilio"]},
    import_prefixes=["twilio"],
    api_hosts=["api.twilio.com"],
    env_vars=["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
    official_domains=["twilio.com", "www.twilio.com", "github.com/twilio"],
    changelog_urls=["https://raw.githubusercontent.com/twilio/twilio-python/main/CHANGES.md"],
)

AUTH0 = VendorSignature(
    vendor="auth0",
    category="identity",
    # The PyPI distribution is auth0-python; it is imported as `auth0`.
    packages={
        "pypi": ["auth0-python"],
        "npm": ["auth0", "@auth0/auth0-react", "@auth0/auth0-spa-js", "@auth0/nextjs-auth0"],
    },
    import_prefixes=["auth0"],
    api_hosts=[".auth0.com"],
    env_vars=["AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET"],
    official_domains=["auth0.com", "github.com/auth0"],
    changelog_urls=["https://raw.githubusercontent.com/auth0/auth0-python/master/CHANGELOG.md"],
)

SIGNATURES: dict[str, VendorSignature] = {s.vendor: s for s in (STRIPE, OPENAI, TWILIO, AUTH0)}


def get_signature(vendor: str) -> VendorSignature:
    try:
        return SIGNATURES[vendor.lower()]
    except KeyError:
        raise KeyError(f"unknown vendor {vendor!r}; known: {sorted(SIGNATURES)}") from None


def is_official_url(url: str, signature: VendorSignature) -> bool:
    """True if ``url`` is on one of the vendor's official domains (``domain`` or ``domain/path-prefix``).

    raw.githubusercontent.com/<org>/... counts as github.com/<org>.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        return False
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == "raw.githubusercontent.com":
        host = "github.com"
    for domain in signature.official_domains:
        dom_host, _, dom_path = domain.lower().partition("/")
        if not (host == dom_host or host.endswith("." + dom_host)):
            continue
        if not dom_path:
            return True
        segments = [s for s in path.split("/") if s]
        if segments and segments[0].lower() == dom_path.strip("/"):
            return True
    return False
