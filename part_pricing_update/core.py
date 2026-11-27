"""Update supplier part prices from Mouser, DigiKey and Farnell, keeping a price history."""

from django.utils.translation import gettext_lazy as _

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, ScheduleMixin, SettingsMixin, SupplierBarcodeMixin, UrlsMixin, UserInterfaceMixin

from . import PLUGIN_DATE, PLUGIN_VERSION
from .suppliers import FARNELL_STORES


def _supplier_setting(name: str) -> dict:
    return {
        'name': _('{name} supplier').format(name=name),
        'description': _(
            'Company linked to {name}. If empty, a supplier whose name contains "{name}" is used'
        ).format(name=name),
        'model': 'company.company',
        'model_filters': {'is_supplier': True},
    }


class PartPricingUpdate(
    AppMixin, ScheduleMixin, SettingsMixin, SupplierBarcodeMixin, UrlsMixin, UserInterfaceMixin, InvenTreePlugin
):
    """PartPricingUpdate - keep supplier prices up to date."""

    # Plugin metadata
    TITLE = 'Part Pricing Update'
    NAME = 'PartPricingUpdate'
    SLUG = 'part-pricing-update'
    DESCRIPTION = 'Update supplier part prices from Mouser, DigiKey and Farnell and keep a price history'
    VERSION = PLUGIN_VERSION
    PUBLISH_DATE = PLUGIN_DATE
    WEBSITE = 'https://github.com/Kamaar/inventree-part-pricing-update'

    AUTHOR = 'Simone Amadori'
    LICENSE = 'MIT'

    MIN_VERSION = '1.0.0'
    # AppMixin plugin: not loaded on a new major InvenTree version until it has been checked
    # (migrations and core models may change, e.g. the pricing changes planned for 2.0)
    MAX_VERSION = '1.99.99'

    # Custom UI on the plugin settings page
    ADMIN_SOURCE = 'Settings.js:renderPricingSettings'

    # The task runs often but processes only the parts which are due, in small
    # batches: the daily quota of each API is spread over the runs of the day
    # (see quota.run_allowance), and each run stays below the worker timeout
    SCHEDULED_TASKS = {
        'update_prices': {
            'func': 'scheduled_update',
            'schedule': 'I',
            'minutes': 15,
        },
    }

    SETTINGS = {
        'AUTO_UPDATE': {
            'name': _('Automatic update'),
            'description': _('Periodically update the prices in the background'),
            'validator': bool,
            'default': True,
        },
        'UPDATE_INTERVAL_DAYS': {
            'name': _('Update interval (days)'),
            'description': _('Minimum number of days between two updates of the same supplier part'),
            'validator': int,
            'default': 7,
        },
        'PARTS_PER_RUN': {
            'name': _('Maximum requests per run'),
            'description': _(
                'Maximum number of supplier parts updated for each supplier by a single run '
                'of the scheduled task (every 15 minutes)'
            ),
            'validator': int,
            'default': 20,
        },
        'MAX_RUN_SECONDS': {
            'name': _('Maximum run time (seconds)'),
            'description': _('Stop each run after this time, to stay below the background worker timeout'),
            'validator': int,
            'default': 60,
        },
        'UPDATE_PRICE_BREAKS': {
            'name': _('Update price breaks'),
            'description': _('Replace the supplier part price breaks with the ones from the supplier'),
            'validator': bool,
            'default': True,
        },
        'UPDATE_AVAILABILITY': {
            'name': _('Update availability'),
            'description': _('Update the quantity available from the supplier'),
            'validator': bool,
            'default': True,
        },
        'CLEAR_UNAVAILABLE_PRICES': {
            'name': _('Remove prices of discontinued products'),
            'description': _(
                'Remove the price breaks (and set the availability to 0) when the supplier reports '
                'the product as discontinued and gives no prices. The removed prices are kept in the history'
            ),
            'validator': bool,
            'default': True,
        },
        'MOUSER_SUPPLIER': _supplier_setting('Mouser'),
        'MOUSER_API_KEY': {
            'name': _('Mouser Search API key'),
            'description': _('Key of the Mouser Search API (not the cart API key)'),
            'protected': True,
            'default': '',
        },
        'MOUSER_DAILY_LIMIT': {
            'name': _('Mouser daily request limit'),
            'description': _(
                'Requests per day (UTC) sent to the Mouser API by this plugin. The API allows 1000: '
                'keep a margin if the same key is used by other tools (e.g. Ki-nTree)'
            ),
            'validator': int,
            'default': 800,
        },
        'DIGIKEY_SUPPLIER': _supplier_setting('DigiKey'),
        'DIGIKEY_CLIENT_ID': {
            'name': _('DigiKey Client ID'),
            'description': _('Client ID of a DigiKey production app subscribed to Product Information V4'),
            'protected': True,
            'default': '',
        },
        'DIGIKEY_CLIENT_SECRET': {
            'name': _('DigiKey Client Secret'),
            'description': _('Client secret of the DigiKey app'),
            'protected': True,
            'default': '',
        },
        'DIGIKEY_SITE': {
            'name': _('DigiKey site'),
            'description': _('Country code of the DigiKey site (e.g. IT, DE, US)'),
            'default': 'IT',
        },
        'DIGIKEY_LANGUAGE': {
            'name': _('DigiKey language'),
            'description': _('Language code (e.g. en, it)'),
            'default': 'en',
        },
        'DIGIKEY_CURRENCY': {
            'name': _('DigiKey currency'),
            'description': _('Currency of the prices (e.g. EUR, USD)'),
            'default': 'EUR',
        },
        'DIGIKEY_DAILY_LIMIT': {
            'name': _('DigiKey daily request limit'),
            'description': _(
                'Requests per day (UTC) sent to the DigiKey API by this plugin. The API allows 1000: '
                'keep a margin if the same key is used by other tools (e.g. Ki-nTree)'
            ),
            'validator': int,
            'default': 800,
        },
        'FARNELL_SUPPLIER': _supplier_setting('Farnell'),
        'FARNELL_API_KEY': {
            'name': _('Farnell API key'),
            'description': _('Key of the element14 Product Search API'),
            'protected': True,
            'default': '',
        },
        'FARNELL_STORE': {
            'name': _('Farnell store'),
            'description': _('Store used for the prices (it also defines the currency)'),
            'choices': [
                (store, f'{label} ({store}, {currency})')
                for store, (label, currency) in FARNELL_STORES.items()
            ],
            'default': 'it.farnell.com',
        },
        'FARNELL_DAILY_LIMIT': {
            'name': _('Farnell daily request limit'),
            'description': _(
                'Requests per day (UTC) sent to the Farnell API by this plugin. The API allows 1000: '
                'keep a margin if the same key is used by other tools (e.g. Ki-nTree)'
            ),
            'validator': int,
            'default': 800,
        },
    }

    # ---------------------------------------------------------------- barcode scans

    def extract_barcode_fields(self, barcode_data: str) -> dict[str, str]:
        """A scanned supplier SKU ('637-SMZ24') or customer part number (IPN), also in a label DataMatrix."""
        from .barcode import match_scan

        self._barcode_match = match = match_scan(self, barcode_data, getattr(self, '_receive_supplier', None))
        fields = {}

        if match is not None and match.supplier_part is not None:
            fields[self.SUPPLIER_PART_NUMBER] = match.supplier_part.SKU

        if match is not None and match.quantity:
            fields[self.QUANTITY] = match.quantity

        return fields

    def _matched_supplier_part(self):
        match = getattr(self, '_barcode_match', None)
        return match.supplier_part if match is not None else None

    def get_supplier(self, cache: bool = False):
        """The supplier of the matched supplier part (Mouser, DigiKey or Farnell)."""
        sp = self._matched_supplier_part()
        return sp.supplier if sp is not None else None

    def get_part(self):
        """The part of a scanned customer part number (IPN)."""
        match = getattr(self, '_barcode_match', None)
        return match.part if match is not None else None

    def get_supplier_part(self):
        return self._matched_supplier_part()

    def get_manufacturer_part(self):
        sp = self._matched_supplier_part()
        return sp.manufacturer_part if sp is not None else None

    def get_purchase_order(self):
        # The label says nothing reliable about the order: the order is the one being received (if any)
        return None

    def scan_receive_item(self, barcode_data: str, user, **kwargs):
        """Inside a purchase order, a customer part number (IPN) selects the supplier part of the order supplier."""
        order = kwargs.get('purchase_order')
        self._receive_supplier = kwargs.get('supplier') or getattr(order, 'supplier', None)

        try:
            return super().scan_receive_item(barcode_data, user, **kwargs)
        finally:
            self._receive_supplier = None

    def scheduled_update(self, *args, **kwargs):
        """Scheduled task: update a batch of supplier parts."""
        if not self.get_setting('AUTO_UPDATE'):
            return

        from .pricing import update_batch

        update_batch(self)

    def setup_urls(self):
        """API endpoints, served below /plugin/part-pricing-update/."""
        from django.urls import path

        from .views import DiscontinuedView, PartPricingView, StatusView, SupplierPartPricingView

        return [
            path('part/<int:pk>/', PartPricingView.as_view(), name='part-pricing'),
            path('supplierpart/<int:pk>/', SupplierPartPricingView.as_view(), name='supplierpart-pricing'),
            path('status/', StatusView.as_view(), name='status'),
            path('discontinued/', DiscontinuedView.as_view(), name='discontinued'),
        ]

    @staticmethod
    def _can_view_supplier_parts(request) -> bool:
        user = getattr(request, 'user', None)
        return bool(user and user.is_authenticated and (user.is_staff or user.has_perm('company.view_supplierpart')))

    def get_ui_routes(self, request, context, **kwargs):
        """The discontinued parts page: /web/plugin/part-pricing-update/discontinued"""
        return [
            {
                'key': 'discontinued-parts',
                'title': _('Discontinued parts'),
                'source': self.plugin_static_file('Discontinued.js:renderDiscontinuedPage'),
                'options': {'path': 'discontinued'},
            }
        ]

    def get_ui_navigation_items(self, request, context, **kwargs):
        """Link to the discontinued parts page in the navigation menu."""
        if not self._can_view_supplier_parts(request):
            return []

        return [
            {
                'key': 'discontinued-parts',
                'title': _('Discontinued parts'),
                'icon': 'ti:alert-triangle:outline',
                'options': {'url': f'plugin/{self.SLUG}/discontinued'},
            }
        ]

    def get_ui_panels(self, request, context: dict, **kwargs):
        """Add the 'Supplier Prices' panel to the part and supplier part pages.

        Only for the users who can see the supplier prices (InvenTree 'purchase order' role).
        """
        from .views import can_update_prices, can_view_prices

        context = context or {}
        target_model = context.get('target_model')

        if target_model not in ('part', 'supplierpart') or not context.get('target_id'):
            return []

        if not can_view_prices(getattr(request, 'user', None)):
            return []

        if target_model == 'part':
            from part.models import Part

            part = Part.objects.filter(pk=context['target_id']).first()

            if not part or not part.purchaseable:
                return []

        return [
            {
                'key': 'supplier-price-history',
                'title': _('Supplier Prices'),
                'description': _('Prices from Mouser, DigiKey and Farnell, with their history'),
                'icon': 'ti:chart-line:outline',
                'source': self.plugin_static_file('Panel.js:renderPricingPanel'),
                'context': {
                    'target_model': target_model,
                    'can_update': can_update_prices(request.user),
                },
            }
        ]
