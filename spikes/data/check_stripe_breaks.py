"""M0 spike: do the stripe-python 16.0.0 breaking changes reproduce offline (no API key, no network)?

Each probe exercises one changelog item the way fixture code would. Run under stripe 15.x and 16.0.0.
"""

import inspect

import stripe

print("stripe", stripe.VERSION)

# #1915: stripe.Reversal renamed to stripe.TransferReversal
print("has stripe.Reversal:", hasattr(stripe, "Reversal"), "| has stripe.TransferReversal:", hasattr(stripe, "TransferReversal"))

# #1914: StripeObject.request() removed
print("StripeObject.request exists:", hasattr(stripe.StripeObject, "request"))

# #1920: path params positional-only in service methods
client = stripe.StripeClient("sk_test_offline_probe")
svc = client.v1.customers if hasattr(client, "v1") else client.customers
params = inspect.signature(svc.retrieve).parameters
first = next(iter(params.values()))
print("customers.retrieve first param:", first.name, first.kind.name)
try:
    svc.retrieve(customer="cus_offline_probe")
except TypeError as exc:
    print("keyword path param -> TypeError:", exc)
except Exception as exc:  # reaching the network layer means the call signature was accepted
    print("keyword path param accepted (failed later with", type(exc).__name__ + ")")
