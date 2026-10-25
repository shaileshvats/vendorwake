"""Customer lookups."""

import stripe


def billing_email(client: stripe.StripeClient, customer_id: str) -> str | None:
    """Email address invoices are sent to for this customer."""
    customer = client.v1.customers.retrieve(customer=customer_id)
    return customer.email
