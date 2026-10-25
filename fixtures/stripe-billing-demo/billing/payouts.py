"""Marketplace payouts: reversing transfers to connected accounts."""

import stripe


def reverse_transfer(client: stripe.StripeClient, transfer_id: str, amount: int) -> stripe.Reversal:
    """Pull ``amount`` (in the smallest currency unit) back from a connected account."""
    return client.v1.transfers.reversals.create(transfer_id, params={"amount": amount})


def reversed_amount(reversal: stripe.Reversal) -> int:
    return reversal.amount
