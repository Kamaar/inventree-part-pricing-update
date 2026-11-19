"""Update SupplierPart pricing from the supplier APIs and record the history."""

import logging
import re
import time
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from . import quota
from .suppliers import (
    AuthError,
    DigiKeyClient,
    FarnellClient,
    MouserClient,
    NotFoundError,
    RateLimitError,
    SupplierClient,
    SupplierError,
    SupplierQuote,
    TemporaryError,
    is_discontinued,
    is_packaging_unavailable,
)
from .suppliers.base import PLACEHOLDER_SKU_REGEX

logger = logging.getLogger('inventree')

PLUGIN_SLUG = 'part-pricing-update'

# Used to find the supplier company when it is not selected in the settings
SUPPLIER_NAME_PATTERNS = {
    'mouser': r'mouser',
    'digikey': r'digi[\s-]?key',
    'farnell': r'farnell|element\s?14|newark',
}


def get_plugin():
    """Return the active plugin instance."""
    from plugin import registry

    return registry.get_plugin(PLUGIN_SLUG)


def get_supplier_companies(plugin) -> dict[str, int | None]:
    """Return the Company pk associated with each supported supplier."""
    from company.models import Company

    result = {}

    for key, pattern in SUPPLIER_NAME_PATTERNS.items():
        pk = None
        value = plugin.get_setting(f'{key.upper()}_SUPPLIER')

        try:
            pk = int(value) if value else None
        except (TypeError, ValueError):
            pk = None

        if pk is None:
            for company in Company.objects.filter(is_supplier=True).only('pk', 'name'):
                if re.search(pattern, company.name, re.IGNORECASE):
                    pk = company.pk
                    break

        result[key] = pk

    return result


def build_clients(plugin) -> dict[str, SupplierClient]:
    """Create the API clients for the suppliers with valid credentials."""
    clients = [
        MouserClient(api_key=plugin.get_setting('MOUSER_API_KEY')),
        DigiKeyClient(
            client_id=plugin.get_setting('DIGIKEY_CLIENT_ID'),
            client_secret=plugin.get_setting('DIGIKEY_CLIENT_SECRET'),
            site=plugin.get_setting('DIGIKEY_SITE'),
            language=plugin.get_setting('DIGIKEY_LANGUAGE'),
            currency=plugin.get_setting('DIGIKEY_CURRENCY'),
        ),
        FarnellClient(
            api_key=plugin.get_setting('FARNELL_API_KEY'),
            store=plugin.get_setting('FARNELL_STORE'),
        ),
    ]

    return {c.key: c for c in clients if c.is_configured()}


def supported_supplier_parts(plugin, companies: dict | None = None):
    """Return the active SupplierParts (with a SKU) of the supported suppliers."""
    from company.models import SupplierPart

    companies = companies or get_supplier_companies(plugin)
    pks = [pk for pk in companies.values() if pk]

    return (
        SupplierPart.objects.filter(supplier__pk__in=pks, active=True)
        .exclude(SKU__isnull=True)
        .exclude(SKU='')
        .exclude(SKU__iregex=PLACEHOLDER_SKU_REGEX)
        .select_related('supplier', 'part')
    )


def _breaks_as_list(quote: SupplierQuote) -> list:
    return [[pb.quantity, str(pb.price)] for pb in quote.price_breaks]


def _same_breaks(a: list, b: list) -> bool:
    """Compare two [[qty, price], ...] lists numerically."""
    if len(a) != len(b):
        return False

    return all(
        int(qa) == int(qb) and Decimal(str(pa)) == Decimal(str(pb))
        for (qa, pa), (qb, pb) in zip(a, b)
    )


