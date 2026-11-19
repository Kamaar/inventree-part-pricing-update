"""DigiKey Product Information v4 API client.

Uses the OAuth2 "2-legged" flow (client credentials), which needs no browser
login and works unattended in the background worker.

Ref: https://developer.digikey.com/tutorials-and-resources/oauth-20-2-legged-flow
Limits: 120 requests per minute, 1000 requests per day (default plan).
"""

import time
from urllib.parse import quote

import requests

from .base import (
    NO_PRICE_PREFIX,
    AuthError,
    NotFoundError,
    PriceBreak,
    ProductDetails,
    RateLimitError,
    SearchResult,
    SupplierClient,
    SupplierError,
    SupplierQuote,
    add_parameter,
    alternative,
    is_discontinued,
    normalize_reach,
    normalize_rohs,
    parse_int,
    parse_price,
)

DIGIKEY_API = 'https://api.digikey.com'
DIGIKEY_SANDBOX_API = 'https://sandbox-api.digikey.com'

# Web site of each DigiKey locale site (for the relative product URLs)
DIGIKEY_SITES = {
    'IT': 'www.digikey.it', 'DE': 'www.digikey.de', 'FR': 'www.digikey.fr', 'ES': 'www.digikey.es',
    'NL': 'www.digikey.nl', 'BE': 'www.digikey.be', 'AT': 'www.digikey.at', 'CH': 'www.digikey.ch',
    'UK': 'www.digikey.co.uk', 'GB': 'www.digikey.co.uk', 'IE': 'www.digikey.ie', 'US': 'www.digikey.com',
}


