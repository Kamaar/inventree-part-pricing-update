import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useCallback, useEffect, useMemo, useState } from 'react';

import PriceChart, { type ChartSeries } from './PriceChart';
import {
  API_ROOT,
  absoluteUrl,
  activeBreakIndex,
  convertPrice,
  type ExchangeRates,
  errorMessage,
  formatDate,
  formatPrice,
  PERIODS,
  type PricingResponse,
  priceAtQuantity,
  type SupplierPartPricing,
} from './pricing';

const QUANTITIES = ['1', '10', '100', '1000'];
const UPDATE_TIMEOUT = 120000;

const SERIES_COLORS = ['blue', 'orange', 'teal', 'grape', 'red', 'lime', 'cyan', 'pink'];

/**
 * Chart series in a single currency (the InvenTree base currency, with the current exchange rates).
 * Returns the supplier parts which could not be converted (missing exchange rate) apart.
 */
function buildSeries(
  supplierParts: SupplierPartPricing[],
  quantity: number,
  context: InvenTreePluginContext,
  currency: string,
  rates: ExchangeRates | null,
): { series: ChartSeries[]; unconverted: string[]; converted: boolean } {
  const shade = context.colorScheme === 'dark' ? 4 : 6;
  const unconverted: string[] = [];
  const series: ChartSeries[] = [];
  let converted = false;

  supplierParts
    .filter((sp) => sp.history.length > 0)
    .forEach((sp, idx) => {
      const records = sp.history;
      let convertible = true;

      const steps = records.map((record, i) => {
        // No price breaks (e.g. product not sold): NaN, a gap in the line
        const price = priceAtQuantity(record.price_breaks, quantity);
        const value = price === null ? null : convertPrice(price, record.currency, currency, rates);

        if (price !== null && value === null) {
          convertible = false;
        }

        return {
          start: Date.parse(record.first_seen),
          // A price is valid until the next record replaced it
          end: Date.parse(i + 1 < records.length ? records[i + 1].first_seen : record.last_seen),
          value: value ?? Number.NaN,
        };
      });

      const currencies = [...new Set(records.map((r) => r.currency).filter(Boolean))];

      if (!convertible) {
        unconverted.push(`${sp.supplier.name} ${sp.sku} (${currencies.join(', ')})`);
        return;
      }

      const foreign = currencies.some((c) => c !== currency);
      converted = converted || foreign;

      series.push({
        label: `${sp.supplier.name} ${sp.sku}${foreign ? ` (${currencies.join(', ')})` : ''}`,
        color: context.theme.colors[SERIES_COLORS[idx % SERIES_COLORS.length]][shade],
        steps,
      });
    });

  return { series, unconverted, converted };
}

function PriceChange({ sp, quantity }: { sp: SupplierPartPricing; quantity: number }) {
  if (sp.history.length < 2) {
    return (
      <Text size='sm' c='dimmed'>
        -
      </Text>
    );
  }

  const last = sp.history[sp.history.length - 1];
  const previous = sp.history[sp.history.length - 2];
  const now = priceAtQuantity(last.price_breaks, quantity);
  const before = priceAtQuantity(previous.price_breaks, quantity);

  if (now === null || before === null || before === 0 || last.currency !== previous.currency) {
    return (
      <Text size='sm' c='dimmed'>
        -
      </Text>
    );
  }

  const change = ((now - before) / before) * 100;

  if (Math.abs(change) < 0.05) {
    return (
      <Text size='sm' c='dimmed'>
        =
      </Text>
    );
  }

  return (
    <Tooltip label={`${formatPrice(before, previous.currency)} (${formatDate(previous.first_seen)})`}>
      <Text size='sm' c={change > 0 ? 'red' : 'green'}>
        {change > 0 ? '+' : ''}
        {change.toFixed(1)}%
      </Text>
    </Tooltip>
  );
}

/**
 * The price break used for the selected quantity; the tooltip lists all of them.
 */
