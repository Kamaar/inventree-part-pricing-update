"""Public API of the PartPricingUpdate plugin, for other plugins.

Other plugins (e.g. inventree-supplier-part-import) must use only this
module: the rest of the package may change between versions.

    from part_pricing_update import api

    if api.is_available():
        client = api.get_client('digikey')   # requests counted in the daily quota
        details = client.details('296-NE555P-ND')
        ...
        api.apply_quote(supplier_part, details.quote)

API_VERSION is increased when this module changes in a non compatible way.
"""

from django.utils import timezone

from . import pricing
from .suppliers import (
    AuthError,
    NotFoundError,
    ProductDetails,
    QuotaExceededError,
    RateLimitError,
    SearchResult,
    SupplierClient,
    SupplierError,
    SupplierQuote,
    is_discontinued,
)

API_VERSION = 1

SUPPLIER_NAMES = {'mouser': 'Mouser', 'digikey': 'DigiKey', 'farnell': 'Farnell'}

__all__ = [
    'API_VERSION',
    'SUPPLIER_NAMES',
    'AuthError',
    'NotFoundError',
    'ProductDetails',
    'QuotaExceededError',
    'RateLimitError',
    'SearchResult',
    'SupplierClient',
    'SupplierError',
    'SupplierQuote',
    'apply_quote',
    'configured_suppliers',
    'get_client',
    'get_plugin',
    'is_available',
    'is_discontinued',
    'remaining_requests',
    'supplier_company',
    'supplier_key_for_company',
]


def get_plugin():
    """Return the PartPricingUpdate plugin if it is installed and active, otherwise None."""
    plugin = pricing.get_plugin()

    if plugin is None or not plugin.is_active():
        return None

    return plugin


def is_available() -> bool:
    """True if the PartPricingUpdate plugin is active."""
    return get_plugin() is not None


def _plugin():
    plugin = get_plugin()

    if plugin is None:
        raise SupplierError('The Part Pricing Update plugin is not active')

    return plugin


def configured_suppliers() -> list[str]:
    """Keys ('mouser', 'digikey', 'farnell') of the suppliers with API credentials."""
    return sorted(pricing.build_clients(_plugin()).keys())


def supplier_company(key: str):
    """The InvenTree Company linked to a supplier key, or None."""
    from company.models import Company

    pk = pricing.get_supplier_companies(_plugin()).get(key)
    return Company.objects.filter(pk=pk).first() if pk else None


def supplier_key_for_company(company) -> str | None:
    """The supplier key of an InvenTree Company, or None."""
    pk = getattr(company, 'pk', company)

    for key, company_pk in pricing.get_supplier_companies(_plugin()).items():
        if company_pk and company_pk == pk:
            return key

    return None


def remaining_requests(key: str) -> int:
    """Requests that can still be sent today to a supplier."""
    return pricing.remaining_requests(_plugin(), key)


def get_client(key: str) -> SupplierClient:
    """Return an API client whose requests are counted in the daily quota.

    Before each request the quota is checked: when it is exhausted (or the
    supplier asked to pause) QuotaExceededError is raised and nothing is sent.
    """
    plugin = _plugin()
    client = pricing.build_clients(plugin).get(key)

    if client is None:
        raise SupplierError(f'{SUPPLIER_NAMES.get(key, key)}: API credentials not configured')

    def counter():
        if pricing.remaining_requests(plugin, key) <= 0:
            raise QuotaExceededError(
                f'{client.name}: daily request limit ({pricing.daily_limit(plugin, key)}) reached'
            )

        pricing.count_request(key)

    client.on_request = counter
    return client


def apply_quote(supplier_part, quote: SupplierQuote) -> bool:
    """Save prices, availability and history of a quote on a SupplierPart.

    Also marks the SupplierPart as just checked, so the scheduled update does
    not query it again before the configured interval. Return True if the
    price breaks changed.
    """
    from .models import PricingStatus

    plugin = _plugin()
    now = timezone.now()
    changed = pricing.save_prices(plugin, supplier_part, quote, now) if quote.price_breaks else False

    PricingStatus.objects.update_or_create(
        supplier_part=supplier_part,
        defaults={
            'last_checked': now,
            'last_success': now if quote.price_breaks else None,
            'last_error': '',
            'supplier_status': quote.status or '',
            'suggested_sku': '',
            'alternatives': quote.alternatives,
        },
    )

    return changed
