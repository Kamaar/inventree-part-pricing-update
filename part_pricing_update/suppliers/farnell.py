"""Farnell / element14 Product Search API client.

Ref: https://partner.element14.com/docs/Product_Search_API_REST__Description
Limits: 2 requests per second, 1000 requests per day (default plan).
"""

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
    add_parameter,
    normalize_reach,
    normalize_rohs,
    parse_int,
    parse_price,
)

FARNELL_URL = 'https://api.element14.com/catalog/products'

# The API returns bare numbers: the currency depends on the store
FARNELL_STORES = {
    'it.farnell.com': ('Italia', 'EUR'),
    'de.farnell.com': ('Deutschland', 'EUR'),
    'fr.farnell.com': ('France', 'EUR'),
    'es.farnell.com': ('España', 'EUR'),
    'nl.farnell.com': ('Nederland', 'EUR'),
    'be.farnell.com': ('Belgique', 'EUR'),
    'at.farnell.com': ('Österreich', 'EUR'),
    'ie.farnell.com': ('Ireland', 'EUR'),
    'pt.farnell.com': ('Portugal', 'EUR'),
    'fi.farnell.com': ('Suomi', 'EUR'),
    'ch.farnell.com': ('Schweiz', 'CHF'),
    'uk.farnell.com': ('United Kingdom', 'GBP'),
    'dk.farnell.com': ('Danmark', 'DKK'),
    'se.farnell.com': ('Sverige', 'SEK'),
    'no.farnell.com': ('Norge', 'NOK'),
    'pl.farnell.com': ('Polska', 'PLN'),
    'cz.farnell.com': ('Česko', 'CZK'),
    'hu.farnell.com': ('Magyarország', 'HUF'),
    'www.newark.com': ('Newark (US)', 'USD'),
    'canada.newark.com': ('Newark (Canada)', 'CAD'),
}