def record_history(supplier_part, quote: SupplierQuote, now=None):
    """Store the quote in the price history, extending the last record if prices did not change."""
    from .models import PriceRecord

    now = now or timezone.now()
    breaks = _breaks_as_list(quote)
    last = PriceRecord.objects.filter(supplier_part=supplier_part).order_by('-first_seen').first()

    if last and last.currency == quote.currency and _same_breaks(last.price_breaks, breaks):
        last.last_seen = now
        last.stock = quote.stock
        last.save(update_fields=['last_seen', 'stock'])
        return last

    return PriceRecord.objects.create(
        supplier_part=supplier_part,
        currency=quote.currency,
        first_seen=now,
        last_seen=now,
        unit_price=quote.price_breaks[0].price if quote.price_breaks else None,
        price_breaks=breaks,
        stock=quote.stock,
    )


def record_no_prices(supplier_part, now=None):
    """Record in the history that the supplier gives no prices any more (e.g. product not sold).

    An empty record (no price breaks) starts the period without prices, shown as a gap in the
    chart; the next checks extend it, until the prices come back (record_history). Nothing is
    recorded for a product which never had prices: there is no line to interrupt.
    """
    from .models import PriceRecord

    now = now or timezone.now()
    last = PriceRecord.objects.filter(supplier_part=supplier_part).order_by('-first_seen').first()

    if last is None:
        return None

    if not last.price_breaks:
        last.last_seen = now
        last.save(update_fields=['last_seen'])
        return last

    return PriceRecord.objects.create(
        supplier_part=supplier_part,
        currency=last.currency,
        # After the last prices, also when they were saved in this same check (clear_price_breaks)
        first_seen=max(now, last.first_seen + timedelta(microseconds=1)),
        last_seen=max(now, last.first_seen + timedelta(microseconds=1)),
        unit_price=None,
        price_breaks=[],
        stock=0,
    )


def clear_price_breaks(supplier_part, now=None) -> bool:
    """Remove the price breaks of a product no longer sold. Return True if removed.

    The current prices are saved in the history first (if not there yet),
    so that also prices entered by hand are not lost.
    """
    from .models import PriceRecord

    now = now or timezone.now()
    breaks = [pb for pb in supplier_part.pricebreaks.order_by('quantity') if pb.price is not None]

    if not breaks:
        return False

    currency = str(breaks[0].price.currency)
    current = [[int(pb.quantity), str(pb.price.amount)] for pb in breaks]
    last = PriceRecord.objects.filter(supplier_part=supplier_part).order_by('-first_seen').first()

    if not (last and last.currency == currency and _same_breaks(last.price_breaks, current)):
        PriceRecord.objects.create(
            supplier_part=supplier_part,
            currency=currency,
            first_seen=now,
            last_seen=now,
            unit_price=breaks[0].price.amount,
            price_breaks=current,
            stock=None,
        )

    supplier_part.pricebreaks.all().delete()
    return True


def apply_price_breaks(supplier_part, quote: SupplierQuote) -> bool:
    """Replace the SupplierPriceBreak entries if they differ from the quote. Return True if changed."""
    from djmoney.money import Money

    from company.models import SupplierPriceBreak

    current = [
        [int(pb.quantity), str(pb.price.amount), str(pb.price.currency)]
        for pb in supplier_part.pricebreaks.order_by('quantity')
        if pb.price is not None
    ]

    if [c[2] for c in current] == [quote.currency] * len(current) and _same_breaks(
        [c[:2] for c in current], _breaks_as_list(quote)
    ):
        return False

    supplier_part.pricebreaks.all().delete()

    for pb in quote.price_breaks:
        SupplierPriceBreak.objects.create(
            part=supplier_part,
            quantity=pb.quantity,
            price=Money(pb.price, quote.currency),
        )

    return True


def max_available() -> int:
    """Largest quantity the SupplierPart 'available' field can store (9,999,999 in InvenTree 1.x)."""
    from company.models import SupplierPart

    field = SupplierPart._meta.get_field('available')
    return 10 ** (field.max_digits - field.decimal_places) - 1


def save_prices(plugin, supplier_part, quote: SupplierQuote, now=None) -> bool:
    """Save price breaks, availability and history of a quote. Return True if the prices changed."""
    now = now or timezone.now()

    with transaction.atomic():
        changed = False

        if plugin.get_setting('UPDATE_PRICE_BREAKS'):
            changed = apply_price_breaks(supplier_part, quote)

        if plugin.get_setting('UPDATE_AVAILABILITY') and quote.stock is not None:
            # Passive parts can have tens of millions in stock (e.g. Mouser 0402 resistors):
            # more than the field can store. The real value stays in the price history.
            supplier_part.update_available_quantity(min(quote.stock, max_available()))

        record_history(supplier_part, quote, now)

    return changed


