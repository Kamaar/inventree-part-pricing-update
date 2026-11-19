"""Mouser Search API client.

Ref: https://api.mouser.com/api/docs/ui/index
Limits: 30 requests per minute, 1000 requests per day.
"""

from urllib.parse import quote_plus

import requests

from .base import (
    AuthError,
    NotFoundError,
    PriceBreak,
    ProductDetails,
    RateLimitError,
    SearchResult,
    SupplierClient,
    SupplierError,
    SupplierQuote,
    TemporaryError,
    add_parameter,
    alternative,
    is_placeholder_sku,
    normalize_rohs,
    parse_int,
    parse_price,
)

MOUSER_URL = 'https://api.mouser.com/api/v1/search/partnumber'
MOUSER_KEYWORD_URL = 'https://api.mouser.com/api/v1/search/keyword'


class MouserClient(SupplierClient):
    """Fetch prices using the Mouser 'search by part number' endpoint."""

    key = 'mouser'
    name = 'Mouser'
    min_interval = 2.1

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = (api_key or '').strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _post(self, url: str, body: dict) -> dict:
        """POST a search request, converting the errors."""
        self._before_request()
        response = self.session.post(
            url,
            params={'apiKey': self.api_key},
            json=body,
            headers={'Accept': 'application/json'},
            timeout=self.timeout,
        )

        if response.status_code == 429:
            raise RateLimitError('Mouser: too many requests', retry_after=60)

        data = self._json(response)
        self._check_errors(data)
        return data

    def _part(self, sku: str) -> dict:
        """Return the 'Parts' entry of a Mouser part number."""
        data = self._post(MOUSER_URL, {
            'SearchByPartRequest': {
                'mouserPartNumber': sku,
                'partSearchOptions': 'Exact',
            }
        })
        results = (data.get('SearchResults') or {}).get('Parts') or []

        # Mouser may return similar SKUs even with the 'Exact' option
        matches = [p for p in results if (p.get('MouserPartNumber') or '').upper() == sku.upper()]

        if not matches:
            raise NotFoundError(f'Mouser: SKU {sku} not found')

        return matches[0]

    def _fetch(self, sku: str, mpn: str | None = None) -> SupplierQuote:
        try:
            return self.parse_part(self._part(sku))
        except NotFoundError:
            if not mpn:
                raise

        # SKU not found: the product may have a new Mouser number, or be removed from the catalog
        parts = self._mpn_parts(mpn)

        if parts:
            # The ones with prices first: the new SKU is reported as suggested SKU
            parts.sort(key=lambda p: not p.get('PriceBreaks'))
            return self.parse_part(parts[0])

        # Neither the SKU nor the MPN exist any more (two independent codes: a typo is unlikely)
        return SupplierQuote(sku='', currency='', status='Not available at Mouser')

    def _mpn_parts(self, mpn: str) -> list[dict]:
        """'Parts' entries with exactly this manufacturer part number (one request)."""
        data = self._post(MOUSER_URL, {
            'SearchByPartRequest': {'mouserPartNumber': mpn, 'partSearchOptions': 'Exact'}
        })

        return [
            part
            for part in (data.get('SearchResults') or {}).get('Parts') or []
            if (part.get('ManufacturerPartNumber') or '').upper() == mpn.upper()
            and not is_placeholder_sku(part.get('MouserPartNumber'))
        ]

    def _details(self, sku: str) -> ProductDetails:
        return self.parse_details(self._part(sku))

    def _search(self, term: str, limit: int) -> list[SearchResult]:
        data = self._post(MOUSER_KEYWORD_URL, {
            'SearchByKeywordRequest': {
                'keyword': term,
                'records': limit,
                'startingRecord': 0,
                'searchOptions': 'None',
                'searchWithYourSignUpLanguage': 'en',
            }
        })

        results = []

        for part in (data.get('SearchResults') or {}).get('Parts') or []:
            result = self.parse_result(part)

            # Parts in the catalog but not sold by Mouser have 'N/A' as part number:
            # reported without SKU, so the caller can tell the user
            if is_placeholder_sku(part.get('MouserPartNumber')):
                result.sku = ''
                result.status = 'Not sold by Mouser'

            results.append(result)

        return results

    def find_by_mpn(self, mpn: str) -> list[SearchResult]:
        """The part number search also matches the manufacturer part number."""
        mpn = (mpn or '').strip()

        if not mpn:
            return []

        try:
            parts = self._mpn_parts(mpn)
        except requests.RequestException as exc:
            raise TemporaryError(f'Mouser: connection error ({exc})') from exc

        return [self.parse_result(part) for part in parts]

    @classmethod
    def parse_result(cls, part: dict) -> SearchResult:
        """Convert a 'Parts' entry into a search result."""
        quote = cls.parse_part(part)

        return SearchResult(
            sku=part.get('MouserPartNumber') or '',
            mpn=part.get('ManufacturerPartNumber') or '',
            manufacturer=part.get('Manufacturer') or '',
            description=part.get('Description') or '',
            packaging=cls._packaging(part),
            image_url=part.get('ImagePath') or '',
            product_url=part.get('ProductDetailUrl') or '',
            status=part.get('LifecycleStatus') or '',
            stock=quote.stock,
            unit_price=str(quote.price_breaks[0].price) if quote.price_breaks else '',
            currency=quote.currency,
        )

    @classmethod
    def parse_details(cls, part: dict) -> ProductDetails:
        """Convert a 'Parts' entry into the full product data.

        The Mouser API reports few technical attributes: the importer prefers
        the parameters of another supplier when available.
        """
        parameters = {}

        for attribute in part.get('ProductAttributes') or []:
            add_parameter(parameters, attribute.get('AttributeName'), attribute.get('AttributeValue'))

        for compliance in (part.get('ProductCompliance') or []) + (part.get('TradeCompliance') or []):
            add_parameter(parameters, compliance.get('ComplianceName'), compliance.get('ComplianceValue'))

        # The Search API does not report REACH
        rohs = normalize_rohs(part.get('ROHSStatus'))

        return ProductDetails(
            supplier='mouser',
            sku=part.get('MouserPartNumber') or '',
            mpn=part.get('ManufacturerPartNumber') or '',
            manufacturer=part.get('Manufacturer') or '',
            description=part.get('Description') or '',
            category=[part['Category']] if part.get('Category') else [],
            parameters=parameters,
            packaging=cls._packaging(part),
            datasheet_url=part.get('DataSheetUrl') or '',
            image_url=part.get('ImagePath') or '',
            product_url=part.get('ProductDetailUrl') or '',
            rohs=rohs,
            quote=cls.parse_part(part),
        )

    @staticmethod
    def _packaging(part: dict) -> str:
        names = [
            a.get('AttributeValue') for a in part.get('ProductAttributes') or []
            if a.get('AttributeName') == 'Packaging' and a.get('AttributeValue')
        ]
        return ', '.join(dict.fromkeys(names))

    @staticmethod
    def _check_errors(data: dict):
        if data.get('Message') and not data.get('SearchResults'):
            raise SupplierError(f'Mouser: {data["Message"]}')

        errors = data.get('Errors') or []

        if not errors:
            return

        code = errors[0].get('Code') or ''
        message = errors[0].get('Message') or code

        if code == 'TooManyRequests':
            raise RateLimitError('Mouser: daily request limit reached', daily=True)

        if code in ('Invalid', 'Required') and errors[0].get('PropertyName') == 'API Key':
            raise AuthError('Mouser: invalid API key')

        raise SupplierError(f'Mouser: {message}')

    @classmethod
    def parse_part(cls, part: dict) -> SupplierQuote:
        """Convert a single entry of 'SearchResults.Parts' into a quote."""
        breaks = []
        currency = ''

        for pb in part.get('PriceBreaks') or []:
            currency = currency or pb.get('Currency') or ''
            breaks.append(PriceBreak(
                quantity=parse_int(pb.get('Quantity')) or 0,
                price=parse_price(pb.get('Price')),
            ))

        stock = parse_int(part.get('AvailabilityInStock'))

        if stock is None:
            stock = parse_int(part.get('Availability')) or 0

        alternatives = []
        replacement = part.get('SuggestedReplacement')

        if replacement:
            alternatives.append(alternative(
                type='Suggested replacement',
                mpn=replacement,
                url=f'https://www.mouser.com/c/?q={quote_plus(replacement)}',
            ))

        # Same product in another packaging (often with another MPN suffix)
        for packaging in part.get('AlternatePackagings') or []:
            mpn = (packaging or {}).get('APMfrPN')

            if mpn and mpn.upper() not in {(a.get('mpn') or '').upper() for a in alternatives}:
                alternatives.append(alternative(
                    type='Alternate packaging',
                    mpn=mpn,
                    url=f'https://www.mouser.com/c/?q={quote_plus(mpn)}',
                ))

        breaks = cls._sorted_breaks(breaks)
        status = part.get('LifecycleStatus') or None

        # 'Mouser does not presently sell this product in your region': no prices, not an error
        if not breaks and part.get('RestrictionMessage'):
            status = 'Not sold by Mouser in your region'

        return SupplierQuote(
            sku=part.get('MouserPartNumber', ''),
            currency=currency.upper(),
            price_breaks=breaks,
            stock=stock,
            url=part.get('ProductDetailUrl'),
            status=status,
            alternatives=alternatives,
        )