class FarnellClient(SupplierClient):
    """Fetch prices using the element14 catalog search by order code."""

    key = 'farnell'
    name = 'Farnell'
    min_interval = 0.6

    def __init__(self, api_key: str, store: str = 'it.farnell.com', **kwargs):
        super().__init__(**kwargs)
        self.api_key = (api_key or '').strip()
        self.store = store or 'it.farnell.com'
        self.currency = FARNELL_STORES.get(self.store, ('', 'EUR'))[1]

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _fetch(self, sku: str, mpn: str | None = None) -> SupplierQuote:
        try:
            return self.parse_product(self._product(sku), self.currency)
        except NotFoundError:
            if not mpn:
                raise

        # Order code not found: the product may have a new code, or be removed from the catalog
        mpn = mpn.strip()
        products = [
            p for p in self._query(f'manuPartNum:{mpn}', 20)
            if (p.get('translatedManufacturerPartNumber') or '').upper() == mpn.upper()
        ]

        if products:
            # The ones with prices first: the new order code is reported as suggested SKU
            products.sort(key=lambda p: not p.get('prices'))
            return self.parse_product(products[0], self.currency)

        # Neither the order code nor the MPN exist any more (two independent codes: a typo is unlikely)
        return SupplierQuote(sku='', currency='', status='Not available at Farnell')

    def _details(self, sku: str) -> ProductDetails:
        return self.parse_details(self._product(sku), self.currency, self.store)

    def _search(self, term: str, limit: int) -> list[SearchResult]:
        return [self.parse_result(p, self.currency, self.store) for p in self._query(f'any:{term}', limit)]

    def find_by_mpn(self, mpn: str) -> list[SearchResult]:
        mpn = (mpn or '').strip()

        if not mpn:
            return []

        try:
            products = self._query(f'manuPartNum:{mpn}', 20)
        except requests.RequestException as exc:
            raise SupplierError(f'Farnell: connection error ({exc})') from exc

        return [
            self.parse_result(p, self.currency, self.store)
            for p in products
            if (p.get('translatedManufacturerPartNumber') or '').upper() == mpn.upper()
        ]

    def _product(self, sku: str) -> dict:
        """Return the product of an order code."""
        try:
            return self._by_code(sku)
        except NotFoundError:
            # Re-reel codes ('2913817RL') are not found by the order code search:
            # use the base code, which has the same unit prices
            if len(sku) > 2 and sku.upper().endswith('RL') and sku[:-2].isdigit():
                return self._by_code(sku[:-2])
            raise

    def _by_code(self, sku: str) -> dict:
        products = self._query(f'id:{sku}', 1)

        if not products:
            raise NotFoundError(f'Farnell: SKU {sku} not found on {self.store}')

        return products[0]

    def _query(self, term: str, limit: int) -> list[dict]:
        """Run a catalog search ('id:...', 'any:...', 'manuPartNum:...') and return the products."""
        self._before_request()
        response = self.session.get(
            FARNELL_URL,
            params={
                'versionNumber': '1.4',
                'term': term,
                'storeInfo.id': self.store,
                'resultsSettings.offset': 0,
                'resultsSettings.numberOfResults': limit,
                'resultsSettings.responseGroup': 'large',
                'callInfo.responseDataFormat': 'JSON',
                'callInfo.apiKey': self.api_key,
            },
            headers={'Accept': 'application/json'},
            timeout=self.timeout,
        )

        # The API gateway replies with plain text errors, e.g. 'Developer Over Rate'
        # (daily quota) or 'Developer Over Qps' (requests per second)
        if response.status_code in (403, 429):
            text = response.text or ''

            if 'Over Rate' in text:
                raise RateLimitError('Farnell: daily request limit reached', daily=True)

            if 'Over Qps' in text or response.status_code == 429:
                raise RateLimitError('Farnell: too many requests per second', retry_after=5)

        data = self._json(response)

        fault = data.get('Fault') or data.get('error')

        if fault or response.status_code != 200:
            message = str(fault or response.status_code)

            if response.status_code in (401, 403) or 'key' in message.lower():
                raise AuthError(f'Farnell: {message}')

            raise SupplierError(f'Farnell: {message}')

        # The result key depends on the search type ('premierFarnellPartNumberReturn',
        # 'keywordSearchReturn', 'manufacturerPartNumberSearchReturn')
        for value in data.values():
            if isinstance(value, dict) and 'numberOfResults' in value:
                return value.get('products') or []

        return []

    @staticmethod
    def image_url(product: dict, store: str) -> str:
        """Build the product image URL (same logic as Ki-nTree)."""
        image = product.get('image') or {}

        if not image.get('baseName'):
            return ''

        locale = 'en_GB' if 'farnell' in (image.get('vrntPath') or '') else 'en_US'
        return f'https://{store}/productimages/standard/{locale}{image["baseName"]}'

    @classmethod
    def parse_result(cls, product: dict, currency: str, store: str) -> SearchResult:
        quote = cls.parse_product(product, currency)

        return SearchResult(
            sku=quote.sku,
            mpn=product.get('translatedManufacturerPartNumber') or '',
            manufacturer=product.get('brandName') or product.get('vendorName') or '',
            description=product.get('displayName') or '',
            packaging=product.get('unitOfMeasure') or '',
            image_url=cls.image_url(product, store),
            product_url=product.get('productURL') or '',
            status=quote.status or '',
            stock=quote.stock,
            unit_price=str(quote.price_breaks[0].price) if quote.price_breaks else '',
            currency=currency,
        )

    @staticmethod
    def product_type(product: dict) -> str:
        """Product type from the name, used as category (the API reports no category).

        'COILCRAFT - MSS1048-822NLC - Power Inductor (SMD), 8.2µH, …' -> 'Power Inductor (SMD)'.
        The name is in the language of the store.
        """
        name = product.get('displayName') or ''
        mpn = product.get('translatedManufacturerPartNumber') or ''
        marker = f'{mpn} - ' if mpn else ''

        if marker and marker in name:
            rest = name.split(marker, 1)[1]
        else:
            parts = name.split(' - ', 2)
            rest = parts[2] if len(parts) == 3 else ''

        return rest.split(',')[0].strip()

    @classmethod
    def parse_details(cls, product: dict, currency: str, store: str) -> ProductDetails:
        """Convert a 'products' entry into the full product data."""
        parameters = {}

        for attribute in product.get('attributes') or []:
            value = attribute.get('attributeValue')
            unit = attribute.get('attributeUnit')

            if value and unit and not str(value).endswith(str(unit)):
                value = f'{value}{unit}'

            add_parameter(parameters, attribute.get('attributeLabel'), value)

        # RoHS3 = RoHS + the 4 phthalates of directive 2015/863
        rohs = normalize_rohs(
            parameters.get('rohsCompliant') or product.get('rohsStatusCode'),
            phthalates=(parameters.get('rohsPhthalatesCompliant') or '').upper() == 'YES',
        )
        # REACH: the SVHC content ('No SVHC (25-Jun-2025)')
        reach = normalize_reach(
            parameters.get('SVHC') or next((v for k, v in parameters.items() if 'SVHC' in k), '')
        )


        add_parameter(parameters, 'Country of Origin', product.get('countryOfOrigin'))

        datasheet = next((d.get('url') for d in product.get('datasheets') or [] if d.get('url')), '')

        if datasheet.startswith('http://'):
            datasheet = 'https://' + datasheet[len('http://'):]

        return ProductDetails(
            supplier='farnell',
            sku=str(product.get('sku', '')),
            mpn=product.get('translatedManufacturerPartNumber') or '',
            manufacturer=product.get('brandName') or product.get('vendorName') or '',
            description=product.get('displayName') or '',
            category=[t for t in [cls.product_type(product)] if t],
            parameters=parameters,
            packaging=product.get('unitOfMeasure') or '',
            datasheet_url=datasheet,
            image_url=cls.image_url(product, store),
            product_url=product.get('productURL') or '',
            rohs=rohs,
            reach=reach,
            quote=cls.parse_product(product, currency),
        )

    @classmethod
    def parse_product(cls, product: dict, currency: str) -> SupplierQuote:
        """Convert a single entry of 'products' into a quote."""
        breaks = [
            PriceBreak(
                quantity=parse_int(pb.get('from')) or 0,
                price=parse_price(pb.get('cost')),
            )
            for pb in product.get('prices') or []
        ]

        stock = (product.get('stock') or {}).get('level')

        return SupplierQuote(
            sku=str(product.get('sku', '')),
            currency=currency,
            price_breaks=cls._sorted_breaks(breaks),
            stock=parse_int(stock),
            url=product.get('productURL'),
            status=product.get('productStatus') or None,
        )
