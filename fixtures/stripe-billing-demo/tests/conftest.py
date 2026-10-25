import pytest

from billing.client import make_client
from tests.stub_http import StubHTTPClient


@pytest.fixture
def stub_client():
    def _make(routes):
        http = StubHTTPClient(routes)
        return make_client("sk_test_fixture_offline", http_client=http), http

    return _make
