"""Invoice delivery."""

import stripe


def send_invoice(client: stripe.StripeClient, invoice_id: str) -> stripe.StripeObject:
    """Email a finalized invoice to the customer."""
    invoice = client.v1.invoices.retrieve(invoice_id)
    # Legacy custom call kept from an older integration.
    return invoice.request("post", f"/v1/invoices/{invoice.id}/send")
