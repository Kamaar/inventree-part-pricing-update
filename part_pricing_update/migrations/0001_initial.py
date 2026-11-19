from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('company', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PriceRecord',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('currency', models.CharField(max_length=3, verbose_name='Currency')),
                ('first_seen', models.DateTimeField(verbose_name='First seen')),
                ('last_seen', models.DateTimeField(verbose_name='Last seen')),
                ('unit_price', models.DecimalField(blank=True, decimal_places=6, help_text='Price at the lowest quantity break', max_digits=19, null=True, verbose_name='Unit price')),
                ('price_breaks', models.JSONField(default=list, help_text='List of [quantity, price] pairs', verbose_name='Price breaks')),
                ('stock', models.IntegerField(blank=True, help_text='Supplier stock at the last check', null=True, verbose_name='Stock')),
                ('supplier_part', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pricing_history', to='company.supplierpart', verbose_name='Supplier Part')),
            ],
            options={
                'verbose_name': 'Price Record',
                'verbose_name_plural': 'Price Records',
                'ordering': ['supplier_part', 'first_seen'],
                'indexes': [models.Index(fields=['supplier_part', '-first_seen'], name='ppu_record_sp_date_idx')],
            },
        ),
        migrations.CreateModel(
            name='PricingStatus',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_checked', models.DateTimeField(blank=True, null=True, verbose_name='Last checked')),
                ('last_success', models.DateTimeField(blank=True, null=True, verbose_name='Last success')),
                ('last_error', models.TextField(blank=True, default='', verbose_name='Last error')),
                ('supplier_part', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='pricing_status', to='company.supplierpart', verbose_name='Supplier Part')),
            ],
            options={
                'verbose_name': 'Pricing Status',
                'verbose_name_plural': 'Pricing Status',
            },
        ),
    ]
