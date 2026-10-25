"""Price catalog. Uses Stripe, but none of the APIs changed in stripe-python 16.0.0."""

import stripe


def active_prices(client: stripe.StripeClient, product_id: str) -> list[tuple[str, int]]:
    prices = client.v1.prices.list(params={"product": product_id, "active": True})
    return [(price.id, price.unit_amount) for price in prices.data]
