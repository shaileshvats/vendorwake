"""Builds the Stripe client used by every billing module."""

import os

import stripe


def make_client(
    api_key: str | None = None, *, http_client: stripe.HTTPClient | None = None
) -> stripe.StripeClient:
    """Return a StripeClient. Tests pass an in-memory ``http_client`` so nothing goes over the network."""
    key = api_key or os.environ["STRIPE_SECRET_KEY"]
    return stripe.StripeClient(key, http_client=http_client, max_network_retries=0)