def update_supplier_part(plugin, supplier_part, client: SupplierClient) -> dict:
    """Fetch the prices for a single SupplierPart and save them.

    RateLimitError and AuthError are re-raised, so the caller can stop
    querying that supplier.
    """
    from .models import PricingStatus

    now = timezone.now()
    status, _ = PricingStatus.objects.get_or_create(supplier_part=supplier_part)
    previous_check = status.last_checked
    status.last_checked = now
    result = {'pk': supplier_part.pk, 'sku': supplier_part.SKU, 'supplier': client.name}

    mpn = supplier_part.manufacturer_part.MPN if supplier_part.manufacturer_part else None

    try:
        quote = client.fetch(supplier_part.SKU, mpn)

        # Product status, replacement products and new supplier code are kept
        # even when there are no prices: they explain why
        status.supplier_status = quote.status or ''
        status.alternatives = quote.alternatives
        status.suggested_sku = (
            quote.sku if quote.sku and quote.sku.upper() != supplier_part.SKU.upper() else ''
        )

        if not quote.price_breaks and is_packaging_unavailable(quote.status):
            # Active product, sold only in another packaging (e.g. full reels): not an error, and the
            # current prices are kept (they come back when the supplier stocks this packaging again)
            status.last_error = ''
            status.save()
            result.update({
                'success': False,
                'no_price': True,
                'status': quote.status,
                'alternatives': len(quote.alternatives),
            })
            return result

        if not quote.price_breaks:
            details = [f'status: {quote.status}'] if quote.status else []

            if quote.alternatives:
                details.append(f'{len(quote.alternatives)} alternatives')

            # A product declared discontinued is a status, not an error. A plain
            # 'no prices' may also be a wrong SKU or a temporary problem of the API
            if not is_discontinued(quote.status):
                details = f' ({", ".join(details)})' if details else ''
                raise NotFoundError(f'{client.name}: no price breaks for {supplier_part.SKU}{details}')

            removed = False

            with transaction.atomic():
                if plugin.get_setting('CLEAR_UNAVAILABLE_PRICES'):
                    removed = clear_price_breaks(supplier_part, now)

                    if plugin.get_setting('UPDATE_AVAILABILITY'):
                        supplier_part.update_available_quantity(0)

                # The period without prices is a gap in the price history
                record_no_prices(supplier_part, now)

            status.last_error = ''
            status.save()
            result.update({
                'success': False,
                'discontinued': True,
                'status': quote.status,
                'alternatives': len(quote.alternatives),
                'prices_removed': removed,
            })
            return result

        if not quote.currency:
            raise SupplierError(f'{client.name}: currency not reported for {supplier_part.SKU}')

        changed = save_prices(plugin, supplier_part, quote, now)

        status.last_success = now
        status.last_error = ''
        result.update({
            'success': True,
            'changed': changed,
            'currency': quote.currency,
            'unit_price': str(quote.price_breaks[0].price),
            'stock': quote.stock,
        })
    except (RateLimitError, AuthError) as exc:
        status.last_error = str(exc)
        status.save()
        raise
    except TemporaryError as exc:
        # Not a problem of the product: the last check stays as it was, so that the
        # SupplierPart is still due and is checked again on the next run
        status.last_checked = previous_check
        status.last_error = str(exc)
        status.save()
        raise
    except SupplierError as exc:
        status.last_error = str(exc)
        result.update({'success': False, 'error': str(exc)})
    except Exception as exc:  # pragma: no cover - unexpected errors are logged, not fatal
        logger.exception('PartPricingUpdate: error updating %s', supplier_part)
        status.last_error = f'Internal error: {exc}'
        result.update({'success': False, 'error': status.last_error})

    status.save()
    return result


