"""API views for the PartPricingUpdate plugin.

All the endpoints are served below /plugin/part-pricing-update/
"""

from django.shortcuts import get_object_or_404

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import pricing
from .models import PriceRecord, PricingStatus
from .suppliers import is_discontinued, is_packaging_unavailable


# The supplier prices belong to the 'purchase order' role of InvenTree (SupplierPriceBreak):
# the same permissions are required to see them and their history, and to update them
VIEW_PRICES = 'company.view_supplierpricebreak'
CHANGE_PRICES = 'company.change_supplierpricebreak'


def can_view_prices(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.has_perm(VIEW_PRICES)))


def can_update_prices(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.has_perm(CHANGE_PRICES)))


class CanUpdatePricing(permissions.BasePermission):
    """Read access for the users who can see the supplier prices, updates for those who can edit them."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return can_view_prices(request.user)

        return can_update_prices(request.user)


class CanViewSupplierParts(permissions.BasePermission):
    """Read access for the users who can see the supplier parts."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or user.has_perm('company.view_supplierpart')))


def _date(value):
    return value.isoformat() if value else None


def serialize_supplier_part(sp, supplier_key: str) -> dict:
    """Return current pricing, update status and history of a SupplierPart.

    The plugin models are queried directly (not through the reverse relations
    of SupplierPart), see pricing.due_supplier_parts().
    """
    status = PricingStatus.objects.filter(supplier_part=sp).first()

    return {
        'pk': sp.pk,
        'sku': sp.SKU,
        'supplier': {'pk': sp.supplier.pk, 'name': sp.supplier.name},
        'supplier_key': supplier_key,
        'link': sp.link,
        'available': float(sp.available) if sp.available is not None else None,
        'availability_updated': _date(sp.availability_updated),
        'price_breaks': [
            {
                'quantity': float(pb.quantity),
                'price': str(pb.price.amount),
                'currency': str(pb.price.currency),
            }
            for pb in sp.pricebreaks.order_by('quantity')
            if pb.price is not None
        ],
        'status': {
            'last_checked': _date(status.last_checked),
            'last_success': _date(status.last_success),
            'last_error': status.last_error,
            'supplier_status': status.supplier_status,
            'discontinued': is_discontinued(status.supplier_status),
            'no_price': is_packaging_unavailable(status.supplier_status),
            'suggested_sku': status.suggested_sku,
            'alternatives': status.alternatives or [],
        } if status else None,
        'history': [
            {
                'first_seen': _date(r.first_seen),
                'last_seen': _date(r.last_seen),
                'currency': r.currency,
                'unit_price': str(r.unit_price) if r.unit_price is not None else None,
                'price_breaks': r.price_breaks,
                'stock': r.stock,
            }
            for r in PriceRecord.objects.filter(supplier_part=sp).order_by('first_seen')
        ],
    }


class PricingMixin:
    """Helpers shared by the part / supplier part views."""

    permission_classes = [CanUpdatePricing]

    def get_supplier_parts(self, plugin, pk):
        raise NotImplementedError

    def get(self, request, pk, *args, **kwargs):
        plugin = pricing.get_plugin()
        companies = pricing.get_supplier_companies(plugin)
        key_by_company = {cpk: key for key, cpk in companies.items() if cpk}
        supplier_parts = self.get_supplier_parts(plugin, pk)

        return Response({
            'configured': sorted(pricing.build_clients(plugin).keys()),
            'supplier_parts': [
                serialize_supplier_part(sp, key_by_company.get(sp.supplier_id, ''))
                for sp in supplier_parts
            ],
        })

    def post(self, request, pk, *args, **kwargs):
        plugin = pricing.get_plugin()
        summary = pricing.update_supplier_parts(
            plugin, pricing.interleave_by_supplier(self.get_supplier_parts(plugin, pk))
        )
        return Response(summary)


class PartPricingView(PricingMixin, APIView):
    """Pricing data of all the supported SupplierParts of a Part.

    GET: current prices and history
    POST: fetch new prices from the suppliers now
    """

    def get_supplier_parts(self, plugin, pk):
        from part.models import Part

        part = get_object_or_404(Part, pk=pk)
        return pricing.supported_supplier_parts(plugin).filter(part=part)


class SupplierPartPricingView(PricingMixin, APIView):
    """Pricing data of a single SupplierPart (same methods as PartPricingView)."""

    def get_supplier_parts(self, plugin, pk):
        from company.models import SupplierPart

        sp = get_object_or_404(SupplierPart, pk=pk)
        return pricing.supported_supplier_parts(plugin).filter(pk=sp.pk)


