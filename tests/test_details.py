"""Tests for search / details, on real API responses saved in tests/fixtures.

The fixtures were captured from the live APIs (Mouser, DigiKey IT/EUR,
Farnell it.farnell.com) and contain no credentials.
"""

import json
import pathlib
from decimal import Decimal

import pytest

from part_pricing_update.suppliers import DigiKeyClient, FarnellClient, MouserClient, NotFoundError

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'


def fixture(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.headers = {}
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
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


def client(cls, *responses, **kwargs):
    c = cls(*kwargs.pop('args', ()), session=FakeSession(*responses), **kwargs)
    c.min_interval = 0
    return c


DIGIKEY_TOKEN = FakeResponse({'access_token': 'T', 'expires_in': 599})


# ---------------------------------------------------------------- Mouser


def test_mouser_details():
    c = client(MouserClient, FakeResponse(fixture('mouser_partnumber_595-NE555P')), args=('key',))

    d = c.details('595-NE555P')

    assert (d.supplier, d.sku, d.mpn, d.manufacturer) == ('mouser', '595-NE555P', 'NE555P', 'Texas Instruments')
    assert d.category == ['Timers & Support Products']
    assert d.packaging == 'Tube'
    assert d.image_url.startswith('https://')
    assert d.parameters['Packaging'] == 'Tube'
    assert d.rohs == 'Conforme'
    assert d.quote.currency == 'EUR'
    assert d.quote.price_breaks[0].quantity == 1


def test_mouser_search():
    c = client(MouserClient, FakeResponse(fixture('mouser_keyword_NE555P')), args=('key',))

    results = c.search('NE555P', limit=3)

    assert results
    assert all(r.sku != 'N/A' for r in results)
    assert results[0].mpn.startswith('NE555')
    assert results[0].currency == 'EUR'
    body = c.session.requests[0][2]['json']['SearchByKeywordRequest']
    assert body['keyword'] == 'NE555P' and body['records'] == 3


def test_mouser_find_by_mpn():
    c = client(MouserClient, FakeResponse(fixture('mouser_partnumber_595-NE555P')), args=('key',))

    results = c.find_by_mpn('ne555p')

    assert [r.sku for r in results] == ['595-NE555P']


# ---------------------------------------------------------------- DigiKey


def test_digikey_details():
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_productdetails_296-NE555P-ND')),
        args=('id', 'secret'),
    )

    d = c.details('296-NE555P-ND')

    assert (d.supplier, d.sku, d.mpn, d.manufacturer) == ('digikey', '296-NE555P-ND', 'NE555P', 'Texas Instruments')
    assert d.category == ['Integrated Circuits (ICs)', 'Clock/Timing', 'Programmable Timers and Oscillators']
    assert d.description == 'IC OSC SINGLE TIMER 100KHZ 8-DIP'
    assert d.packaging == 'Tube'
    assert d.datasheet_url.startswith('https://')
    assert d.image_url.startswith('https://')
    assert d.parameters['Frequency'] == '100kHz'
    assert d.parameters['Mounting Type'] == 'Through Hole'
    assert d.quote.status == 'Active'
    assert d.quote.price_breaks[0].price == Decimal('0.52')


