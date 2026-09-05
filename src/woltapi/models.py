"""Small public summaries for discovery operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    """A venue found in a server-driven search result."""

    id: str
    slug: str
    title: str | None
    currency: str | None
    delivers: bool | None
    online: bool | None


@dataclass(frozen=True)
class DeliveryTarget:
    """An opaque saved delivery reference without address details."""

    id: str


@dataclass(frozen=True)
class PaymentMethod:
    """An enabled saved-card reference without card metadata."""

    id: str
    type: str
    is_selected: bool
    is_default: bool


@dataclass(frozen=True)
class OrderStatus:
    """A minimal operational order state with an unbounded status string."""

    purchase_id: str
    status: str
    currency: str | None
    payment_amount: int | float | None
    total_price: int | float | None
    delivery_price: int | float | None
    delivery_method: str | None