class DiscontinuedView(APIView):
    """Supplier parts which the supplier reports as discontinued (or being discontinued).

    GET: one row per supplier part, with the stock of the part, the assemblies which use it
    and the other supplier parts of the same part which are still available
    """

    permission_classes = [CanViewSupplierParts]

    def get(self, request, *args, **kwargs):
        from django.db.models import Count

        from company.models import SupplierPart
        from part.filters import annotate_total_stock
        from part.models import BomItem

        plugin = pricing.get_plugin()
        companies = pricing.get_supplier_companies(plugin)
        key_by_company = {cpk: key for key, cpk in companies.items() if cpk}

        # The status is free text from the supplier: the keyword check is done in Python
        statuses = {
            status.supplier_part_id: status
            for status in PricingStatus.objects.exclude(supplier_status='')
            if is_discontinued(status.supplier_status)
        }

        supplier_parts = list(
            SupplierPart.objects.filter(pk__in=statuses.keys())
            .select_related('part', 'supplier', 'manufacturer_part__manufacturer')
            .annotate(in_stock=annotate_total_stock(reference='part__'))
            .order_by('part__IPN', 'part__name', 'supplier__name')
        )
        part_ids = {sp.part_id for sp in supplier_parts}

        used_in = dict(
            BomItem.objects.filter(sub_part__in=part_ids)
            .values('sub_part')
            .annotate(n=Count('part', distinct=True))
            .values_list('sub_part', 'n')
        )

        # Other sources: the active supplier parts of the same part not reported as discontinued
        other_sources = {}

        for sp in SupplierPart.objects.filter(part__in=part_ids, active=True).exclude(
            pk__in=statuses.keys()
        ).select_related('supplier'):
            other_sources.setdefault(sp.part_id, []).append({
                'pk': sp.pk,
                'sku': sp.SKU,
                'supplier': sp.supplier.name,
                'available': float(sp.available) if sp.available is not None else None,
            })

        rows = []
        # The prices of the alternatives only for the users who can see the supplier prices
        show_prices = can_view_prices(request.user)

        for sp in supplier_parts:
            status = statuses[sp.pk]
            part = sp.part
            mp = sp.manufacturer_part

            rows.append({
                'pk': sp.pk,
                'sku': sp.SKU,
                'link': sp.link,
                'supplier': {'pk': sp.supplier.pk, 'name': sp.supplier.name},
                'supplier_key': key_by_company.get(sp.supplier_id, ''),
                'mpn': mp.MPN if mp else '',
                'manufacturer': mp.manufacturer.name if mp and mp.manufacturer else '',
                'part': {
                    'pk': part.pk,
                    'name': part.name,
                    'IPN': part.IPN,
                    'description': part.description,
                    'active': part.active,
                },
                'in_stock': float(sp.in_stock or 0),
                'used_in': used_in.get(part.pk, 0),
                'supplier_status': status.supplier_status,
                'suggested_sku': status.suggested_sku,
                'alternatives': [
                    alt if show_prices else {**alt, 'unit_price': ''} for alt in status.alternatives or []
                ],
                'last_checked': _date(status.last_checked),
                'other_sources': other_sources.get(part.pk, []),
            })

        return Response({'results': rows})


class StatusView(APIView):
    """Overview for the plugin settings page.

    GET: supplier configuration and update statistics
    POST: queue an update of all the supported SupplierParts
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, request, *args, **kwargs):
        from company.models import Company

        plugin = pricing.get_plugin()
        companies = pricing.get_supplier_companies(plugin)
        clients = pricing.build_clients(plugin)
        queryset = pricing.supported_supplier_parts(plugin, companies)
        due = pricing.due_supplier_parts(plugin)
        failing = PricingStatus.objects.filter(supplier_part__in=queryset).exclude(last_error='')

        suppliers = []

        for key, cpk in companies.items():
            company = Company.objects.filter(pk=cpk).first() if cpk else None
            parts = queryset.filter(supplier__pk=cpk) if cpk else queryset.none()

            suppliers.append({
                'key': key,
                'company': {'pk': company.pk, 'name': company.name} if company else None,
                'configured': key in clients,
                'supplier_parts': parts.count(),
                'due': due.filter(supplier__pk=cpk).count() if cpk else 0,
                'errors': failing.filter(supplier_part__supplier__pk=cpk).count() if cpk else 0,
                'discontinued': sum(
                    1
                    for value in PricingStatus.objects.filter(
                        supplier_part__in=parts
                    ).exclude(supplier_status='').values_list('supplier_status', flat=True)
                    if is_discontinued(value)
                ),
                'requests_today': pricing.get_usage(key).requests,
                'daily_limit': pricing.daily_limit(plugin, key),
                'blocked_until': _date(pricing.blocked_until(key)),
            })

        errors = [
            {
                'pk': status.supplier_part.pk,
                'sku': status.supplier_part.SKU,
                'supplier': status.supplier_part.supplier.name,
                'part': status.supplier_part.part.full_name,
                'part_pk': status.supplier_part.part.pk,
                'error': status.last_error,
                'last_checked': _date(status.last_checked),
            }
            for status in failing.select_related(
                'supplier_part__supplier', 'supplier_part__part'
            ).order_by('-last_checked')[:50]
        ]

        return Response({'suppliers': suppliers, 'errors': errors})

    def post(self, request, *args, **kwargs):
        from InvenTree.tasks import offload_task

        offload_task('part_pricing_update.pricing.update_all', force_async=True)
        return Response({'queued': True})
