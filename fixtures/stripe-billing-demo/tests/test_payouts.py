from billing.payouts import reverse_transfer, reversed_amount


def test_reverse_transfer(stub_client):
    client, http = stub_client(
        {
            ("post", "/v1/transfers/tr_123/reversals"): {
                "id": "trr_1",
                "object": "transfer_reversal",
                "amount": 500,
                "transfer": "tr_123",
            }
        }
    )
    reversal = reverse_transfer(client, "tr_123", 500)
    assert reversed_amount(reversal) == 500
    assert http.calls == [("post", "/v1/transfers/tr_123/reversals")]
