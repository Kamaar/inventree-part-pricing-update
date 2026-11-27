"""Barcode scans of the supplier labels (Mouser, DigiKey, Farnell).

- The linear 'P/N' barcode: the supplier part number as plain text ('637-SMZ24'). Matched only
  when the text is exactly the SKU of one supplier part of the configured suppliers.
- The customer part number, which is the IPN of the part ('IPN-00042'): as a linear barcode, or
  as the 'P' field of the label DataMatrix (ISO/IEC 15434, Mouser: 'P' holds the customer part
  number, not the SKU). The part is opened; inside a purchase order the supplier part of the
  order supplier is received, with the quantity 'Q' of the DataMatrix.

Any other barcode (order number, quantity, ...) is left to the other barcode plugins.
"""

import re
from dataclasses import dataclass

MAX_LENGTH = 100

DATA_IDENTIFIER = re.compile(r'^(\d{0,3}[A-Z])(.*)$', re.DOTALL)


@dataclass
class ScanMatch:
    supplier_part: object = None
    part: object = None  # only for a customer part number (IPN): a SKU opens the supplier part
    quantity: str | None = None


def looks_like_code(text: str) -> bool:
    """A single printable token: not a 2D barcode (control characters) nor a sentence."""
    return bool(text) and len(text) <= MAX_LENGTH and not any(ord(c) < 32 for c in text) and ' ' not in text.strip()


def ecia_fields(text: str) -> dict[str, str] | None:
    """The fields of an ISO/IEC 15434 barcode ('[)>␞06␝P…␝Q20␞␄'), by data identifier; None if not one."""
    if not text.startswith('[)>'):
        return None

    fields = {}

    for chunk in re.split('[\x1d\x1e]', text[3:]):
        match = DATA_IDENTIFIER.match(chunk.strip('\x04\r\n'))

        if match:
            fields.setdefault(match.group(1), match.group(2).strip())

    return fields


def configured_companies(plugin) -> list:
    from .pricing import get_supplier_companies

    return [pk for pk in get_supplier_companies(plugin).values() if pk]


def match_supplier_part(plugin, barcode_data):
    """The only supplier part of Mouser / DigiKey / Farnell with this SKU, or None."""
    from company.models import SupplierPart

    text = str(barcode_data or '').strip()

    if not looks_like_code(text):
        return None

    companies = configured_companies(plugin)

    if not companies:
        return None

    matches = list(SupplierPart.objects.filter(supplier__in=companies, SKU__iexact=text).select_related('supplier')[:2])
    return matches[0] if len(matches) == 1 else None


def match_ipn(code: str):
    """The only part with this IPN (customer part number on the supplier labels), or None."""
    from part.models import Part

    if not looks_like_code(code):
        return None

    matches = list(Part.objects.filter(IPN__iexact=code)[:2])
    return matches[0] if len(matches) == 1 else None


def supplier_part_of(part, supplier):
    """The only supplier part of the part from this supplier (the one of the order being received)."""
    if part is None or supplier is None:
        return None

    matches = list(part.supplier_parts.filter(supplier=supplier).select_related('supplier')[:2])
    return matches[0] if len(matches) == 1 else None


def match_scan(plugin, barcode_data, supplier=None) -> ScanMatch | None:
    """What a scanned label barcode refers to; supplier: the supplier of the order being received."""
    text = str(barcode_data or '').strip()
    fields = ecia_fields(text)

    if fields is not None:
        # DataMatrix of the label: the customer part number (IPN) in 'P'
        part = match_ipn(fields.get('P') or '')

        if part is None:
            return None

        return ScanMatch(supplier_part_of(part, supplier), part, fields.get('Q') or None)

    if (sp := match_supplier_part(plugin, text)) is not None:
        return ScanMatch(supplier_part=sp)

    if (part := match_ipn(text)) is not None:
        return ScanMatch(supplier_part_of(part, supplier), part)

    return None