# --------------------------------------------------------------------------
# API quota


def daily_limit(plugin, key: str) -> int:
    """Daily request limit configured for a supplier."""
    try:
        return max(int(plugin.get_setting(f'{key.upper()}_DAILY_LIMIT')), 0)
    except (TypeError, ValueError):
        return quota.OFFICIAL_LIMITS[key]['per_day']


def get_usage(key: str, now=None):
    """Request counter of the current UTC day for a supplier."""
    from .models import SupplierApiUsage

    now = now or timezone.now()
    usage, _ = SupplierApiUsage.objects.get_or_create(
        supplier=key, day=now.astimezone(dt_timezone.utc).date()
    )
    return usage


def blocked_until(key: str, now=None):
    """Return the end of the block requested by the API, if still active."""
    from .models import SupplierApiUsage

    now = now or timezone.now()
    usage = (
        SupplierApiUsage.objects.filter(supplier=key, blocked_until__gt=now)
        .order_by('-blocked_until')
        .first()
    )
    return usage.blocked_until if usage else None


def remaining_requests(plugin, key: str, now=None) -> int:
    """Requests that can still be sent today to a supplier (0 if blocked)."""
    now = now or timezone.now()

    if blocked_until(key, now):
        return 0

    return max(daily_limit(plugin, key) - get_usage(key, now).requests, 0)


def count_request(key: str):
    """Add one request to the counter of the current day."""
    from .models import SupplierApiUsage

    usage = get_usage(key)
    SupplierApiUsage.objects.filter(pk=usage.pk).update(requests=F('requests') + 1)


def block_supplier(key: str, exc: RateLimitError):
    """Stop the requests to a supplier after it reported a rate limit error."""
    now = timezone.now()

    if exc.daily:
        until = quota.next_utc_midnight(now)
    else:
        until = now + timedelta(seconds=exc.retry_after or 60)

    usage = get_usage(key, now)
    usage.blocked_until = until
    usage.save(update_fields=['blocked_until'])
    logger.warning('PartPricingUpdate: %s - %s paused until %s', exc, key, until)


def _request_counter(key: str, remaining: dict):
    """Hook called by the client before each request (retries included)."""

    def counter():
        count_request(key)
        remaining[key] -= 1

    return counter


# --------------------------------------------------------------------------
# Update


def update_supplier_parts(plugin, supplier_parts, time_budget: float | None = None) -> dict:
    """Update a list of SupplierParts, within the daily API limits.

    Arguments:
        time_budget: stop after this many seconds, so that a background task
            does not exceed the worker timeout. The remaining parts are left
            for the next run.
    """
    companies = get_supplier_companies(plugin)
    key_by_company = {pk: key for key, pk in companies.items() if pk}
    clients = build_clients(plugin)
    deadline = time.monotonic() + time_budget if time_budget else None
    remaining = {}
    stopped = {}
    results = []

    for sp in supplier_parts:
        if deadline and time.monotonic() > deadline:
            break

        key = key_by_company.get(sp.supplier_id)

        if not key:
            continue

        if key not in clients:
            results.append({'pk': sp.pk, 'sku': sp.SKU, 'success': False,
                            'error': f'{key}: API credentials not configured'})
            continue

        if key not in remaining:
            remaining[key] = remaining_requests(plugin, key)
            clients[key].on_request = _request_counter(key, remaining)

        if key not in stopped and remaining[key] <= 0:
            until = blocked_until(key)
            stopped[key] = (
                f'{clients[key].name}: paused by the API until {until:%Y-%m-%d %H:%M} UTC'
                if until
                else f'{clients[key].name}: daily request limit ({daily_limit(plugin, key)}) reached'
            )

        if key in stopped:
            results.append({'pk': sp.pk, 'sku': sp.SKU, 'success': False, 'error': stopped[key]})
            continue

        try:
            results.append(update_supplier_part(plugin, sp, clients[key]))
        except RateLimitError as exc:
            block_supplier(key, exc)
            stopped[key] = str(exc)
            results.append({'pk': sp.pk, 'sku': sp.SKU, 'success': False, 'error': str(exc)})
        except AuthError as exc:
            logger.warning('PartPricingUpdate: %s', exc)
            stopped[key] = str(exc)
            results.append({'pk': sp.pk, 'sku': sp.SKU, 'success': False, 'error': str(exc)})
        except TemporaryError as exc:
            # Service down: the other parts of this supplier wait for the next run
            logger.warning('PartPricingUpdate: %s', exc)
            stopped[key] = str(exc)
            results.append({'pk': sp.pk, 'sku': sp.SKU, 'success': False, 'error': str(exc)})

    return {
        'updated': sum(1 for r in results if r.get('success')),
        'discontinued': sum(1 for r in results if r.get('discontinued')),
        'no_price': sum(1 for r in results if r.get('no_price')),
        'failed': sum(
            1 for r in results if not r.get('success') and not r.get('discontinued') and not r.get('no_price')
        ),
        'results': results,
    }


