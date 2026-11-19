"""The plugin on a real InvenTree: models, price updates, part pricing, permissions, API."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group, User
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from part_pricing_update import pricing
from part_pricing_update.models import PriceRecord, PricingStatus
from part_pricing_update.suppliers import DigiKeyClient, MouserClient
from test_details import DIGIKEY_TOKEN, FakeResponse, FakeSession, fixture


def mouser(*responses):
    client = MouserClient('key', session=FakeSession(*responses))
    client.min_interval = 0
    return client


def digikey(*responses):
    client = DigiKeyClient('id', 'secret', session=FakeSession(DIGIKEY_TOKEN, *responses))
    client.min_interval = 0
    return client


def api_client(user):
    client = APIClient()
    client.force_login(user)
    return client


def test_plugin_is_active_and_compatible(plugin):
    from InvenTree.version import inventreeVersion
    from plugin import registry

    assert registry.get_plugin_config(plugin.slug).is_active()
    assert plugin.check_version(), f'Not compatible with InvenTree {inventreeVersion()}'


def test_supplier_companies_found_by_name(plugin, suppliers):
    companies = pricing.get_supplier_companies(plugin)

    assert companies == {key: company.pk for key, company in suppliers.items()}


def test_price_update_saves_breaks_history_and_part_pricing(plugin, suppliers, part):
    from common.settings import set_global_setting
    from company.models import SupplierPart

    set_global_setting('INVENTREE_DEFAULT_CURRENCY', 'EUR')
    sp = SupplierPart.objects.create(part=part, supplier=suppliers['digikey'], SKU='296-NE555P-ND')

    result = pricing.update_supplier_part(
        plugin, sp, digikey(FakeResponse(fixture('digikey_productdetails_296-NE555P-ND')))
    )

    assert result['success'], result
    assert sp.pricebreaks.count() > 0
    assert PriceRecord.objects.filter(supplier_part=sp).count() == 1
    assert PricingStatus.objects.get(supplier_part=sp).last_error == ''

    # InvenTree computes the part pricing from the supplier price breaks
    part.pricing.update_pricing()
    part.pricing.refresh_from_db()
    assert part.pricing.supplier_price_min is not None


def test_product_not_sold_any_more_is_a_gap_in_the_history(plugin, suppliers, part):
    from company.models import SupplierPart

    sp = SupplierPart.objects.create(part=part, supplier=suppliers['mouser'], SKU='595-NE555P')
    pricing.update_supplier_part(plugin, sp, mouser(FakeResponse(fixture('mouser_partnumber_595-NE555P'))))

    not_sold = fixture('mouser_partnumber_771-PESD12VL1BA-T_R')
    not_sold['SearchResults']['Parts'][0]['MouserPartNumber'] = '595-NE555P'
    result = pricing.update_supplier_part(plugin, sp, mouser(FakeResponse(not_sold)))

    assert result['discontinued'] and not result.get('error')
    records = list(PriceRecord.objects.filter(supplier_part=sp).order_by('first_seen'))
    assert [bool(r.price_breaks) for r in records] == [True, False]


def test_server_error_keeps_the_part_due(plugin, suppliers, part):
    from company.models import SupplierPart

    sp = SupplierPart.objects.create(part=part, supplier=suppliers['mouser'], SKU='81-GRM39C331J50')
    old = timezone.now() - timedelta(days=30)
    PricingStatus.objects.create(supplier_part=sp, last_checked=old)

    page = FakeResponse(None, status_code=503)
    page.text = '<html>error</html>'
    client = mouser(page)

    from part_pricing_update.suppliers import TemporaryError

    with pytest.raises(TemporaryError):
        pricing.update_supplier_part(plugin, sp, client)

    assert PricingStatus.objects.get(supplier_part=sp).last_checked == old
    assert pricing.due_supplier_parts(plugin).filter(pk=sp.pk).exists()


def test_permissions_follow_the_purchase_order_role(plugin, suppliers, part):
    from company.models import SupplierPart
    from users.models import RuleSet

    SupplierPart.objects.create(part=part, supplier=suppliers['digikey'], SKU='296-NE555P-ND')
    url = f'/plugin/part-pricing-update/part/{part.pk}/'

    plain = User.objects.create_user('it_plain', password='x')
    viewer = User.objects.create_user('it_viewer', password='x')
    group = Group.objects.create(name='it_po_view')
    RuleSet.objects.filter(group=group, name='purchase_order').update(can_view=True)
    # Save once more: the permissions of the group are rebuilt from its rulesets
    for ruleset in RuleSet.objects.filter(group=group, name='purchase_order'):
        ruleset.save()
    viewer.groups.add(group)
    viewer = User.objects.get(pk=viewer.pk)
    staff = User.objects.create_user('it_staff', password='x', is_staff=True)

    assert api_client(plain).get(url).status_code == 403
    assert api_client(viewer).get(url).status_code == 200
    assert api_client(viewer).post(url).status_code == 403
    assert api_client(staff).get(url).status_code == 200

    def panels(user):
        request = RequestFactory().get('/')
        request.user = user
        return plugin.get_ui_panels(request, {'target_model': 'part', 'target_id': part.pk})

    assert panels(plain) == []
    assert panels(viewer) and panels(viewer)[0]['context']['can_update'] is False


def test_discontinued_page(plugin, suppliers, part):
    from company.models import SupplierPart

    sp = SupplierPart.objects.create(part=part, supplier=suppliers['digikey'], SKU='OLD-SKU')
    PricingStatus.objects.create(supplier_part=sp, supplier_status='Not available at Digi-Key')
    staff = User.objects.create_user('it_staff2', password='x', is_staff=True)

    response = api_client(staff).get('/plugin/part-pricing-update/discontinued/')

    assert response.status_code == 200
    rows = {row['sku']: row for row in response.data['results']}
    assert rows['OLD-SKU']['supplier_status'] == 'Not available at Digi-Key'
    assert rows['OLD-SKU']['part']['pk'] == part.pk


def test_static_files_resolve(plugin):
    from django.core.management import call_command

    # Copy the static files of the active plugins, then resolve the hashed name of the vite manifest
    call_command('collectplugins')
    url = plugin.plugin_static_file('Panel.js:renderPricingPanel')

    assert url.startswith('/static/plugins/part-pricing-update/Panel-') and url.endswith(':renderPricingPanel')
