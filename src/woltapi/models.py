"""Small public summaries for discovery operations."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """A saved delivery reference with limited display details."""

    id: str
    alias: str | None = field(default=None, repr=False)
    label_type: str | None = field(default=None, repr=False)
    address: str | None = field(default=None, repr=False)
    city: str | None = field(default=None, repr=False)
    postcode: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PaymentMethod:
    """An enabled saved-card reference with limited display details."""

    id: str
    type: str
    is_selected: bool
    is_default: bool
    title: str | None = field(default=None, repr=False)
    subtitle: str | None = field(default=None, repr=False)


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
