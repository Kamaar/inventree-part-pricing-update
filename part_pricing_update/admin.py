"""Admin site configuration for the PartPricingUpdate plugin."""

from django.contrib import admin

from .models import PriceRecord, PricingStatus, SupplierApiUsage


@admin.register(PriceRecord)
class PriceRecordAdmin(admin.ModelAdmin):
    """Admin interface for the price history."""

    list_display = ('supplier_part', 'unit_price', 'currency', 'first_seen', 'last_seen', 'stock')
    list_filter = ('supplier_part__supplier', 'currency')
    search_fields = ('supplier_part__SKU', 'supplier_part__part__name', 'supplier_part__part__IPN')
    raw_id_fields = ('supplier_part',)


@admin.register(PricingStatus)
class PricingStatusAdmin(admin.ModelAdmin):
    """Admin interface for the update status."""

    list_display = ('supplier_part', 'last_checked', 'last_success', 'supplier_status', 'suggested_sku', 'last_error')
    list_filter = ('supplier_part__supplier',)
    search_fields = ('supplier_part__SKU', 'last_error')
    raw_id_fields = ('supplier_part',)


@admin.register(SupplierApiUsage)
class SupplierApiUsageAdmin(admin.ModelAdmin):
    """Admin interface for the API request counters."""

    list_display = ('supplier', 'day', 'requests', 'blocked_until')
    list_filter = ('supplier',)