def test_digikey_search_one_result_per_packaging():
    c = client(DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_keyword_NE555P')), args=('id', 'secret'))

    results = c.search('NE555P', limit=3)

    skus = [r.sku for r in results]
    assert '296-NE555P-ND' in skus
    assert len(skus) == len(set(skus))  # ExactMatches and Products overlap
    assert all(r.mpn and r.packaging for r in results)
    assert c.session.requests[1][2]['json'] == {'Keywords': 'NE555P', 'Limit': 3}


def test_digikey_search_reports_old_mpn_of_renamed_product():
    # KYOCERA AVX renamed 0402ZD224KAT2A to KGM05AR51A224KH: the old MPN is only in 'OtherNames'
    c = client(DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_keyword_0402ZD224KAT2A')), args=('id', 'secret'))

    results = c.search('0402ZD224KAT2A')

    assert results and all(r.mpn == 'KGM05AR51A224KH' for r in results)
    assert '0402ZD224KAT2A' in results[0].aliases
    assert 'KGM05AR51A224KH' not in results[0].aliases


def test_digikey_find_by_mpn_exact():
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_productdetails_296-NE555P-ND')),
        args=('id', 'secret'),
    )

    results = c.find_by_mpn('NE555P')

    assert [r.sku for r in results] == ['296-NE555P-ND']


def test_digikey_find_by_mpn_falls_back_to_keyword():
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN, FakeResponse({}, status_code=404), FakeResponse(fixture('digikey_keyword_NE555P')),
        args=('id', 'secret'),
    )

    results = c.find_by_mpn('NE555P')

    assert results and all(r.mpn == 'NE555P' for r in results)


# ---------------------------------------------------------------- Farnell


def test_farnell_details():
    c = client(FarnellClient, FakeResponse(fixture('farnell_id_2288284')), args=('key',), store='it.farnell.com')

    d = c.details('2288284')

    assert (d.supplier, d.sku, d.mpn) == ('farnell', '2288284', 'MSS1048-822NLC')
    assert d.manufacturer.upper() == 'COILCRAFT'
    assert d.datasheet_url.startswith('https://')
    assert d.image_url.startswith('https://it.farnell.com/productimages/')
    # it.farnell.com answers in Italian
    assert d.parameters['Induttanza'].startswith('8.2')
    assert d.quote.currency == 'EUR'
    assert d.quote.price_breaks[0].price == Decimal('2.42')
    # No category in the API: the product type of the name (in the store language)
    assert d.category == ['Induttore di potenza (SMD)']


@pytest.mark.parametrize('name, mpn, expected', [
    ('COILCRAFT - MSS1048-822NLC - Power Inductor (SMD), 8.2µH, 5.4A', 'MSS1048-822NLC', 'Power Inductor (SMD)'),
    # A brand with ' - ' in its name: the MPN marks the start of the type
    ('NEOHM - TE CONNECTIVITY - CRGCQ0603F10K - SMD Chip Resistor, 10 kohm', 'CRGCQ0603F10K', 'SMD Chip Resistor'),
    ('MURATA - GRM188R71H104KA93D - SMD Multilayer Ceramic Capacitor, 0.1 µF', '', 'SMD Multilayer Ceramic Capacitor'),
    ('Something without separators', 'X', ''),
])
def test_farnell_product_type(name, mpn, expected):
    product = {'displayName': name, 'translatedManufacturerPartNumber': mpn}

    assert FarnellClient.product_type(product) == expected


def test_farnell_find_by_mpn():
    c = client(FarnellClient, FakeResponse(fixture('farnell_mpn_MSS1048-822NLC')), args=('key',), store='it.farnell.com')

    results = c.find_by_mpn('MSS1048-822NLC')

    assert results and all(r.mpn == 'MSS1048-822NLC' for r in results)
    assert c.session.requests[0][2]['params']['term'] == 'manuPartNum:MSS1048-822NLC'


def test_details_placeholder_sku():
    with pytest.raises(NotFoundError):
        MouserClient('key', session=FakeSession()).details('N/A')


# ---------------------------------------------------------------- RoHS / REACH


def test_compliance_from_real_responses():
    mouser = client(MouserClient, FakeResponse(fixture('mouser_partnumber_595-NE555P')), args=('key',))
    digikey = client(
        DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_productdetails_296-NE555P-ND')),
        args=('id', 'secret'),
    )
    farnell = client(FarnellClient, FakeResponse(fixture('farnell_id_2288284')), args=('key',), store='it.farnell.com')

    m = mouser.details('595-NE555P')
    d = digikey.details('296-NE555P-ND')
    f = farnell.details('2288284')

    # Same values for every supplier
    assert (m.rohs, m.reach) == ('Conforme', '')  # Mouser does not report REACH
    assert (d.rohs, d.reach) == ('Conforme (RoHS3)', 'Conforme (nessuna SVHC)')
    assert f.rohs == 'Conforme (RoHS3)'  # rohsCompliant + rohsPhthalatesCompliant
    assert f.reach == 'Conforme (nessuna SVHC al 25-Jun-2025)'

    # The raw supplier data stays in the parameters
    assert f.parameters['SVHC'].startswith('No SVHC')
    assert m.parameters['Country of Origin'] == 'Mexico'


