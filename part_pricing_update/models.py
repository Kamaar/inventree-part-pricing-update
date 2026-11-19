"""Database models for the PartPricingUpdate plugin."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class PluginPermissionMixin:
    """Every authenticated user can read, only staff users can modify."""

    @classmethod
    def check_user_permission(cls, user, permission: str) -> bool:
        if not user or not user.is_authenticated:
            return False

        return permission == 'view' or user.is_staff


class PriceRecord(PluginPermissionMixin, models.Model):
    """A set of supplier price breaks, valid between 'first_seen' and 'last_seen'.

    A new record is created only when the prices change: while they stay the
    same, the latest record is extended by updating 'last_seen'.
    """

    class Meta:
        app_label = 'part_pricing_update'
        verbose_name = _('Price Record')
        verbose_name_plural = _('Price Records')
        ordering = ['supplier_part', 'first_seen']
        indexes = [models.Index(fields=['supplier_part', '-first_seen'], name='ppu_record_sp_date_idx')]

    supplier_part = models.ForeignKey(
        'company.SupplierPart',
        on_delete=models.CASCADE,
        related_name='pricing_history',
        verbose_name=_('Supplier Part'),
    )

    currency = models.CharField(max_length=3, verbose_name=_('Currency'))

    first_seen = models.DateTimeField(verbose_name=_('First seen'))

    last_seen = models.DateTimeField(verbose_name=_('Last seen'))

    unit_price = models.DecimalField(
        max_digits=19,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_('Unit price'),
        help_text=_('Price at the lowest quantity break'),
    )

    price_breaks = models.JSONField(
        default=list,
        verbose_name=_('Price breaks'),
        help_text=_('List of [quantity, price] pairs'),
    )

    stock = models.IntegerField(
        null=True,
        blank=True,
        verbose_name=_('Stock'),
        help_text=_('Supplier stock at the last check'),
    )

    def __str__(self):
        return f'{self.supplier_part.SKU} {self.unit_price} {self.currency} ({self.first_seen:%Y-%m-%d})'


class PricingStatus(PluginPermissionMixin, models.Model):
    """Last update attempt for a SupplierPart, used to schedule the next one."""

    class Meta:
        app_label = 'part_pricing_update'
        verbose_name = _('Pricing Status')
        verbose_name_plural = _('Pricing Status')

    supplier_part = models.OneToOneField(
        'company.SupplierPart',
        on_delete=models.CASCADE,
        related_name='pricing_status',
        verbose_name=_('Supplier Part'),
    )

    last_checked = models.DateTimeField(null=True, blank=True, verbose_name=_('Last checked'))

    last_success = models.DateTimeField(null=True, blank=True, verbose_name=_('Last success'))

    last_error = models.TextField(blank=True, default='', verbose_name=_('Last error'))

    # Fields added after 0.1.0 have a database default too, so that processes
    # still running the previous version can insert rows during an upgrade
    supplier_status = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_default='',
        verbose_name=_('Supplier status'),
        help_text=_('Product lifecycle status reported by the supplier (e.g. Active, Obsolete)'),
    )

    suggested_sku = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_default='',
        verbose_name=_('Suggested SKU'),
        help_text=_('Current supplier code, when the stored SKU is no longer valid'),
    )

    alternatives = models.JSONField(
        default=list,
        null=True,
        blank=True,
        verbose_name=_('Alternatives'),
        help_text=_('Replacement products suggested by the supplier'),
    )

    def __str__(self):
        return f'{self.supplier_part.SKU}: {self.last_error or "OK"}'


class SupplierApiUsage(PluginPermissionMixin, models.Model):
    """Number of API requests sent to a supplier in a UTC day."""

    class Meta:
        app_label = 'part_pricing_update'
        verbose_name = _('Supplier API Usage')
        verbose_name_plural = _('Supplier API Usage')
        unique_together = ('supplier', 'day')
        ordering = ['-day', 'supplier']

    supplier = models.CharField(max_length=20, verbose_name=_('Supplier'))

    day = models.DateField(verbose_name=_('Day (UTC)'))

    requests = models.PositiveIntegerField(default=0, verbose_name=_('Requests'))

    blocked_until = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Blocked until'),
        help_text=_('Set when the API reports that the request limit has been reached'),
    )

    def __str__(self):
        return f'{self.supplier} {self.day}: {self.requests}'
