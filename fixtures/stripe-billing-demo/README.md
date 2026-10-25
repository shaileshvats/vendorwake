# stripe-billing-demo (Vendorwake demo fixture)

This is a **demo fixture**, not a real product. It's a small billing module pinned to `stripe==15.6.1`,
written so that the stripe-python 16.0.0 breaking changes land on specific, known lines:

| File | Uses | 16.0.0 change |
|---|---|---|
| `billing/payouts.py` | `stripe.Reversal` in type annotations | #1915 `stripe.Reversal` renamed to `stripe.TransferReversal` |
| `billing/invoices.py` | `StripeObject.request(...)` on a retrieved invoice | #1914 `StripeObject.request()` removed |
| `billing/customers.py` | `client.v1.customers.retrieve(customer=...)` | #1920 path parameters are now positional-only |
| `billing/catalog.py` | `client.v1.prices.list(params=...)` | none, so it should come out **unaffected** |

The tests run offline: `tests/stub_http.py` plugs an in-memory `stripe.HTTPClient` into `StripeClient`, so
no request ever leaves the process and no API key is needed.

```
pip install -r requirements-dev.txt && pytest -q          # stripe 15.6.1: 4 passed
pip install stripe==16.0.0 && pytest -q --continue-on-collection-errors
# 2 failed, 1 passed, 1 error: TypeError (keyword path param), AttributeError (request), AttributeError (Reversal)
```