def test_digikey_environmental_documents():
    media = {
        'MediaLinks': [
            {'MediaType': 'Datasheets', 'Title': 'NE555', 'Url': 'https://www.ti.com/lit/ds/na555.pdf'},
            {
                'MediaType': 'Environmental Information',
                'Title': 'RoHS ERJ Series',
                'Url': '//industrial.panasonic.com/cdbs/www-cgi/auto-publishing.cgi?type=ROSU&region=WW&cd=AOA0002',
            },
        ]
    }
    c = client(DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(media), args=('id', 'secret'))

    docs = c.environmental_documents('10-ERJ-P06F82R0VCT-ND')

    assert docs == [{
        'title': 'RoHS ERJ Series',
        'url': 'https://industrial.panasonic.com/cdbs/www-cgi/auto-publishing.cgi?type=ROSU&region=WW&cd=AOA0002',
    }]
    assert c.session.requests[1][1].endswith('/search/10-ERJ-P06F82R0VCT-ND/media')


def test_mouser_search_not_sold():
    """Real Mouser answer for 885012107019 (Würth): in the catalog, 'N/A' part number."""
    data = {'Errors': [], 'SearchResults': {'NumberOfResult': 1, 'Parts': [
        {'MouserPartNumber': 'N/A', 'ManufacturerPartNumber': '885012107019', 'Manufacturer': 'Wurth Elektronik', 'PriceBreaks': []},
    ]}}
    c = client(MouserClient, FakeResponse(data), args=('key',))

    results = c.search('885012107019')

    assert [(r.sku, r.mpn, r.status) for r in results] == [('', '885012107019', 'Not sold by Mouser')]


def test_digikey_search_falls_back_to_exact_code():
    """The keyword search finds nothing for some exact codes: productdetails is tried."""
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN,
        FakeResponse({'ExactMatches': [], 'Products': [], 'SearchLocaleUsed': {}}),
        FakeResponse(fixture('digikey_productdetails_296-NE555P-ND')),
        args=('id', 'secret'),
    )

    results = c.search('296-NE555P-ND')

    assert [r.sku for r in results] == ['296-NE555P-ND']
    assert c.session.requests[2][1].endswith('/search/296-NE555P-ND/productdetails')


def test_digikey_search_nothing_found():
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN,
        FakeResponse({'ExactMatches': [], 'Products': []}),
        FakeResponse({}, status_code=404),
        args=('id', 'secret'),
    )

    assert c.search('885012107019') == []


# ---------------------------------------------------------------- Mouser: products without prices, server errors

MOUSER_EMPTY = {'Errors': [], 'SearchResults': {'NumberOfResult': 0, 'Parts': []}}


def test_mouser_not_sold_in_region_is_a_status():
    # 'Mouser does not presently sell this product in your region' (real response, mouser.it)
    c = client(MouserClient, FakeResponse(fixture('mouser_partnumber_771-PESD12VL1BA-T_R')), args=('key',))

    quote = c.fetch('771-PESD12VL1BA-T/R')

    from part_pricing_update.suppliers import is_discontinued

    assert quote.price_breaks == []
    assert quote.status == 'Not sold by Mouser in your region'
    assert is_discontinued(quote.status)
    assert {'type': 'Alternate packaging', 'mpn': 'PESD12VL1BAF'}.items() <= quote.alternatives[0].items()


