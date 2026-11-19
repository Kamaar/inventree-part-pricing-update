"""Supplier API clients (independent from Django)."""

from .base import (
    AuthError,
    NotFoundError,
    PriceBreak,
    ProductDetails,
    QuotaExceededError,
    RateLimitError,
    SearchResult,
    SupplierClient,
    SupplierError,
    SupplierQuote,
    TemporaryError,
    is_discontinued,
    is_packaging_unavailable,
    is_placeholder_sku,
)
from .digikey import DigiKeyClient
from .farnell import FARNELL_STORES, FarnellClient
from .mouser import MouserClient

__all__ = [
    'FARNELL_STORES',
    'AuthError',
    'DigiKeyClient',
    'FarnellClient',
    'MouserClient',
    'NotFoundError',
    'PriceBreak',
    'ProductDetails',
    'QuotaExceededError',
    'RateLimitError',
    'SearchResult',
    'SupplierClient',
    'SupplierError',
    'SupplierQuote',
    'TemporaryError',
    'is_discontinued',
    'is_packaging_unavailable',
    'is_placeholder_sku',
]
