from billing.customers import billing_email


def test_billing_email(stub_client):
    client, http = stub_client(
        {("get", "/v1/customers/cus_123"): {"id": "cus_123", "object": "customer", "email": "ap@example.com"}}
    )
    assert billing_email(client, "cus_123") == "ap@example.com"
    assert http.calls == [("get", "/v1/customers/cus_123")]
