"""Fixed service-host definitions for ordering discovery operations."""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping


class ServiceHost(str, Enum):
    """The only hosts to which this library routes ordering credentials."""

    RESTAURANT = "restaurant"
    CONSUMER = "consumer"
    PAYMENT = "payment"


BASE_URLS: Final[Mapping[ServiceHost, str]] = MappingProxyType(
    {
        ServiceHost.RESTAURANT: "https://restaurant-api.wolt.com",
        ServiceHost.CONSUMER: "https://consumer-api.wolt.com",
        ServiceHost.PAYMENT: "https://payment-service.wolt.com",
    }
)
