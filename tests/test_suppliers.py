"""Tests for the supplier API clients, with mocked HTTP responses.

Run with: uv run pytest
"""

import copy
from decimal import Decimal

import pytest

from part_pricing_update.suppliers import (
    AuthError,
    DigiKeyClient,
    FarnellClient,
    MouserClient,
    NotFoundError,
    RateLimitError,
)
from part_pricing_update.suppliers.base import parse_int, parse_price


class FakeResponse:
    def __init__(self, data, status_code=200, headers=None, text=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text if text is not None else str(data)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    """Return the queued responses in order, recording the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def _next(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self._next('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._next('POST', url, **kwargs)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('0,123 €', '0.123'),
        ('€0.123', '0.123'),
        ('$1,234.56', '1234.56'),
        ('1.234,56 €', '1234.56'),
        ('12,50', '12.50'),
        ('1.234.567', '1234567'),
        (0.5, '0.5'),
        ('', None),
        ('N/A', None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == (Decimal(expected) if expected else None)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [(1234, 1234), ('1.234 In Stock', 1234), ('1,234', 1234), ('None', None), (None, None)],
)
def test_parse_int(text, expected):
    assert parse_int(text) == expected


MOUSER_RESPONSE = {
    'Errors': [],
    'SearchResults': {
        'NumberOfResult': 2,
        'Parts': [
            {'MouserPartNumber': '595-NE555P-OTHER', 'PriceBreaks': []},
            {
                'MouserPartNumber': '595-NE555P',
                'ProductDetailUrl': 'https://www.mouser.it/ProductDetail/595-NE555P',
                'AvailabilityInStock': '12345',
                'PriceBreaks': [
                    {'Quantity': 10, 'Price': '0,321 €', 'Currency': 'EUR'},
                    {'Quantity': 1, 'Price': '0,45 €', 'Currency': 'EUR'},
                    {'Quantity': 100, 'Price': '0,25 €', 'Currency': 'EUR'},
                ],
            },
        ],
    },
}


def test_mouser_quote():
    session = FakeSession(FakeResponse(MOUSER_RESPONSE))
    client = MouserClient('key', session=session)
    client.min_interval = 0

    quote = client.fetch('595-NE555P')

    assert quote.currency == 'EUR'
    assert quote.stock == 12345
    assert [(pb.quantity, pb.price) for pb in quote.price_breaks] == [
        (1, Decimal('0.45')),
        (10, Decimal('0.321')),
        (100, Decimal('0.25')),
    ]
    assert session.requests[0][2]['params'] == {'apiKey': 'key'}


def test_mouser_errors():
    client = MouserClient('key', session=FakeSession(
        FakeResponse({'Errors': [{'Code': 'TooManyRequests'}], 'SearchResults': None}),
        FakeResponse({'Errors': [{'Code': 'Invalid', 'PropertyName': 'API Key'}], 'SearchResults': None}),
        FakeResponse({'Errors': [], 'SearchResults': {'NumberOfResult': 0, 'Parts': []}}),
    ))
    client.min_interval = 0

    with pytest.raises(RateLimitError):
        client.fetch('ABC123')
    with pytest.raises(AuthError):
        client.fetch('ABC123')
    with pytest.raises(NotFoundError):
        client.fetch('ABC123')


DIGIKEY_RESPONSE = {
    'SearchLocaleUsed': {'Site': 'IT', 'Language': 'en', 'Currency': 'EUR'},
    'Product': {
        'ProductUrl': 'https://www.digikey.it/it/products/detail/x/296-1411-5-ND',
        'QuantityAvailable': 50000,
        'ProductVariations': [
            {
                'DigiKeyProductNumber': '296-NE555P-TR-ND',
                'MinimumOrderQuantity': 2000,
                'StandardPricing': [{'BreakQuantity': 2000, 'UnitPrice': 0.1}],
            },
            {
                'DigiKeyProductNumber': '296-1411-5-ND',
                'MinimumOrderQuantity': 1,
                'QuantityAvailableforPackageType': 4321,
                'StandardPricing': [
                    {'BreakQuantity': 1, 'UnitPrice': 0.52},
                    {'BreakQuantity': 10, 'UnitPrice': 0.415},
                ],
            },
        ],
    },
}


def test_digikey_quote_and_token_reuse():
    session = FakeSession(
        FakeResponse({'access_token': 'TOKEN', 'expires_in': 599}),
        FakeResponse(DIGIKEY_RESPONSE),
        FakeResponse(DIGIKEY_RESPONSE),
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0

    quote = client.fetch('296-1411-5-ND')
    client.fetch('296-1411-5-ND')

    assert quote.currency == 'EUR'
    assert quote.stock == 4321
    assert [(pb.quantity, pb.price) for pb in quote.price_breaks] == [
        (1, Decimal('0.52')),
        (10, Decimal('0.415')),
    ]

    # One token request, then two product requests with the bearer token
    methods = [r[0] for r in session.requests]
    assert methods == ['POST', 'GET', 'GET']
    assert session.requests[0][2]['data']['grant_type'] == 'client_credentials'
    assert session.requests[1][2]['headers']['Authorization'] == 'Bearer TOKEN'
    assert session.requests[1][2]['headers']['X-DIGIKEY-Locale-Currency'] == 'EUR'


def test_digikey_auth_error():
    client = DigiKeyClient('id', 'bad', session=FakeSession(
        FakeResponse({'error': 'invalid_client', 'error_description': 'bad secret'}, status_code=401),
    ))

    with pytest.raises(AuthError):
        client.fetch('296-1411-5-ND')


FARNELL_RESPONSE = {
    'premierFarnellPartNumberReturn': {
        'numberOfResults': 1,
        'products': [
            {
                'sku': '3006059',
                'productURL': 'https://it.farnell.com/3006059',
                'stock': {'level': 789},
                'prices': [
                    {'from': 1, 'to': 9, 'cost': 0.612},
                    {'from': 10, 'to': 99, 'cost': 0.48},
                ],
            }
        ],
    }
}


def test_farnell_quote():
    session = FakeSession(FakeResponse(FARNELL_RESPONSE))
    client = FarnellClient('key', store='uk.farnell.com', session=session)
    client.min_interval = 0

    quote = client.fetch('3006059')

    assert quote.currency == 'GBP'
    assert quote.stock == 789
    assert [(pb.quantity, pb.price) for pb in quote.price_breaks] == [
        (1, Decimal('0.612')),
        (10, Decimal('0.48')),
    ]
    params = session.requests[0][2]['params']
    assert params['term'] == 'id:3006059'
    assert params['storeInfo.id'] == 'uk.farnell.com'


def test_farnell_not_found():
    client = FarnellClient('key', session=FakeSession(
        FakeResponse({'premierFarnellPartNumberReturn': {'numberOfResults': 0}}),
    ))
    client.min_interval = 0

    with pytest.raises(NotFoundError):
        client.fetch('0000000')


def test_empty_sku():
    with pytest.raises(NotFoundError):
        MouserClient('key', session=FakeSession()).fetch('  ')


def test_rate_limit_details():
    mouser = MouserClient('key', session=FakeSession(
        FakeResponse({'Errors': [{'Code': 'TooManyRequests'}], 'SearchResults': None}),
    ))
    with pytest.raises(RateLimitError) as exc:
        mouser.fetch('ABC123')
    assert exc.value.daily

    digikey = DigiKeyClient('id', 'secret', session=FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=429, headers={'Retry-After': '30'}),
        FakeResponse({}, status_code=429, headers={'Retry-After': '40000'}),
    ))
    digikey.min_interval = 0
    with pytest.raises(RateLimitError) as exc:
        digikey.fetch('ABC123')
    assert not exc.value.daily and exc.value.retry_after == 30
    with pytest.raises(RateLimitError) as exc:
        digikey.fetch('ABC123')
    assert exc.value.daily

    farnell = FarnellClient('key', session=FakeSession(
        FakeResponse(ValueError(), status_code=403, text='<h1>Developer Over Rate</h1>'),
        FakeResponse(ValueError(), status_code=403, text='<h1>Developer Over Qps</h1>'),
    ))
    farnell.min_interval = 0
    with pytest.raises(RateLimitError) as exc:
        farnell.fetch('ABC123')
    assert exc.value.daily
    with pytest.raises(RateLimitError) as exc:
        farnell.fetch('ABC123')
    assert not exc.value.daily


@pytest.mark.parametrize('sku', ['N/A', 'n/a', 'NA', ' - ', '---', 'TBD', '?', 'none', ''])
def test_placeholder_sku_skipped(sku):
    session = FakeSession()
    with pytest.raises(NotFoundError):
        MouserClient('key', session=session).fetch(sku)
    assert session.requests == []


@pytest.mark.parametrize('sku', ['595-NE555P', '2913817RL', 'NA555', 'X7R-100N'])
def test_real_sku_not_placeholder(sku):
    from part_pricing_update.suppliers import is_placeholder_sku

    assert not is_placeholder_sku(sku)


def test_farnell_reel_fallback_counts_both_requests():
    session = FakeSession(
        FakeResponse({'premierFarnellPartNumberReturn': {'numberOfResults': 0}}),
        FakeResponse(FARNELL_RESPONSE),
    )
    client = FarnellClient('key', session=session)
    client.min_interval = 0
    calls = []
    client.on_request = lambda: calls.append(1)

    quote = client.fetch('2913817RL')

    assert quote.price_breaks
    assert [r[2]['params']['term'] for r in session.requests] == ['id:2913817RL', 'id:2913817']
    assert len(calls) == 2


def test_digikey_status_reported():
    data = copy.deepcopy(DIGIKEY_RESPONSE)
    data['Product']['ProductStatus'] = {'Id': 2, 'Status': 'Obsolete'}
    data['Product']['ProductVariations'][1]['StandardPricing'] = []

    quote = DigiKeyClient.parse_product(data, '296-1411-5-ND')

    assert quote.status == 'Obsolete'
    assert quote.price_breaks == []


SUBSTITUTIONS_RESPONSE = {
    'ProductSubstitutesCount': 1,
    'ProductSubstitutes': [
        {
            'SubstituteType': 'Similar',
            'ProductUrl': 'https://www.digikey.it/x',
            'Description': 'FIXED IND 8.2UH',
            'Manufacturer': {'Id': 1, 'Name': 'Coilcraft'},
            'ManufacturerProductNumber': 'XEL4030-822MEC',
            'UnitPrice': '1,23 €',
            'QuantityAvailable': 2500,
            'DigiKeyProductNumber': '2457-XEL4030-822MECCT-ND',
        }
    ],
}


def test_digikey_renumbered_sku_found_by_mpn():
    renumbered = copy.deepcopy(DIGIKEY_RESPONSE)
    renumbered['Product']['ProductStatus'] = {'Status': 'Active'}
    renumbered['Product']['ProductVariations'] = [
        {
            'DigiKeyProductNumber': '631-FT5CNDPK12.8-T1CT-ND',
            'MinimumOrderQuantity': 1,
            'StandardPricing': [{'BreakQuantity': 1, 'UnitPrice': 4.2}],
        }
    ]
    session = FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),
        FakeResponse(renumbered),
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0

    quote = client.fetch('631-1062-1-ND', 'FT5CNDPK12.8-T1')

    assert quote.sku == '631-FT5CNDPK12.8-T1CT-ND'
    assert quote.price_breaks[0].price == Decimal('4.2')
    assert quote.alternatives == []
    assert session.requests[2][1].endswith('/search/FT5CNDPK12.8-T1/productdetails')


def test_digikey_not_orderable_gets_substitutions():
    obsolete = copy.deepcopy(DIGIKEY_RESPONSE)
    obsolete['Product']['ProductStatus'] = {'Status': 'Obsolete'}
    obsolete['Product']['ProductVariations'] = []
    session = FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),
        FakeResponse(obsolete),
        FakeResponse(SUBSTITUTIONS_RESPONSE),
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0
    calls = []
    client.on_request = lambda: calls.append(1)

    quote = client.fetch('2457-MSS1048-822NLC-ND', 'MSS1048-822NLC')

    assert quote.status == 'Obsolete'
    assert quote.price_breaks == []
    assert quote.alternatives[0]['mpn'] == 'XEL4030-822MEC'
    assert quote.alternatives[0]['manufacturer'] == 'Coilcraft'
    assert quote.alternatives[0]['stock'] == 2500
    assert session.requests[3][1].endswith('/search/MSS1048-822NLC/substitutions')
    assert len(calls) == 3  # token request not counted


def test_digikey_not_found_without_mpn():
    client = DigiKeyClient('id', 'secret', session=FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),
    ))
    client.min_interval = 0

    with pytest.raises(NotFoundError):
        client.fetch('631-1062-1-ND')


def test_mouser_suggested_replacement():
    part = copy.deepcopy(MOUSER_RESPONSE['SearchResults']['Parts'][1])
    part['LifecycleStatus'] = 'Obsolete'
    part['SuggestedReplacement'] = 'NE555DR'

    quote = MouserClient.parse_part(part)

    assert quote.status == 'Obsolete'
    assert quote.alternatives[0]['mpn'] == 'NE555DR'


@pytest.mark.parametrize(
    ('status', 'expected'),
    [
        ('Active', False),
        ('Preliminary', False),
        ('STOCKED', False),
        ('DIRECT_SHIP', False),
        ('New Product', False),
        (None, False),
        ('', False),
        ('Obsolete', True),
        ('Discontinued at Digi-Key', True),
        ('Last Time Buy', True),
        ('Not For New Designs', True),
        ('Not Recommended for New Designs', True),
        ('End of Life', True),
        ('NO_LONGER_STOCKED', True),
        ('NO_LONGER_MANUFACTURED', True),
        ('Not available at Digi-Key', True),
    ],
)
def test_is_discontinued(status, expected):
    from part_pricing_update.suppliers import is_discontinued

    assert is_discontinued(status) is expected


def test_digikey_keyword_fallback_for_discontinued_product():
    discontinued = copy.deepcopy(DIGIKEY_RESPONSE['Product'])
    discontinued['ManufacturerProductNumber'] = 'MSS1048-822NLC'
    discontinued['ProductStatus'] = {'Status': 'Active'}
    discontinued['Discontinued'] = True
    discontinued['ProductVariations'] = [
        {'DigiKeyProductNumber': '2457-MSS1048-822NLCCT-ND', 'MinimumOrderQuantity': 1, 'StandardPricing': []}
    ]
    other = {'ManufacturerProductNumber': 'MSS1048-822NLB', 'ProductVariations': []}
    session = FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),  # productdetails by SKU
        FakeResponse({}, status_code=404),  # productdetails by MPN
        FakeResponse({'Products': [other, discontinued], 'ExactMatches': [], 'SearchLocaleUsed': {'Currency': 'EUR'}}),
        FakeResponse(SUBSTITUTIONS_RESPONSE),
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0

    quote = client.fetch('2457-MSS1048-822NLC-ND', 'MSS1048-822NLC')

    assert quote.status == 'Discontinued at Digi-Key'
    assert quote.price_breaks == []
    assert quote.alternatives[0]['mpn'] == 'XEL4030-822MEC'
    method, url, kwargs = session.requests[3]
    assert method == 'POST' and url.endswith('/search/keyword')
    assert kwargs['json'] == {'Keywords': 'MSS1048-822NLC', 'Limit': 10}


def test_digikey_keyword_fallback_no_exact_match():
    session = FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),
        FakeResponse({}, status_code=404),
        FakeResponse({'Products': [{'ManufacturerProductNumber': 'OTHER'}], 'ExactMatches': []}),
        FakeResponse({}, status_code=404),  # substitutions by SKU
        FakeResponse({}, status_code=404),  # substitutions by MPN
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0

    # Real DigiKey behaviour for MSS1048-822NLC: 404 everywhere, no keyword match
    quote = client.fetch('2457-MSS1048-822NLC-ND', 'MSS1048-822NLC')

    assert quote.status == 'Not available at Digi-Key'
    assert quote.price_breaks == []
    assert quote.alternatives == []


def test_digikey_removed_product_alternatives_by_sku():
    session = FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}),
        FakeResponse({}, status_code=404),  # productdetails by SKU
        FakeResponse({}, status_code=404),  # productdetails by MPN
        FakeResponse({'Products': [], 'ExactMatches': []}),  # keyword
        FakeResponse({}, status_code=404),  # substitutions by SKU
        FakeResponse(SUBSTITUTIONS_RESPONSE),  # substitutions by MPN
    )
    client = DigiKeyClient('id', 'secret', session=session)
    client.min_interval = 0
    calls = []
    client.on_request = lambda: calls.append(1)

    quote = client.fetch('2457-MSS1048-822NLC-ND', 'MSS1048-822NLC')

    assert quote.status == 'Not available at Digi-Key'
    assert quote.alternatives[0]['mpn'] == 'XEL4030-822MEC'
    assert len(calls) == 5


@pytest.mark.parametrize(
    ('value', 'phthalates', 'expected'),
    [
        # Same values for every supplier
        ('ROHS3 Compliant', None, 'Conforme (RoHS3)'),
        ('RoHS Compliant', None, 'Conforme'),
        ('RoHS Compliant By Exemption', None, 'Conforme (con esenzione)'),
        ('Not RoHS Compliant', None, 'Non conforme'),
        ('RoHS non-compliant', None, 'Non conforme'),
        ('Not Applicable', None, 'Non applicabile'),
        ('YES', True, 'Conforme (RoHS3)'),
        ('YES', False, 'Conforme'),
        ('YES-EXEMPT', False, 'Conforme (con esenzione)'),
        ('NO', None, 'Non conforme'),
        ('Vendor undefined', None, ''),
        ('', None, ''),
        (None, None, ''),
    ],
)
def test_normalize_rohs(value, phthalates, expected):
    from part_pricing_update.suppliers.base import normalize_rohs

    assert normalize_rohs(value, phthalates) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('REACH Unaffected', 'Conforme (nessuna SVHC)'),
        ('REACH Affected', 'Contiene SVHC'),
        ('REACH not applicable', 'Non applicabile'),
        ('No SVHC (25-Jun-2025)', 'Conforme (nessuna SVHC al 25-Jun-2025)'),
        ('No SVHC', 'Conforme (nessuna SVHC)'),
        ('Lead (27-Jun-2024)', 'Contiene SVHC: Lead (27-Jun-2024)'),
        ('To Be Advised', ''),
        ('Vendor undefined', ''),
        (None, ''),
    ],
)
def test_normalize_reach(value, expected):
    from part_pricing_update.suppliers.base import normalize_reach

    assert normalize_reach(value) == expected


def test_digikey_substitution_relative_url():
    """Real DigiKey answer: the substitutions have site-relative product URLs."""
    data = {'ProductSubstitutes': [{
        'SubstituteType': 'Parametric Equivalent', 'ManufacturerProductNumber': 'C0603C0G500-1R5CNE-CT',
        'ProductUrl': '/en/products/detail/venkel/C0603C0G500-1R5CNE-CT/14652164',
        'Manufacturer': {'Name': 'Venkel'}, 'DigiKeyProductNumber': '2679-C0603C0G500-1R5CNE-CT-ND',
    }]}
    client = DigiKeyClient('id', 'secret', site='IT', session=FakeSession(
        FakeResponse({'access_token': 'T', 'expires_in': 599}), FakeResponse(data),
    ))
    client.min_interval = 0

    alternatives = client.substitutions('490-10710-1-ND')

    assert alternatives[0]['url'] == 'https://www.digikey.it/en/products/detail/venkel/C0603C0G500-1R5CNE-CT/14652164'
