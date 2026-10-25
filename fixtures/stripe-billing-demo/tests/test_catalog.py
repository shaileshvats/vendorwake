from billing.catalog import active_prices


def test_active_prices(stub_client):
    client, _ = stub_client(
        {
            ("get", "/v1/prices"): {
                "object": "list",
                "url": "/v1/prices",
                "has_more": False,
                "data": [{"id": "price_1", "object": "price", "unit_amount": 1200}],
            }
        }
    )
    assert active_prices(client, "prod_1") == [("price_1", 1200)]
