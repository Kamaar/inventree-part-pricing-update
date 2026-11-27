"""Tests for the parsing of the scanned label barcodes (no InvenTree needed)."""

from part_pricing_update.barcode import ecia_fields, looks_like_code

MOUSER = '[)>\x1e06\x1dK367\x1d14K002\x1dPIPN-00042\x1dQ5\x1d11K093755626\x1d4LCN\x1d1VDiotec\x1e\x04'


def test_mouser_datamatrix_fields():
    fields = ecia_fields(MOUSER)
    assert fields['P'] == 'IPN-00042'
    assert fields['Q'] == '5'
    assert fields['K'] == '367'
    assert fields['14K'] == '002'
    assert fields['11K'] == '093755626'
    assert fields['1V'] == 'Diotec'


def test_not_a_datamatrix():
    assert ecia_fields('637-SMZ24') is None
    assert ecia_fields('IPN-00042') is None


def test_linear_codes():
    assert looks_like_code('637-SMZ24')
    assert looks_like_code('IPN-00042')
    assert not looks_like_code(MOUSER)
    assert not looks_like_code('two words')
    assert not looks_like_code('')
