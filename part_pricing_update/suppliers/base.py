"""Common types for the supplier API clients.

This module (and the whole 'suppliers' package) must not import Django,
so the clients can be tested outside of an InvenTree instance.
"""

import html
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import requests


class SupplierError(Exception):
    """Generic error returned by a supplier API."""


class NotFoundError(SupplierError):
    """The requested SKU does not exist in the supplier catalog."""


class RateLimitError(SupplierError):
    """The API quota has been exhausted: stop querying this supplier for now.

    Attributes:
        daily: True if the daily quota is exhausted (wait until the next day)
        retry_after: seconds to wait, when reported by the API
    """

    def __init__(self, message: str, daily: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.daily = daily
        self.retry_after = retry_after


class TemporaryError(SupplierError):
    """Server or network problem (HTTP 5xx, timeout): not an error of the product.

    The SupplierPart is checked again on the next run, and the supplier is not
    queried any more in the current run.
    """


class AuthError(SupplierError):
    """The credentials were rejected: stop querying this supplier for now."""


class QuotaExceededError(RateLimitError):
    """The daily request limit configured in the plugin has been reached.

    Raised before sending the request, by the hook installed with
    part_pricing_update.api.get_client().
    """


@dataclass
class PriceBreak:
    """Unit price valid from a given quantity."""

    quantity: int
    price: Decimal


@dataclass
class SupplierQuote:
    """Pricing data returned by a supplier for a single SKU."""

    sku: str
    currency: str
    price_breaks: list[PriceBreak] = field(default_factory=list)
    stock: int | None = None
    url: str | None = None
    #: Lifecycle status reported by the supplier (e.g. 'Active', 'Obsolete')
    status: str | None = None
    #: Replacement products suggested by the supplier, see alternative()
    alternatives: list[dict] = field(default_factory=list)


def alternative(
    type: str | None = '',
    mpn: str | None = '',
    manufacturer: str | None = '',
    sku: str | None = '',
    description: str | None = '',
    url: str | None = '',
    unit_price: str | None = '',
    stock: int | None = None,
) -> dict:
    """Replacement product, as stored in PricingStatus.alternatives (JSON)."""
    return {
        'type': type or '',
        'mpn': mpn or '',
        'manufacturer': manufacturer or '',
        'sku': sku or '',
        'description': description or '',
        'url': url or '',
        'unit_price': str(unit_price or ''),
        'stock': stock,
    }


@dataclass
class SearchResult:
    """A product found by a keyword / MPN search (one supplier packaging)."""

    #: Empty when the supplier lists the product but does not sell it (Mouser 'N/A')
    sku: str
    mpn: str = ''
    manufacturer: str = ''
    description: str = ''
    #: Packaging of this SKU (e.g. 'Cut Tape', 'Tape & Reel')
    packaging: str = ''
    image_url: str = ''
    product_url: str = ''
    status: str = ''
    stock: int | None = None
    #: Unit price at quantity 1, as text (e.g. '0.52')
    unit_price: str = ''
    currency: str = ''
    #: Other names of the product (old / renamed MPNs, other SKUs), e.g. DigiKey 'OtherNames'
    aliases: list[str] = field(default_factory=list)


@dataclass
class ProductDetails:
    """All the product data needed to create a part in InvenTree."""

    supplier: str
    sku: str
    mpn: str = ''
    manufacturer: str = ''
    description: str = ''
    detailed_description: str = ''
    #: Supplier category path, from the most generic (e.g. ['Capacitors', 'Ceramic Capacitors'])
    category: list[str] = field(default_factory=list)
    #: Every product attribute reported by the supplier, by supplier name (in order)
    parameters: dict[str, str] = field(default_factory=dict)
    packaging: str = ''
    datasheet_url: str = ''
    image_url: str = ''
    product_url: str = ''
    #: RoHS / REACH status, with the same values for every supplier (see normalize_rohs / normalize_reach)
    rohs: str = ''
    reach: str = ''
    #: Prices, stock and lifecycle status, as used for the price updates
    quote: SupplierQuote | None = None
    #: Other names of the product (old / renamed MPNs, other SKUs), e.g. DigiKey 'OtherNames'
    aliases: list[str] = field(default_factory=list)


# Values used in InvenTree for the 'Stato RoHS' and 'REACH' parameters
# (Italian values, the same for every supplier: they are saved as part parameters by the import plugin)
ROHS3 = 'Conforme (RoHS3)'
ROHS = 'Conforme'
ROHS_EXEMPTION = 'Conforme (con esenzione)'
NOT_ROHS = 'Non conforme'
NOT_APPLICABLE = 'Non applicabile'
REACH_UNAFFECTED = 'Conforme (nessuna SVHC)'
REACH_AFFECTED = 'Contiene SVHC'

_UNKNOWN = ('', 'unknown', 'n/a', 'vendor undefined', 'undefined')


def normalize_rohs(value, phthalates=None) -> str:
    """Convert the RoHS status of any supplier to the value used in InvenTree.

    Arguments:
        value: supplier text ('ROHS3 Compliant', 'RoHS Compliant By Exemption', 'YES', 'NO'...)
        phthalates: True if the supplier says the 4 phthalates of RoHS3
            (directive 2015/863) are compliant too
    """
    text = str(value or '').strip().lower()

    if text in _UNKNOWN:
        return ''

    if 'not applicable' in text:
        return NOT_APPLICABLE

    if text.startswith(('not', 'non')) or text in ('no', 'false', 'n') or 'non-compliant' in text or 'non compliant' in text:
        return NOT_ROHS

    if 'exempt' in text:
        return ROHS_EXEMPTION

    if 'rohs3' in text or 'rohs 3' in text or phthalates:
        return ROHS3

    if 'compliant' in text or text in ('yes', 'true', 'y', 'rohs'):
        return ROHS

    return str(value).strip()


def normalize_reach(value) -> str:
    """Convert the REACH status of any supplier to the value used in InvenTree.

    DigiKey reports 'REACH Unaffected' / 'REACH Affected'; Farnell reports the
    SVHC content ('No SVHC (25-Jun-2025)' or the substances found).
    """
    text = str(value or '').strip()
    lower = text.lower()

    if lower in _UNKNOWN or 'advised' in lower:
        return ''

    if 'not applicable' in lower:
        return NOT_APPLICABLE

    if 'unaffected' in lower:
        return REACH_UNAFFECTED

    if lower.startswith('no svhc'):
        # Keep the date of the SVHC list used for the check
        date = text[text.find('(') + 1:text.rfind(')')].strip() if '(' in text else ''
        return f'{REACH_UNAFFECTED[:-1]} al {date})' if date else REACH_UNAFFECTED

    if 'affected' in lower:
        return REACH_AFFECTED

    # Any other SVHC text lists the substances found
    return f'{REACH_AFFECTED}: {text}'[:100]


def add_parameter(parameters: dict[str, str], name, value):
    """Add a supplier attribute, joining repeated names ('Packaging': 'Reel, Cut Tape')."""
    name = str(name or '').strip()
    value = str(value if value is not None else '').strip()

    if not name or not value or value in ('-', '--'):
        return

    if name in parameters and value not in parameters[name].split(', '):
        parameters[name] = f'{parameters[name]}, {value}'
    else:
        parameters.setdefault(name, value)


# SKUs used as placeholders when the supplier code is unknown
PLACEHOLDER_SKU_REGEX = r'^\s*(n\s*/?\s*a|none|null|tbd|tba|-+|\?+|\.+|x+)\s*$'


# Lifecycle statuses of products which are (or will soon be) no longer sold.
# Each supplier uses its own values, e.g.:
#   DigiKey: Obsolete, Discontinued at Digi-Key, Last Time Buy, Not For New Designs
#   Mouser:  Obsolete, End of Life, Not Recommended for New Designs
#   Farnell: NO_LONGER_STOCKED, NO_LONGER_MANUFACTURED (vs STOCKED, DIRECT_SHIP)
DISCONTINUED_KEYWORDS = (
    'obsolete', 'discontinued', 'no longer', 'no_longer', 'last time',
    'end of life', 'eol', 'not for new', 'not recommended', 'not available',
    # Mouser 'RestrictionMessage': 'Mouser does not presently sell this product in your region'
    'not sold',
)


# Status of an active product with no price in the packaging of the SKU (sold in another one)
NO_PRICE_PREFIX = 'No price for'


def is_packaging_unavailable(status: str | None) -> bool:
    """True if the product is sold, but not in the packaging of this SKU (e.g. only full reels)."""
    return (status or '').startswith(NO_PRICE_PREFIX)


def is_discontinued(status: str | None) -> bool:
    """Return True if the supplier status says that the product is being discontinued."""
    status = (status or '').lower()
    return any(keyword in status for keyword in DISCONTINUED_KEYWORDS)


def plain_text(text: str | None) -> str:
    """Text of an HTML error page, without tags and entities."""
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))
    return ' '.join(text.split())