def interleave_by_supplier(supplier_parts) -> list:
    """Alternate the suppliers, so that the delays between requests overlap."""
    groups = {}

    for sp in supplier_parts:
        groups.setdefault(sp.supplier_id, []).append(sp)

    return quota.interleave(list(groups.values()))


def due_supplier_parts(plugin):
    """Return the SupplierParts not checked within the configured interval, oldest first."""
    from .models import PricingStatus

    days = max(int(plugin.get_setting('UPDATE_INTERVAL_DAYS') or 7), 1)
    threshold = timezone.now() - timedelta(days=days)

    # Query through PricingStatus instead of the reverse relation 'pricing_status':
    # InvenTree loads the plugin apps after the core models, and the reverse
    # relations are not always available to the SupplierPart lookups
    last_checked = PricingStatus.objects.filter(supplier_part=OuterRef('pk')).values('last_checked')[:1]

    return (
        supported_supplier_parts(plugin)
        .annotate(ppu_last_checked=Subquery(last_checked))
        .filter(Q(ppu_last_checked__isnull=True) | Q(ppu_last_checked__lt=threshold))
        .order_by(F('ppu_last_checked').asc(nulls_first=True), 'pk')
    )


def update_batch(plugin=None, interval_minutes: int | None = None) -> dict:
    """Update the SupplierParts which are due, within the API limits.

    For each supplier the requests left today are spread over the scheduled
    runs left in the day, capped by the PARTS_PER_RUN setting.
    """
    plugin = plugin or get_plugin()

    if plugin is None:
        return {'updated': 0, 'discontinued': 0, 'failed': 0, 'results': []}

    if interval_minutes is None:
        interval_minutes = plugin.SCHEDULED_TASKS['update_prices']['minutes']

    now = timezone.now()
    cap = max(int(plugin.get_setting('PARTS_PER_RUN') or 10), 1)
    time_budget = max(int(plugin.get_setting('MAX_RUN_SECONDS') or 60), 10)
    companies = get_supplier_companies(plugin)
    clients = build_clients(plugin)
    due = due_supplier_parts(plugin)
    groups = []

    for key, company_pk in companies.items():
        if not company_pk or key not in clients:
            continue

        allowance = quota.run_allowance(remaining_requests(plugin, key, now), now, interval_minutes, cap)

        if allowance > 0:
            groups.append(list(due.filter(supplier__pk=company_pk)[:allowance]))

    summary = update_supplier_parts(plugin, quota.interleave(groups), time_budget)

    if summary['results']:
        logger.info(
            'PartPricingUpdate: %s supplier parts updated, %s discontinued, %s failed',
            summary['updated'],
            summary['discontinued'],
            summary['failed'],
        )

    return summary


def update_all():
    """Mark every supported SupplierPart as due, then start the first batch.

    The following batches are processed by the scheduled task, so the API
    daily limits and the worker timeout are respected.
    """
    from .models import PricingStatus

    plugin = get_plugin()

    if plugin is None:
        return

    PricingStatus.objects.filter(
        supplier_part__in=supported_supplier_parts(plugin)
    ).update(last_checked=None)

    update_batch(plugin)
