from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('part_pricing_update', '0002_supplierapiusage'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricingstatus',
            name='supplier_status',
            field=models.CharField(blank=True, default='', help_text='Product lifecycle status reported by the supplier (e.g. Active, Obsolete)', max_length=100, verbose_name='Supplier status'),
        ),
        migrations.AddField(
            model_name='pricingstatus',
            name='suggested_sku',
            field=models.CharField(blank=True, default='', help_text='Current supplier code, when the stored SKU is no longer valid', max_length=100, verbose_name='Suggested SKU'),
        ),
        migrations.AddField(
            model_name='pricingstatus',
            name='alternatives',
            field=models.JSONField(blank=True, default=list, help_text='Replacement products suggested by the supplier', verbose_name='Alternatives'),
        ),
    ]