def test_mouser_sku_and_mpn_not_found_is_removed_product():
    c = client(MouserClient, FakeResponse(MOUSER_EMPTY), FakeResponse(MOUSER_EMPTY), args=('key',))

    quote = c.fetch('584-ADF7021-VBCPZ', 'ADF7021-VBCPZ')

    assert quote.status == 'Not available at Mouser'
    assert quote.price_breaks == []
    # One request for the SKU, one for the MPN
    assert len(c.session.requests) == 2


def test_mouser_sku_not_found_uses_the_mpn():
    c = client(MouserClient, FakeResponse(MOUSER_EMPTY), FakeResponse(fixture('mouser_partnumber_595-NE555P')), args=('key',))

    quote = c.fetch('595-OLD-NE555P', 'NE555P')

    # The new Mouser number is reported, and becomes the suggested SKU
    assert quote.sku == '595-NE555P'
    assert quote.price_breaks


def test_mouser_sku_not_found_without_mpn_is_an_error():
    c = client(MouserClient, FakeResponse(MOUSER_EMPTY), args=('key',))

    with pytest.raises(NotFoundError):
        c.fetch('595-OLD-NE555P')


def test_server_error_is_temporary():
    from part_pricing_update.suppliers import TemporaryError

    page = FakeResponse(None, status_code=503)
    page.text = '<HTML><HEAD><TITLE>Error</TITLE></HEAD><BODY> An error occurred while processing your request.'
    c = client(MouserClient, page, args=('key',))

    with pytest.raises(TemporaryError) as exc:
        c.fetch('81-GRM39C331J50')

    assert 'HTTP 503' in str(exc.value)
    assert '<' not in str(exc.value)


def test_digikey_no_price_in_this_packaging():
    # Active, not stocked: only the full reel has a price, not the cut tape of the SKU (real response)
    c = client(
        DigiKeyClient, DIGIKEY_TOKEN, FakeResponse(fixture('digikey_productdetails_118-TBU-DF055-500-WHCT-ND')),
        args=('id', 'secret'),
    )

    quote = c.fetch('118-TBU-DF055-500-WHCT-ND', 'TBU-DF055-500-WH')

    from part_pricing_update.suppliers import is_discontinued, is_packaging_unavailable

    assert quote.price_breaks == []
    assert is_packaging_unavailable(quote.status) and not is_discontinued(quote.status)
    assert 'Cut Tape' in quote.status and 'Tape & Reel' in quote.status
    assert [a['sku'] for a in quote.alternatives] == ['118-TBU-DF055-500-WHTR-ND']
    assert 'min 6000' in quote.alternatives[0]['type']
    # No request for the substitutions: the product is sold in another packaging
    assert len(c.session.requests) == 2


FARNELL_EMPTY = {'premierFarnellPartNumberReturn': {'numberOfResults': 0}}


def test_farnell_code_and_mpn_not_found_is_removed_product():
    c = client(
        FarnellClient, FakeResponse(FARNELL_EMPTY), FakeResponse({'manufacturerPartNumberSearchReturn': {'numberOfResults': 0}}),
        args=('key',), store='it.farnell.com',
    )

    quote = c.fetch('4239396', 'MCX-PLUG-TEST')

    from part_pricing_update.suppliers import is_discontinued

    assert quote.status == 'Not available at Farnell' and is_discontinued(quote.status)
    assert c.session.requests[1][2]['params']['term'] == 'manuPartNum:MCX-PLUG-TEST'


def test_farnell_code_not_found_uses_the_mpn():
    c = client(
        FarnellClient, FakeResponse(FARNELL_EMPTY), FakeResponse(fixture('farnell_mpn_MSS1048-822NLC')),
        args=('key',), store='it.farnell.com',
    )

    quote = c.fetch('9999999', 'MSS1048-822NLC')

    # The current order code is reported (suggested SKU) with its prices
    assert quote.sku and quote.sku != '9999999'
    assert quote.price_breaks


def test_farnell_code_not_found_without_mpn_is_an_error():
    c = client(FarnellClient, FakeResponse(FARNELL_EMPTY), args=('key',), store='it.farnell.com')

    with pytest.raises(NotFoundError):
        c.fetch('4239396')
