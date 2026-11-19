import type { InvenTreePluginContext } from '@inventreedb/ui';
import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useCallback, useEffect, useState } from 'react';

import { API_ROOT, errorMessage, formatDate } from './pricing';

type StatusResponse = {
  suppliers: {
    key: string;
    company: { pk: number; name: string } | null;
    configured: boolean;
    supplier_parts: number;
    due: number;
    errors: number;
    discontinued: number;
    requests_today: number;
    daily_limit: number;
    blocked_until: string | null;
  }[];
  errors: {
    pk: number;
    sku: string;
    supplier: string;
    part: string;
    part_pk: number;
    error: string;
    last_checked: string | null;
  }[];
};

const SUPPLIER_NAMES: Record<string, string> = {
  mouser: 'Mouser',
  digikey: 'DigiKey',
  farnell: 'Farnell',
};

function PricingSettings({ context }: { context: InvenTreePluginContext }) {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<boolean>(false);

  const load = useCallback(() => {
    context.api
      .get(`${API_ROOT}/status/`)
      .then((response) => {
        setData(response.data);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [context.api]);

  useEffect(() => {
    load();
  }, [load]);

  const updateAll = useCallback(() => {
    context.api
      .post(`${API_ROOT}/status/`)
      .then(() => {
        setQueued(true);
        notifications.show({
          title: 'Supplier prices',
          message: 'All the supplier parts will be updated by the background worker',
          color: 'green',
        });
      })
      .catch((err) => notifications.show({ title: 'Supplier prices', message: errorMessage(err), color: 'red' }));
  }, [context.api]);

  if (error) {
    return (
      <Alert color='red' title='Error loading the plugin status'>
        {error}
      </Alert>
    );
  }

  if (!data) {
    return <Loader />;
  }

  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Title order={5}>Suppliers</Title>
        <Group gap='xs'>
          <Button size='xs' variant='default' onClick={load}>
            Refresh
          </Button>
          <Button size='xs' onClick={updateAll} disabled={queued}>
            Update all now
          </Button>
        </Group>
      </Group>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>API</Table.Th>
            <Table.Th>InvenTree supplier</Table.Th>
            <Table.Th>Credentials</Table.Th>
            <Table.Th>Supplier parts</Table.Th>
            <Table.Th>Due for update</Table.Th>
            <Table.Th>Discontinued</Table.Th>
            <Table.Th>Errors</Table.Th>
            <Table.Th>Requests today (UTC)</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.suppliers.map((s) => (
            <Table.Tr key={s.key}>
              <Table.Td>{SUPPLIER_NAMES[s.key] ?? s.key}</Table.Td>
              <Table.Td>
                {s.company ? (
                  s.company.name
                ) : (
                  <Text size='sm' c='orange'>
                    Not found
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                <Badge color={s.configured ? 'green' : 'gray'} variant='light'>
                  {s.configured ? 'Configured' : 'Missing'}
                </Badge>
              </Table.Td>
              <Table.Td>{s.supplier_parts}</Table.Td>
              <Table.Td>{s.due}</Table.Td>
              <Table.Td>
                {s.discontinued ? (
                  <Anchor size='sm' onClick={() => context.navigate('/plugin/part-pricing-update/discontinued')}>
                    {s.discontinued}
                  </Anchor>
                ) : (
                  0
                )}
              </Table.Td>
              <Table.Td>{s.errors}</Table.Td>
              <Table.Td>
                <Group gap='xs'>
                  <Text size='sm'>
                    {s.requests_today} / {s.daily_limit}
                  </Text>
                  {s.blocked_until && (
                    <Badge color='orange' variant='light'>
                      Paused until {new Date(s.blocked_until).toLocaleString()}
                    </Badge>
                  )}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      {data.errors.length > 0 && (
        <>
          <Title order={5}>Latest errors</Title>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Part</Table.Th>
                <Table.Th>Supplier</Table.Th>
                <Table.Th>SKU</Table.Th>
                <Table.Th>Error</Table.Th>
                <Table.Th>Checked</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.errors.map((e) => (
                <Table.Tr key={e.pk}>
                  <Table.Td>
                    <Anchor size='sm' onClick={() => context.navigate(`/part/${e.part_pk}/`)}>
                      {e.part}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{e.supplier}</Table.Td>
                  <Table.Td>{e.sku}</Table.Td>
                  <Table.Td>{e.error}</Table.Td>
                  <Table.Td>{formatDate(e.last_checked)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}

// Called by InvenTree to render the custom section of the plugin settings page
export function renderPricingSettings(context: InvenTreePluginContext) {
  return <PricingSettings context={context} />;
}
