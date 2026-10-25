import json

from vendorwake.analysis import build_inventory, parse_manifests, scan_python
from vendorwake.analysis.js_scan import scan_js
from vendorwake.catalog import SIGNATURES, get_signature, is_official_url
from vendorwake.domain.contracts import RepoRef


def test_requirements_pyproject_and_poetry():
    files = {
        "requirements.txt": "# deps\nStripe==15.6.1  # pinned\nrequests>=2,<3\n-r dev.txt\n",
        "svc/pyproject.toml": (
            '[project]\ndependencies = ["openai>=1.40", "twilio==9.3.0"]\n'
            '[project.optional-dependencies]\ndev = ["pytest"]\n'
            '[tool.poetry.dependencies]\npython = "^3.12"\nauth0-python = "4.7.1"\n'
        ),
    }
    deps = {
        (d.package, d.version_spec, d.pinned_version, d.manifest_path, d.line) for d in parse_manifests(files)
    }
    assert ("stripe", "==15.6.1", "15.6.1", "requirements.txt", 2) in deps
    assert ("requests", "<3,>=2", None, "requirements.txt", 3) in deps
    assert ("twilio", "==9.3.0", "9.3.0", "svc/pyproject.toml", 2) in deps
    assert ("auth0-python", "4.7.1", "4.7.1", "svc/pyproject.toml", 7) in deps
    assert not any(p == "python" for p, *_ in deps)


def test_package_json_and_lockfile():
    files = {
        "web/package.json": json.dumps({"dependencies": {"stripe": "^17.1.0", "openai": "4.2.0"}}, indent=1),
        "web/package-lock.json": json.dumps(
            {"lockfileVersion": 3, "packages": {"": {}, "node_modules/stripe": {"version": "17.4.0"}}}
        ),
    }
    deps = {(d.package, d.version_spec, d.pinned_version, d.manifest_path) for d in parse_manifests(files)}
    assert ("stripe", "^17.1.0", None, "web/package.json") in deps
    assert ("openai", "4.2.0", "4.2.0", "web/package.json") in deps
    assert ("stripe", None, "17.4.0", "web/package-lock.json") in deps


def test_python_scan_aliases_annotations_calls_and_keywords():
    src = (
        "import stripe as s\nfrom stripe import Reversal as R\n\n"
        "def f(client: s.StripeClient, rid: str) -> R:\n"
        "    obj = client.v1.customers.retrieve(customer=rid)\n"
        "    obj.request('post', '/v1/x')\n"
        "    return s.Reversal.retrieve(rid)\n"
    )
    sites = scan_python("a.py", src, ["stripe"]).sites
    got = {(x.line, x.kind, x.symbol) for x in sites}
    assert (2, "import", "stripe.Reversal") in got
    assert (4, "attribute", "stripe.Reversal") in got  # alias R resolved
    assert (7, "attribute", "stripe.Reversal") in got
    call = next(x for x in sites if x.kind == "call" and x.method == "retrieve" and x.derived)
    assert call.keywords == ("customer",) and call.chain == ("v1", "customers", "retrieve")
    req = next(x for x in sites if x.method == "request")
    assert req.derived and req.line == 6


def test_python_scan_ignores_non_vendor_and_survives_syntax_errors():
    assert scan_python("b.py", "import requests\nrequests.get('x')\n", ["stripe"]).sites == []
    broken = scan_python("c.py", "import stripe\ndef (:\n", ["stripe"])
    assert broken.parse_error and broken.sites[0].symbol == "stripe"


def test_js_scan():
    src = (
        "import Stripe from 'stripe';\nconst stripe = new Stripe(key);\n"
        'const { OpenAI } = require("openai");\n'
    )
    sites = scan_js("x.ts", src, ["stripe"], "stripe")
    assert sites[0].kind == "import" and sites[0].line == 1
    assert any(s.line == 2 and s.kind == "call" for s in sites)
    assert scan_js("y.js", "const OpenAI = require('openai')\n", ["openai"], "openai")[0].symbol == "openai"


def test_detection_without_package_names():
    files = {
        "pay.go": 'req, _ := http.NewRequest("POST", "https://api.stripe.com/v1/charges", nil)\n',
        ".env.example": "STRIPE_SECRET_KEY=\nAUTH0_DOMAIN=tenant.us.auth0.com\n",
    }
    inv = build_inventory(RepoRef(owner="o", name="n"), files)
    by_vendor = {i.vendor: i for i in inv.integrations}
    assert by_vendor["stripe"].integration_type == "http_api"
    assert {r.symbol for r in by_vendor["stripe"].references} >= {
        "host:api.stripe.com",
        "env:STRIPE_SECRET_KEY",
    }
    assert by_vendor["auth0"].integration_type == "http_api"
    assert "openai" not in by_vendor


def test_fixture_inventory(fixture_files):
    inv = build_inventory(RepoRef(owner="o", name="n"), fixture_files, commit_sha="abc")
    stripe = next(i for i in inv.integrations if i.vendor == "stripe")
    assert (
        stripe.integration_type == "sdk" and stripe.detected_version == "15.6.1" and stripe.confidence >= 0.9
    )
    refs = {(r.file_path, r.line, r.symbol) for r in stripe.references}
    assert ("billing/payouts.py", 6, "stripe.Reversal") in refs
    assert [i.vendor for i in inv.integrations] == ["stripe"]


def test_subpath_scoping(fixture_files):
    files = {f"fixtures/demo/{p}": c for p, c in fixture_files.items()} | {"other/x.py": "import openai\n"}
    inv = build_inventory(RepoRef(owner="o", name="n", subpath="fixtures/demo"), files)
    assert [i.vendor for i in inv.integrations] == ["stripe"]


def test_catalog_official_urls():
    stripe = get_signature("Stripe")
    assert is_official_url(stripe.changelog_urls[0], stripe)
    assert is_official_url("https://docs.stripe.com/changelog", stripe)
    assert not is_official_url("https://github.com/stripe-fan/stripe-python", stripe)
    assert not is_official_url("https://stripe.com.evil.example/x", stripe)
    assert set(SIGNATURES) == {"stripe", "openai", "twilio", "auth0"}
