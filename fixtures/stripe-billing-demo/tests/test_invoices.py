from billing.invoices import send_invoice


def test_send_invoice(stub_client):
    invoice = {"id": "in_123", "object": "invoice", "status": "open"}
    client, http = stub_client(
        {
            ("get", "/v1/invoices/in_123"): invoice,
            ("post", "/v1/invoices/in_123/send"): {**invoice, "status_transitions": {}},
        }
    )
    sent = send_invoice(client, "in_123")
    assert sent.id == "in_123"
    assert http.calls == [("get", "/v1/invoices/in_123"), ("post", "/v1/invoices/in_123/send")]
