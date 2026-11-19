"""Integration tests on a real InvenTree (the CI uses the inventree/inventree:stable image).

Environment:
    INVENTREE_SRC                    path of src/backend/InvenTree
    INVENTREE_DB_ENGINE / _DB_NAME   a migrated database (sqlite is enough)
    INVENTREE_PLUGINS_ENABLED=True, INVENTREE_PLUGIN_TESTING=True, INVENTREE_PLUGIN_TESTING_SETUP=True

Every test runs in a transaction which is rolled back: the database is left as it was.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.environ['INVENTREE_SRC'])
# The supplier client test helpers (FakeSession, fixtures)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'InvenTree.settings')

from InvenTree.wsgi import application  # noqa: F401 - loads Django and the plugin registry


@pytest.fixture(autouse=True)
def rollback():
    from django.db import transaction

    with transaction.atomic():
        yield
        transaction.set_rollback(True)


@pytest.fixture
def plugin():
    from part_pricing_update import pricing

    p = pricing.get_plugin()
    assert p is not None, 'The PartPricingUpdate plugin is not loaded'
    return p


@pytest.fixture
def suppliers(plugin):
    """Supplier companies, selected in the plugin settings (the database may have others)."""
    from company.models import Company

    companies = {
        key: Company.objects.create(name=name, is_supplier=True)
        for key, name in (('mouser', 'Mouser Electronics'), ('digikey', 'Digi-Key'), ('farnell', 'Farnell'))
    }
    previous = {key: plugin.get_setting(f'{key.upper()}_SUPPLIER') for key in companies}

    for key, company in companies.items():
        plugin.set_setting(f'{key.upper()}_SUPPLIER', company.pk)

    yield companies

    # The settings are cached outside the database transaction
    for key, value in previous.items():
        plugin.set_setting(f'{key.upper()}_SUPPLIER', value or '')


@pytest.fixture
def part():
    from part.models import Part, PartCategory

    category = PartCategory.objects.create(name='Integration test')
    return Part.objects.create(name='IT-PART', category=category, component=True, purchaseable=True, active=True)