function PriceBreaksCell({ sp, quantity }: { sp: SupplierPartPricing; quantity: number }) {
  const breaks = sp.price_breaks;
  const active = activeBreakIndex(
    breaks.map((pb) => pb.quantity),
    quantity,
  );

  if (active < 0) {
    return (
      <Text size='sm' c='dimmed'>
        -
      </Text>
    );
  }

  const label = breaks
    .map((pb, i) => `${i === active ? '▶ ' : '   '}${pb.quantity}+: ${formatPrice(Number(pb.price), pb.currency)}`)
    .join('\n');

  return (
    <Tooltip multiline label={label} style={{ whiteSpace: 'pre', fontFamily: 'monospace' }}>
      <Text size='sm'>
        {breaks[active].quantity}+{' '}
        <Text span size='xs' c='dimmed'>
          ({active + 1}/{breaks.length})
        </Text>
      </Text>
    </Tooltip>
  );
}

function StatusBadge({ sp }: { sp: SupplierPartPricing }) {
  if (!sp.status) {
    return (
      <Badge color='gray' variant='light'>
        Never updated
      </Badge>
    );
  }

  const badges = [];
  const supplierStatus = sp.status.supplier_status ?? '';

  if (sp.status.last_error) {
    badges.push(
      <Tooltip key='error' label={sp.status.last_error} multiline w={300}>
        <Badge color='red' variant='light'>
          Error
        </Badge>
      </Tooltip>,
    );
  }

  if (sp.status.discontinued) {
    badges.push(
      <Badge key='status' color='orange' variant='light'>
        {supplierStatus}
      </Badge>,
    );
  }

  if (sp.status.no_price) {
    badges.push(
      <Tooltip key='no-price' label={`${supplierStatus}. The last prices are kept.`} multiline w={300}>
        <Badge color='yellow' variant='light'>
          No price for this packaging
        </Badge>
      </Tooltip>,
    );
  }

  if (sp.status.suggested_sku) {
    badges.push(
      <Tooltip key='sku' label={`The supplier code is now ${sp.status.suggested_sku}`}>
        <Badge color='yellow' variant='light'>
          New code
        </Badge>
      </Tooltip>,
    );
  }

  if (badges.length === 0) {
    return (
      <Badge color='green' variant='light'>
        OK
      </Badge>
    );
  }

  return <Group gap={4}>{badges}</Group>;
}

/**
 * New supplier code and replacement products suggested by the suppliers.
 */
