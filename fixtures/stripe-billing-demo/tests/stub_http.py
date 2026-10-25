"""In-memory stand-in for Stripe's HTTP layer: canned JSON per (method, path), no network access."""

import json
from urllib.parse import urlsplit

import stripe


class StubHTTPClient(stripe.HTTPClient):
    name = "stub"

    def __init__(self, routes: dict[tuple[str, str], dict]) -> None:
        super().__init__()
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, headers, post_data=None, *, _usage=None):
        key = (method.lower(), urlsplit(url).path)
        self.calls.append(key)
        if key not in self.routes:
            raise AssertionError(f"unexpected Stripe request {key}")
        return json.dumps(self.routes[key]), 200, {"request-id": "req_stub"}

    def request_stream(self, method, url, headers, post_data=None, *, _usage=None):
        raise NotImplementedError

    def close(self):
        pass
