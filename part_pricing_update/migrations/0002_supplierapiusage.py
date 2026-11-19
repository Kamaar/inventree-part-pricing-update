from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('part_pricing_update', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierApiUsage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('supplier', models.CharField(max_length=20, verbose_name='Supplier')),
                ('day', models.DateField(verbose_name='Day (UTC)')),
                ('requests', models.PositiveIntegerField(default=0, verbose_name='Requests')),
                ('blocked_until', models.DateTimeField(blank=True, help_text='Set when the API reports that the request limit has been reached', null=True, verbose_name='Blocked until')),
            ],
            options={
                'verbose_name': 'Supplier API Usage',
                'verbose_name_plural': 'Supplier API Usage',
                'ordering': ['-day', 'supplier'],
                'unique_together': {('supplier', 'day')},
            },
        ),
    ]