function SupplierNotices({ supplierParts }: { supplierParts: SupplierPartPricing[] }) {
  const notices = supplierParts.filter(
    (sp) => sp.status && (sp.status.suggested_sku || (sp.status.alternatives ?? []).length > 0),
  );

  if (notices.length === 0) {
    return null;
  }

  return (
    <Stack gap='sm'>
      {notices.map((sp) => (
        <Alert
          key={sp.pk}
          color='orange'
          title={`${sp.supplier.name} ${sp.sku}${sp.status?.supplier_status ? ` - ${sp.status.supplier_status}` : ''}`}
        >
          <Stack gap='xs'>
            {sp.status?.suggested_sku && (
              <Text size='sm'>
                The code {sp.sku} is no longer valid: the product is now sold as <b>{sp.status.suggested_sku}</b>.
                Prices have been updated from the new code; update the SKU of the supplier part.
              </Text>
            )}
            {(sp.status?.alternatives ?? []).length > 0 && (
              <>
                <Text size='sm'>Alternatives suggested by {sp.supplier.name}:</Text>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Type</Table.Th>
                      <Table.Th>MPN</Table.Th>
                      <Table.Th>Manufacturer</Table.Th>
                      <Table.Th>Supplier code</Table.Th>
                      <Table.Th>Unit price</Table.Th>
                      <Table.Th>Stock</Table.Th>
                      <Table.Th>Description</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {sp.status?.alternatives.map((alt) => (
                      <Table.Tr key={`${alt.sku}-${alt.mpn}`}>
                        <Table.Td>{alt.type}</Table.Td>
                        <Table.Td>
                          {alt.url ? (
                            <Anchor
                              href={absoluteUrl(alt.url, sp.supplier_key)}
                              target='_blank'
                              rel='noreferrer'
                              size='sm'
                            >
                              {alt.mpn}
                            </Anchor>
                          ) : (
                            alt.mpn
                          )}
                        </Table.Td>
                        <Table.Td>{alt.manufacturer}</Table.Td>
                        <Table.Td>{alt.sku}</Table.Td>
                        <Table.Td>{alt.unit_price}</Table.Td>
                        <Table.Td>{alt.stock ?? '-'}</Table.Td>
                        <Table.Td>{alt.description}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </>
            )}
          </Stack>
        </Alert>
      ))}
    </Stack>
  );
}

function PricingPanel({ context }: { context: InvenTreePluginContext }) {
  const model: string = context.context?.target_model ?? context.model ?? 'part';
  const canUpdate: boolean = !!context.context?.can_update;
  const url = `${API_ROOT}/${model}/${context.id}/`;

  const [data, setData] = useState<PricingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [updating, setUpdating] = useState<boolean>(false);
  const [quantity, setQuantity] = useState<string>('1');
  const [period, setPeriod] = useState<string>('All');
  const [rates, setRates] = useState<ExchangeRates | null>(null);

  // Exchange rates of InvenTree: the chart shows every supplier in the base currency
  useEffect(() => {
    context.api
      .get('/api/currency/exchange/')
      .then((response) => setRates(response.data))
      .catch(() => setRates(null));
  }, [context.api]);

  const load = useCallback(() => {
    setLoading(true);
    context.api
      .get(url)
      .then((response) => {
        setData(response.data);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }, [context.api, url]);

  useEffect(() => {
    load();
  }, [load]);

  const updateNow = useCallback(() => {
    setUpdating(true);
    context.api
      // Several suppliers, with the minimum delay between requests: much longer
      // than the default timeout of the InvenTree API client (5 s)
      .post(url, {}, { timeout: UPDATE_TIMEOUT })
      .then((response) => {
        const { updated, discontinued, no_price: noPrice, failed } = response.data;
        const parts = [`${updated} updated`];

        if (discontinued) {
          parts.push(`${discontinued} discontinued`);
        }

        if (noPrice) {
          parts.push(`${noPrice} not priced in this packaging`);
        }

        if (failed) {
          parts.push(`${failed} failed`);
        }

        notifications.show({
          title: 'Supplier prices',
          message: parts.join(', '),
          color: failed ? 'orange' : 'green',
        });
        load();
        context.reloadInstance?.();
      })
      .catch((err) => {
        const timedOut = err?.code === 'ECONNABORTED' || /timeout/i.test(err?.message ?? '');
        notifications.show({
          title: 'Supplier prices',
          message: timedOut
            ? 'The update is taking long: it continues on the server, reload the page in a minute'
            : errorMessage(err),
          color: timedOut ? 'orange' : 'red',
        });
      })
      .finally(() => setUpdating(false));
  }, [context, url, load]);

  const qty = Number(quantity);
  const supplierParts = data?.supplier_parts ?? [];
  // Base currency of InvenTree; without exchange rates, the currency of the latest prices
  const chartCurrency = rates?.base_currency || supplierParts.flatMap((sp) => sp.history).at(-1)?.currency || 'EUR';
  const { series, unconverted, converted } = useMemo(
    () => buildSeries(supplierParts, qty, context, chartCurrency, rates),
    [supplierParts, qty, context, chartCurrency, rates],
  );
  const months = PERIODS[period] ?? 0;
  const since = useMemo(() => {
    if (!months) {
      return null;
    }

    const date = new Date();
    date.setMonth(date.getMonth() - months);
    return date.getTime();
  }, [months]);
  const missingCredentials = [
    ...new Set(supplierParts.filter((sp) => !data?.configured.includes(sp.supplier_key)).map((sp) => sp.supplier.name)),
  ];

  if (loading && !data) {
    return <Loader />;
  }

  if (error) {
    return (
      <Alert color='red' title='Error loading supplier prices'>
        {error}
      </Alert>
    );
  }

  if (supplierParts.length === 0) {
    return (
      <Alert color='blue' title='No supplier parts'>
        There are no active Mouser, DigiKey or Farnell supplier parts with a SKU.
      </Alert>
    );
  }

  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Group gap='xs'>
          <Text size='sm'>Unit price at quantity</Text>
          <SegmentedControl size='xs' data={QUANTITIES} value={quantity} onChange={setQuantity} />
        </Group>
        {canUpdate && (
          <Button size='xs' onClick={updateNow} loading={updating}>
            Update now
          </Button>
        )}
      </Group>

      {missingCredentials.length > 0 && (
        <Alert color='orange' title='API not configured'>
          Set the API credentials for {missingCredentials.join(', ')} in the plugin settings.
        </Alert>
      )}

      <Paper withBorder p='sm'>
        <Group justify='space-between' mb='xs'>
          <Text size='xs' c='dimmed'>
            Prices in {chartCurrency}
            {converted ? ', converted with the current exchange rates' : ''}. Dashed: no prices.
          </Text>
          <SegmentedControl size='xs' data={Object.keys(PERIODS)} value={period} onChange={setPeriod} />
        </Group>
        {series.length > 0 ? (
          <PriceChart
            series={series}
            currency={chartCurrency}
            since={since}
            textColor={context.colorScheme === 'dark' ? context.theme.colors.dark[1] : context.theme.colors.gray[7]}
            gridColor={context.colorScheme === 'dark' ? context.theme.colors.dark[4] : context.theme.colors.gray[2]}
          />
        ) : (
          <Text c='dimmed' ta='center'>
            No price history yet: it is recorded at every update.
          </Text>
        )}
        {unconverted.length > 0 && (
          <Text size='xs' c='orange' mt='xs'>
            Not in the chart (no exchange rate to {chartCurrency}): {unconverted.join(', ')}
          </Text>
        )}
      </Paper>

      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Supplier</Table.Th>
            <Table.Th>SKU</Table.Th>
            <Table.Th>Price @ {quantity}</Table.Th>
            <Table.Th>Change</Table.Th>
            <Table.Th>Price breaks</Table.Th>
            <Table.Th>Stock</Table.Th>
            <Table.Th>Last checked</Table.Th>
            <Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {supplierParts.map((sp) => {
            const breaks: [number, string][] = sp.price_breaks.map((pb) => [pb.quantity, pb.price]);
            const currency = sp.price_breaks[0]?.currency ?? '';

            return (
              <Table.Tr key={sp.pk}>
                <Table.Td>{sp.supplier.name}</Table.Td>
                <Table.Td>
                  {sp.link ? (
                    <Anchor href={sp.link} target='_blank' rel='noreferrer' size='sm'>
                      {sp.sku}
                    </Anchor>
                  ) : (
                    sp.sku
                  )}
                </Table.Td>
                <Table.Td>{formatPrice(priceAtQuantity(breaks, qty), currency)}</Table.Td>
                <Table.Td>
                  <PriceChange sp={sp} quantity={qty} />
                </Table.Td>
                <Table.Td>
                  <PriceBreaksCell sp={sp} quantity={qty} />
                </Table.Td>
                <Table.Td>{sp.available ?? '-'}</Table.Td>
                <Table.Td>{formatDate(sp.status?.last_checked)}</Table.Td>
                <Table.Td>
                  <StatusBadge sp={sp} />
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>

      <SupplierNotices supplierParts={supplierParts} />
    </Stack>
  );
}

// Called by InvenTree to render the panel
export function renderPricingPanel(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <PricingPanel context={context} />;
}