class DigiKeyClient(SupplierClient):
    """Fetch prices using the DigiKey 'productdetails' endpoint."""

    key = 'digikey'
    name = 'DigiKey'
    min_interval = 0.6

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        site: str = 'IT',
        language: str = 'en',
        currency: str = 'EUR',
        sandbox: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.client_id = (client_id or '').strip()
        self.client_secret = (client_secret or '').strip()
        self.site = site or 'IT'
        self.language = language or 'en'
        self.currency = currency or 'EUR'
        self.base_url = DIGIKEY_SANDBOX_API if sandbox else DIGIKEY_API
        self._token: str | None = None
        self._token_expiry = 0.0

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token

        response = self.session.post(
            f'{self.base_url}/v1/oauth2/token',
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials',
            },
            timeout=self.timeout,
        )

        data = self._json(response)

        if response.status_code != 200 or 'access_token' not in data:
            raise AuthError(
                f'DigiKey: authentication failed ({data.get("error_description") or data.get("detail") or response.status_code})'
            )

        self._token = data['access_token']
        # Renew one minute before the real expiry
        self._token_expiry = time.monotonic() + int(data.get('expires_in', 600)) - 60
        return self._token

    def _get(self, path: str, not_found: str) -> dict:
        """GET a Product Information v4 endpoint, converting the errors."""
        return self._request('GET', path, not_found)

    def _request(self, method: str, path: str, not_found: str, body: dict | None = None) -> dict:
        """Call a Product Information v4 endpoint, converting the errors."""
        token = self._get_token()
        self._before_request()
        send = self.session.post if method == 'POST' else self.session.get
        extra = {'json': body} if body is not None else {}
        response = send(
            f'{self.base_url}/products/v4{path}',
            **extra,
            headers={
                'Authorization': f'Bearer {token}',
                'X-DIGIKEY-Client-Id': self.client_id,
                'X-DIGIKEY-Locale-Site': self.site,
                'X-DIGIKEY-Locale-Language': self.language,
                'X-DIGIKEY-Locale-Currency': self.currency,
                'Accept': 'application/json',
            },
            timeout=self.timeout,
        )

        if response.status_code == 429:
            # 'Retry-After' is short for the per-minute limit, long for the daily one
            retry_after = parse_int(response.headers.get('Retry-After'))
            raise RateLimitError(
                f'DigiKey: request limit reached (retry after {retry_after or "?"} s)',
                daily=bool(retry_after and retry_after > 3600),
                retry_after=retry_after,
            )

        if response.status_code == 401:
            # Token revoked or expired early: request a new one next time
            self._token = None
            raise AuthError('DigiKey: unauthorized (check that the app is subscribed to Product Information V4)')

        if response.status_code == 404:
            raise NotFoundError(not_found)

        data = self._json(response)

        if response.status_code != 200:
            raise SupplierError(f'DigiKey: {data.get("title", "")} {data.get("detail", response.status_code)}'.strip())

        return data

    def _fetch(self, sku: str, mpn: str | None = None) -> SupplierQuote:
        try:
            data = self._get(f'/search/{quote(sku, safe="")}/productdetails', f'DigiKey: SKU {sku} not found')
        except NotFoundError:
            # DigiKey renumbers products from time to time:
            # the manufacturer part number still finds them
            if not mpn or mpn.upper() == sku.upper():
                raise

            try:
                data = self._get(f'/search/{quote(mpn, safe="")}/productdetails', '')
            except NotFoundError:
                # 'productdetails' needs an exact and unique match, while the
                # web site (and the keyword search) also lists discontinued products
                data = self.keyword_match(mpn)

                if data is None:
                    return self._removed_product(sku, mpn)

        result = self.parse_product(data, sku)

        # Replacement products: not needed when the product is sold in another packaging
        if (not result.price_breaks and not result.alternatives) or is_discontinued(result.status):
            result.alternatives = self.substitutions(result.sku or mpn or sku)

        return result

    def _details(self, sku: str) -> ProductDetails:
        data = self._get(f'/search/{quote(sku, safe="")}/productdetails', f'DigiKey: SKU {sku} not found')
        return self.parse_details(data, sku)

    def _search(self, term: str, limit: int) -> list[SearchResult]:
        data = self._request('POST', '/search/keyword', '', body={'Keywords': term, 'Limit': limit})
        results = self._results(data.get('ExactMatches'), data.get('Products'))

        if results:
            return results

        # The keyword search may find nothing for an exact code (e.g. a numeric MPN):
        # try it as DigiKey product number / MPN (one more request)
        try:
            data = self._get(f'/search/{quote(term, safe="")}/productdetails', '')
        except NotFoundError:
            return []

        return self._results([data.get('Product') or {}])

    def find_by_mpn(self, mpn: str) -> list[SearchResult]:
        """Exact MPN lookup (one request), with the keyword search as fallback (one more)."""
        mpn = (mpn or '').strip()

        if not mpn:
            return []

        try:
            try:
                data = self._get(f'/search/{quote(mpn, safe="")}/productdetails', '')
                results = self._results([data.get('Product') or {}])
            except NotFoundError:
                data = self._request('POST', '/search/keyword', '', body={'Keywords': mpn, 'Limit': 20})
                results = self._results(data.get('ExactMatches'), data.get('Products'))
        except requests.RequestException as exc:
            raise SupplierError(f'DigiKey: connection error ({exc})') from exc

        return [r for r in results if r.mpn.upper() == mpn.upper()]

    @classmethod
    def _results(cls, *product_lists) -> list[SearchResult]:
        """One search result for each packaging (variation) of each product."""
        results = {}

        for products in product_lists:
            for product in products or []:
                status = cls._status(product) or ''

                for variation in product.get('ProductVariations') or []:
                    sku = variation.get('DigiKeyProductNumber')

                    if not sku or sku in results:
                        continue

                    pricing = variation.get('StandardPricing') or []
                    results[sku] = SearchResult(
                        sku=sku,
                        mpn=product.get('ManufacturerProductNumber') or '',
                        manufacturer=(product.get('Manufacturer') or {}).get('Name') or '',
                        description=(product.get('Description') or {}).get('ProductDescription') or '',
                        packaging=(variation.get('PackageType') or {}).get('Name') or '',
                        image_url=cls._url(product.get('PhotoUrl')),
                        product_url=product.get('ProductUrl') or '',
                        status=status,
                        stock=parse_int(variation.get('QuantityAvailableforPackageType')),
                        unit_price=str(pricing[0].get('UnitPrice')) if pricing else '',
                        aliases=cls._aliases(product),
                    )

        return list(results.values())

    @classmethod
    def parse_details(cls, data: dict, sku: str) -> ProductDetails:
        """Convert a 'productdetails' response into the full product data."""
        product = data.get('Product') or {}
        quote_data = cls.parse_product(data, sku)

        variation = next(
            (v for v in product.get('ProductVariations') or [] if v.get('DigiKeyProductNumber') == quote_data.sku),
            {},
        )

        # Category tree: the product belongs to the innermost child
        category = []
        node = product.get('Category')

        while node:
            if node.get('Name'):
                category.append(node['Name'])
            node = (node.get('ChildCategories') or [None])[0]

        parameters = {}

        for parameter in product.get('Parameters') or []:
            add_parameter(parameters, parameter.get('ParameterText'), parameter.get('ValueText'))

        add_parameter(parameters, 'Series', (product.get('Series') or {}).get('Name'))
        add_parameter(parameters, 'Base Product Number', (product.get('BaseProductNumber') or {}).get('Name'))

        classifications = product.get('Classifications') or {}

        for name, key in (
            ('Moisture Sensitivity Level', 'MoistureSensitivityLevel'),
            ('ECCN', 'ExportControlClassNumber'),
            ('HTSUS', 'HtsusCode'),
        ):
            add_parameter(parameters, name, classifications.get(key))

        rohs = normalize_rohs(classifications.get('RohsStatus'))
        reach = normalize_reach(classifications.get('ReachStatus'))


        description = product.get('Description') or {}

        return ProductDetails(
            supplier='digikey',
            sku=quote_data.sku or sku,
            mpn=product.get('ManufacturerProductNumber') or '',
            manufacturer=(product.get('Manufacturer') or {}).get('Name') or '',
            description=description.get('ProductDescription') or '',
            detailed_description=description.get('DetailedDescription') or '',
            category=category,
            parameters=parameters,
            packaging=(variation.get('PackageType') or {}).get('Name') or '',
            datasheet_url=cls._url(product.get('DatasheetUrl')),
            image_url=cls._url(product.get('PhotoUrl')),
            product_url=product.get('ProductUrl') or '',
            rohs=rohs,
            reach=reach,
            quote=quote_data,
            aliases=cls._aliases(product),
        )

    @staticmethod
    def _aliases(product: dict) -> list[str]:
        """Other names of the product: a renamed product keeps here its old MPN ('0402ZD224KAT2A')."""
        mpn = (product.get('ManufacturerProductNumber') or '').upper()
        names = [str(n).strip() for n in product.get('OtherNames') or [] if n]
        return list(dict.fromkeys(n for n in names if n and n.upper() != mpn))

    @staticmethod
    def _url(value, site: str = '') -> str:
        """Absolute URL: DigiKey returns protocol-relative ('//mm.digikey.com/...') and,
        for the substitutions, site-relative URLs ('/en/products/detail/...')."""
        value = (value or '').strip()

        if value.startswith('//'):
            return f'https:{value}'

        if value.startswith('/'):
            return f'https://{DIGIKEY_SITES.get((site or "").upper(), "www.digikey.com")}{value}'

        return value

    @staticmethod
    def _status(product: dict) -> str | None:
        status = (product.get('ProductStatus') or {}).get('Status')

        # The flags are set even when the status is still 'Active'
        if product.get('Discontinued') and not is_discontinued(status):
            return 'Discontinued at Digi-Key'

        if product.get('EndOfLife') and not is_discontinued(status):
            return 'End of Life'

        return status

    def _removed_product(self, sku: str, mpn: str) -> SupplierQuote:
        """Neither the SKU nor the MPN exist any more: the product has been removed from the catalog.

        Two independent codes not found make a typo unlikely. The alternatives
        are often not available either (the web site has more than the API).
        """
        alternatives = []

        for number in dict.fromkeys((sku, mpn)):
            alternatives = self.substitutions(number)

            if alternatives:
                break

        return SupplierQuote(
            sku='',
            currency=self.currency,
            status='Not available at Digi-Key',
            alternatives=alternatives,
        )

    def environmental_documents(self, sku: str) -> list[dict]:
        """RoHS / REACH declarations of the manufacturer (one request).

        Returns the 'Environmental Information' media: [{'title': ..., 'url': ...}]
        """
        try:
            data = self._get(f'/search/{quote(sku, safe="")}/media', '')
        except NotFoundError:
            return []
        except requests.RequestException as exc:
            raise SupplierError(f'DigiKey: connection error ({exc})') from exc

        documents = []

        for media in data.get('MediaLinks') or []:
            url = self._url(media.get('Url'))

            if media.get('MediaType') == 'Environmental Information' and url and url not in [d['url'] for d in documents]:
                documents.append({'title': (media.get('Title') or '').strip(), 'url': url})

        return documents

    def keyword_match(self, mpn: str) -> dict | None:
        """Search the MPN by keyword (one request), returning a productdetails-like response."""
        try:
            data = self._request('POST', '/search/keyword', '', body={'Keywords': mpn, 'Limit': 10})
        except NotFoundError:
            return None

        candidates = (data.get('ExactMatches') or []) + (data.get('Products') or [])
        product = next(
            (p for p in candidates if (p.get('ManufacturerProductNumber') or '').upper() == mpn.upper()),
            None,
        )

        if product is None:
            return None

        return {'Product': product, 'SearchLocaleUsed': data.get('SearchLocaleUsed')}

    def substitutions(self, product_number: str) -> list[dict]:
        """Replacement products suggested by DigiKey (one request)."""
        try:
            data = self._get(f'/search/{quote(product_number, safe="")}/substitutions', '')
        except NotFoundError:
            return []

        return [
            alternative(
                type=sub.get('SubstituteType'),
                mpn=sub.get('ManufacturerProductNumber'),
                manufacturer=(sub.get('Manufacturer') or {}).get('Name'),
                sku=sub.get('DigiKeyProductNumber'),
                description=sub.get('Description'),
                url=self._url(sub.get('ProductUrl'), self.site),
                unit_price=sub.get('UnitPrice'),
                stock=parse_int(sub.get('QuantityAvailable')),
            )
            for sub in data.get('ProductSubstitutes') or []
        ]

    @classmethod
    def parse_product(cls, data: dict, sku: str) -> SupplierQuote:
        """Convert a 'productdetails' response into a quote for the given SKU."""
        product = data.get('Product') or {}
        variations = product.get('ProductVariations') or []

        currency = (data.get('SearchLocaleUsed') or {}).get('Currency') or ''
        status = cls._status(product)

        if not variations:
            # Not sold by DigiKey any more: the caller reports the status
            return SupplierQuote(sku='', currency=currency.upper(), status=status, url=product.get('ProductUrl'))

        # The same product has several packagings (cut tape, reel, Digi-Reel):
        # pick the one matching the SKU, or the one with the lowest MOQ
        variation = next(
            (v for v in variations if (v.get('DigiKeyProductNumber') or '').upper() == sku.upper()),
            None,
        ) or min(variations, key=lambda v: v.get('MinimumOrderQuantity') or 0)

        breaks = [
            PriceBreak(
                quantity=parse_int(pb.get('BreakQuantity')) or 0,
                price=parse_price(pb.get('UnitPrice')),
            )
            for pb in variation.get('StandardPricing') or []
        ]

        stock = variation.get('QuantityAvailableforPackageType')

        if stock is None:
            stock = product.get('QuantityAvailable')

        breaks = cls._sorted_breaks(breaks)
        alternatives = []

        # Active product without price in this packaging (e.g. not stocked: only full reels):
        # the packagings with a price are the alternatives
        if not breaks and not is_discontinued(status):
            priced = [v for v in variations if v is not variation and v.get('StandardPricing')]

            for other in priced:
                pricing = sorted(other.get('StandardPricing'), key=lambda pb: pb.get('BreakQuantity') or 0)
                moq = parse_int(other.get('MinimumOrderQuantity')) or parse_int(pricing[0].get('BreakQuantity'))
                alternatives.append(alternative(
                    type=f'Other packaging: {(other.get("PackageType") or {}).get("Name") or "?"}'
                    + (f', min {moq}' if moq else ''),
                    mpn=product.get('ManufacturerProductNumber') or '',
                    manufacturer=(product.get('Manufacturer') or {}).get('Name') or '',
                    sku=other.get('DigiKeyProductNumber') or '',
                    url=cls._url(product.get('ProductUrl')),
                    unit_price=str(pricing[0].get('UnitPrice') or ''),
                    stock=parse_int(other.get('QuantityAvailableforPackageType')),
                ))

            if priced:
                names = ', '.join((v.get('PackageType') or {}).get('Name') or '?' for v in priced)
                status = (
                    f'{NO_PRICE_PREFIX} {(variation.get("PackageType") or {}).get("Name") or "this packaging"}: '
                    f'sold as {names}'
                )

        return SupplierQuote(
            sku=variation.get('DigiKeyProductNumber') or sku,
            currency=currency.upper(),
            price_breaks=breaks,
            stock=parse_int(stock),
            url=product.get('ProductUrl'),
            status=status,
            alternatives=alternatives,
        )
