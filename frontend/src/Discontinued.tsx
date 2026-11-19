import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';

import { type Alternative, API_ROOT, absoluteUrl, errorMessage, formatDate } from './pricing';

type DiscontinuedRow = {
  pk: number;
  sku: string;
  link: string;
  supplier: { pk: number; name: string };
  supplier_key: string;
  mpn: string;
  manufacturer: string;
  part: { pk: number; name: string; IPN: string; description: string; active: boolean };
  in_stock: number;
  used_in: number;
  supplier_status: string;
  suggested_sku: string;
  alternatives: Alternative[];
  last_checked: string | null;
  other_sources: { pk: number; sku: string; supplier: string; available: number | null }[];
};

function csvValue(value: string | number): string {
  const text = String(value ?? '');
  return /[",;\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportCsv(rows: DiscontinuedRow[]) {
  const header = [
    'IPN',
    'Part',
    'Description',
    'Supplier',
    'SKU',
    'Manufacturer',
    'MPN',
    'Supplier status',
    'In stock',
    'Used in',
    'Other sources',
    'Suggested SKU',
    'Alternatives',
    'Last checked',
  ];
  const lines = rows.map((r) =>
    [
      r.part.IPN,
      r.part.name,
      r.part.description,
      r.supplier.name,
      r.sku,
      r.manufacturer,
      r.mpn,
      r.supplier_status,
      r.in_stock,
      r.used_in,
      r.other_sources.map((s) => `${s.supplier} ${s.sku}`).join(', '),
      r.suggested_sku,
      r.alternatives.map((a) => a.mpn || a.sku).join(', '),
      formatDate(r.last_checked),
    ]
      .map(csvValue)
      .join(';'),
  );

  const blob = new Blob([`﻿${[header.join(';'), ...lines].join('\n')}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `discontinued-parts-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function Alternatives({ row }: { row: DiscontinuedRow }) {
  return (
    <Table withRowBorders={false}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Type</Table.Th>
          <Table.Th>MPN</Table.Th>
          <Table.Th>Manufacturer</Table.Th>
          <Table.Th>SKU</Table.Th>
          <Table.Th>Description</Table.Th>
          <Table.Th>Unit price</Table.Th>
          <Table.Th>Stock</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {row.alternatives.map((alt, index) => (
          <Table.Tr key={`${alt.sku}-${index}`}>
            <Table.Td>{alt.type}</Table.Td>
            <Table.Td>
              {alt.url ? (
                <Anchor href={absoluteUrl(alt.url, row.supplier_key)} target='_blank' rel='noreferrer' size='sm'>
                  {alt.mpn || alt.sku}
                </Anchor>
              ) : (
                alt.mpn || alt.sku
              )}
            </Table.Td>
            <Table.Td>{alt.manufacturer}</Table.Td>
            <Table.Td>{alt.sku}</Table.Td>
            <Table.Td>{alt.description}</Table.Td>
            <Table.Td>{alt.unit_price || '-'}</Table.Td>
            <Table.Td>{alt.stock ?? '-'}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function DiscontinuedPage({ context }: { context: InvenTreePluginContext }) {
  const [rows, setRows] = useState<DiscontinuedRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [supplier, setSupplier] = useState<string | null>(null);
  const [search, setSearch] = useState<string>('');
  const [onlyWithoutSources, setOnlyWithoutSources] = useState<boolean>(false);
  const [onlyActive, setOnlyActive] = useState<boolean>(true);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const load = useCallback(() => {
    setRows(null);
    context.api
      .get(`${API_ROOT}/discontinued/`)
      .then((response) => {
        setRows(response.data.results);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [context.api]);

  useEffect(() => {
    load();
  }, [load]);

  const suppliers = useMemo(() => Array.from(new Set((rows ?? []).map((r) => r.supplier.name))).sort(), [rows]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();

    return (rows ?? []).filter((r) => {
      if (supplier && r.supplier.name !== supplier) return false;
      if (onlyWithoutSources && r.other_sources.length > 0) return false;
      if (onlyActive && !r.part.active) return false;
      if (!term) return true;

      return [r.part.IPN, r.part.name, r.part.description, r.sku, r.mpn, r.manufacturer, r.supplier_status]
        .join(' ')
        .toLowerCase()
        .includes(term);
    });
  }, [rows, supplier, search, onlyWithoutSources, onlyActive]);

  const toggle = (pk: number) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(pk)) {
        next.delete(pk);
      } else {
        next.add(pk);
      }
      return next;
    });

  if (error) {
    return (
      <Alert color='red' title='Error loading the discontinued parts'>
        {error}
      </Alert>
    );
  }

  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Title order={3}>Discontinued parts</Title>
        <Group gap='xs'>
          <Button size='xs' variant='default' onClick={load}>
            Refresh
          </Button>
          <Button size='xs' variant='default' onClick={() => exportCsv(filtered)} disabled={!filtered.length}>
            Export CSV
          </Button>
        </Group>
      </Group>
      <Text size='sm' c='dimmed'>
        Supplier parts which the supplier reports as discontinued, obsolete or at end of life, from the last price
        update. "Other sources" are the active supplier parts of the same part not reported as discontinued.
      </Text>

      <Group gap='md' align='flex-end'>
        <TextInput
          label='Search'
          placeholder='IPN, name, SKU, MPN...'
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          w={260}
        />
        <Select
          label='Supplier'
          placeholder='All'
          data={suppliers}
          value={supplier}
          onChange={setSupplier}
          clearable
          w={200}
        />
        <Switch
          label='Only active parts'
          checked={onlyActive}
          onChange={(e) => setOnlyActive(e.currentTarget.checked)}
        />
        <Switch
          label='Only parts without other sources'
          checked={onlyWithoutSources}
          onChange={(e) => setOnlyWithoutSources(e.currentTarget.checked)}
        />
      </Group>

      {rows === null ? (
        <Loader />
      ) : (
        <>
          <Text size='sm'>
            {filtered.length} of {rows.length} supplier parts
          </Text>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Part</Table.Th>
                <Table.Th>Supplier</Table.Th>
                <Table.Th>SKU</Table.Th>
                <Table.Th>MPN</Table.Th>
                <Table.Th>Supplier status</Table.Th>
                <Table.Th>In stock</Table.Th>
                <Table.Th>Used in</Table.Th>
                <Table.Th>Other sources</Table.Th>
                <Table.Th>Alternatives</Table.Th>
                <Table.Th>Checked</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filtered.map((r) => (
                <Fragment key={r.pk}>
                  <Table.Tr>
                    <Table.Td>
                      <Anchor size='sm' onClick={() => context.navigate(`/part/${r.part.pk}/`)}>
                        {r.part.IPN ? `${r.part.IPN} - ` : ''}
                        {r.part.name}
                      </Anchor>
                      {!r.part.active && (
                        <Badge ml='xs' size='xs' color='gray' variant='light'>
                          Inactive
                        </Badge>
                      )}
                      {r.part.description && (
                        <Text size='xs' c='dimmed'>
                          {r.part.description}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{r.supplier.name}</Table.Td>
                    <Table.Td>
                      <Anchor size='sm' onClick={() => context.navigate(`/purchasing/supplier-part/${r.pk}/`)}>
                        {r.sku}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <Text size='sm'>{r.mpn || '-'}</Text>
                      {r.manufacturer && (
                        <Text size='xs' c='dimmed'>
                          {r.manufacturer}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge color='orange' variant='light'>
                        {r.supplier_status}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{r.in_stock}</Table.Td>
                    <Table.Td>{r.used_in || '-'}</Table.Td>
                    <Table.Td>
                      {r.other_sources.length ? (
                        <Group gap={4}>
                          {r.other_sources.map((s) => (
                            <Tooltip
                              key={s.pk}
                              label={`${s.sku}${s.available !== null ? ` - available: ${s.available}` : ''}`}
                            >
                              <Badge
                                color='green'
                                variant='light'
                                style={{ cursor: 'pointer' }}
                                onClick={() => context.navigate(`/purchasing/supplier-part/${s.pk}/`)}
                              >
                                {s.supplier}
                              </Badge>
                            </Tooltip>
                          ))}
                        </Group>
                      ) : (
                        <Badge color='red' variant='light'>
                          None
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {r.suggested_sku && <Text size='xs'>Suggested: {r.suggested_sku}</Text>}
                      {r.alternatives.length ? (
                        <Anchor size='sm' onClick={() => toggle(r.pk)}>
                          {expanded.has(r.pk) ? 'Hide' : `Show ${r.alternatives.length}`}
                        </Anchor>
                      ) : (
                        !r.suggested_sku && '-'
                      )}
                    </Table.Td>
                    <Table.Td>{formatDate(r.last_checked)}</Table.Td>
                  </Table.Tr>
                  {expanded.has(r.pk) && (
                    <Table.Tr>
                      <Table.Td colSpan={10}>
                        <Alternatives row={r} />
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}

// Called by InvenTree to render the plugin page
export function renderDiscontinuedPage(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <DiscontinuedPage context={context} />;
}