def is_placeholder_sku(sku: str | None) -> bool:
    """Return True for empty SKUs and placeholders like 'N/A', '-', 'TBD'."""
    return not sku or re.match(PLACEHOLDER_SKU_REGEX, sku, re.IGNORECASE) is not None


def parse_price(value) -> Decimal | None:
    """Convert a localized price string ('0,123 €', '$1,234.56', '1.234,5') to Decimal.

    When only one separator is present it is treated as the decimal separator,
    since unit prices with 3+ decimals ('0,123') are far more common than
    unit prices above 1000.
    """
    if value is None:
        return None

    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    text = re.sub(r'[^\d.,]', '', str(value))

    if not text or not re.search(r'\d', text):
        return None

    if ',' in text and '.' in text:
        # The right-most separator is the decimal one
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    elif ',' in text:
        if text.count(',') > 1:
            text = text.replace(',', '')
        else:
            text = text.replace(',', '.')
    elif text.count('.') > 1:
        text = text.replace('.', '')

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_int(value) -> int | None:
    """Extract an integer from values like 1234, '1.234', '1,234 In Stock'."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    digits = re.sub(r'\D', '', str(value).split(' ')[0])
    return int(digits) if digits else None


class SupplierClient:
    """Base class for a supplier pricing API client."""

    #: Internal key ('mouser', 'digikey', 'farnell')
    key = ''

    #: Human readable name
    name = ''

    #: Minimum delay between two requests, in seconds
    min_interval = 1.0

    def __init__(self, session: requests.Session | None = None, timeout: float = 20):
        """Create a new client. 'session' can be replaced in tests."""
        self.session = session or requests.Session()
        self.timeout = timeout
        self._last_request = 0.0

        #: Called before each request counted by the API quota
        self.on_request = None

    def is_configured(self) -> bool:
        """Return True if all the required credentials are available."""
        raise NotImplementedError

    def fetch(self, sku: str, mpn: str | None = None) -> SupplierQuote:
        """Return pricing data for the given SKU, respecting the API rate limit.

        Arguments:
            sku: supplier product number
            mpn: manufacturer part number, used by some suppliers when the SKU
                is no longer valid (the returned quote.sku is the new code)
        """
        sku = (sku or '').strip()
        mpn = (mpn or '').strip() or None

        if is_placeholder_sku(sku):
            raise NotFoundError(f'Invalid SKU "{sku}"')

        try:
            return self._fetch(sku, mpn)
        except requests.RequestException as exc:
            raise TemporaryError(f'{self.name}: connection error ({exc})') from exc

    def search(self, term: str, limit: int = 10) -> list[SearchResult]:
        """Search the supplier catalog by keyword (one request)."""
        term = (term or '').strip()

        if not term:
            return []

        try:
            return self._search(term, max(1, min(limit, 50)))
        except requests.RequestException as exc:
            raise TemporaryError(f'{self.name}: connection error ({exc})') from exc

    def details(self, sku: str) -> ProductDetails:
        """Return all the data of a product, prices included (one request)."""
        sku = (sku or '').strip()

        if is_placeholder_sku(sku):
            raise NotFoundError(f'Invalid SKU "{sku}"')

        try:
            return self._details(sku)
        except requests.RequestException as exc:
            raise TemporaryError(f'{self.name}: connection error ({exc})') from exc

    def find_by_mpn(self, mpn: str) -> list[SearchResult]:
        """Return the products with exactly this manufacturer part number."""
        mpn = (mpn or '').strip()

        if not mpn:
            return []

        return [r for r in self.search(mpn, limit=20) if r.sku and r.mpn.upper() == mpn.upper()]

    def _search(self, term: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError

    def _details(self, sku: str) -> ProductDetails:
        raise NotImplementedError

    def _before_request(self):
        """Wait for the minimum interval, then notify the quota counter.

        Subclasses call it before each request to the product endpoint.
        """
        wait = self._last_request + self.min_interval - time.monotonic()

        if wait > 0:
            time.sleep(wait)

        self._last_request = time.monotonic()

        if self.on_request:
            self.on_request()

    def _fetch(self, sku: str, mpn: str | None) -> SupplierQuote:
        raise NotImplementedError

    def _json(self, response: requests.Response) -> dict:
        # Server errors (e.g. the HTML page of the CDN for HTTP 503) are temporary
        if response.status_code >= 500:
            raise TemporaryError(
                f'{self.name}: service temporarily unavailable (HTTP {response.status_code}), checked again on the next run'
            )

        try:
            return response.json()
        except ValueError as exc:
            raise SupplierError(
                f'{self.name}: invalid response (HTTP {response.status_code}): {plain_text(response.text)[:200]}'
            ) from exc

    @staticmethod
    def _sorted_breaks(breaks: list[PriceBreak]) -> list[PriceBreak]:
        """Drop invalid entries and duplicate quantities, sort by quantity."""
        unique = {}

        for pb in breaks:
            if pb.price is not None and pb.quantity and pb.quantity > 0:
                unique.setdefault(pb.quantity, pb)

        return [unique[q] for q in sorted(unique)]
