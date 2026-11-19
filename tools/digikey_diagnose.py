"""Show the raw DigiKey API responses for a product (diagnostics).

Uses the DigiKey credentials saved in the plugin settings. Run inside the
InvenTree container:

    docker compose exec -T -w /home/inventree/src/backend/InvenTree inventree-server \\
        python manage.py shell < digikey_diagnose.py

Set SKU / MPN below. It sends 5 requests to the DigiKey API (not counted
by the plugin quota). The access token is never printed.
"""

import json
from urllib.parse import quote

SKU = '2457-MSS1048-822NLC-ND'
MPN = 'MSS1048-822NLC'

from plugin import registry

from part_pricing_update import pricing

plugin = registry.get_plugin('part-pricing-update')
client = pricing.build_clients(plugin).get('digikey')

if client is None:
    print('DigiKey credentials not configured in the plugin settings')
else:
    headers = {
        'Authorization': f'Bearer {client._get_token()}',
        'X-DIGIKEY-Client-Id': client.client_id,
        'X-DIGIKEY-Locale-Site': client.site,
        'X-DIGIKEY-Locale-Language': client.language,
        'X-DIGIKEY-Locale-Currency': client.currency,
        'Accept': 'application/json',
    }
    base = f'{client.base_url}/products/v4'

    def show(label, response):
        print(f'\n=== {label}: HTTP {response.status_code}')

        try:
            data = response.json()
        except ValueError:
            print(response.text[:500])
            return

        product = data.get('Product')

        if product:
            print('Status:', product.get('ProductStatus'), 'Discontinued:', product.get('Discontinued'),
                  'EndOfLife:', product.get('EndOfLife'))
            for v in product.get('ProductVariations') or []:
                print('  variation', v.get('DigiKeyProductNumber'), 'pricing:', v.get('StandardPricing'))
        elif 'ProductSubstitutes' in data:
            for sub in data['ProductSubstitutes']:
                print('  substitute', sub.get('SubstituteType'), sub.get('ManufacturerProductNumber'),
                      sub.get('DigiKeyProductNumber'), sub.get('UnitPrice'))
        elif 'Products' in data:
            print('ProductsCount:', data.get('ProductsCount'))
            for p in (data.get('ExactMatches') or []) + (data.get('Products') or []):
                print('  product', p.get('ManufacturerProductNumber'), p.get('ProductStatus'),
                      'Discontinued:', p.get('Discontinued'))
        else:
            print(json.dumps(data)[:500])

    session = client.session
    show(f'productdetails {SKU}', session.get(f'{base}/search/{quote(SKU, safe="")}/productdetails', headers=headers, timeout=20))
    show(f'productdetails {MPN}', session.get(f'{base}/search/{quote(MPN, safe="")}/productdetails', headers=headers, timeout=20))
    show(f'keyword {MPN}', session.post(f'{base}/search/keyword', json={'Keywords': MPN, 'Limit': 10}, headers=headers, timeout=20))
    show(f'substitutions {SKU}', session.get(f'{base}/search/{quote(SKU, safe="")}/substitutions', headers=headers, timeout=20))
    show(f'substitutions {MPN}', session.get(f'{base}/search/{quote(MPN, safe="")}/substitutions', headers=headers, timeout=20))
