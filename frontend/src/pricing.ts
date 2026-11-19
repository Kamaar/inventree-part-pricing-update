// Types and helpers shared by the plugin UI components

export const API_ROOT = '/plugin/part-pricing-update';

export type HistoryRecord = {
  first_seen: string;
  last_seen: string;
  currency: string;
  unit_price: string | null;
  price_breaks: [number, string][];
  stock: number | null;
};

export type Alternative = {
  type: string;
  mpn: string;
  manufacturer: string;
  sku: string;
  description: string;
  url: string;
  unit_price: string;
  stock: number | null;
};

export type SupplierPartPricing = {
  pk: number;
  sku: string;
  supplier: { pk: number; name: string };
  supplier_key: string;
  link: string;
  available: number | null;
  availability_updated: string | null;
  price_breaks: { quantity: number; price: string; currency: string }[];
  status: {
    last_checked: string | null;
    last_success: string | null;
    last_error: string;
    supplier_status: string;
    discontinued: boolean;
    // Active product sold only in another packaging: the current prices are kept
    no_price: boolean;
    suggested_sku: string;
    alternatives: Alternative[];
  } | null;
  history: HistoryRecord[];
};

export type PricingResponse = {
  configured: string[];
  supplier_parts: SupplierPartPricing[];
};

/**
 * Unit price for the given quantity: the price of the largest break not above it
 * (or the first break, when the quantity is below the minimum).
 */
export function priceAtQuantity(breaks: [number, string][], quantity: number): number | null {
  if (!breaks || breaks.length === 0) {
    return null;
  }

  let price = Number(breaks[0][1]);

  for (const [qty, value] of breaks) {
    if (qty <= quantity) {
      price = Number(value);
    }
  }

  return price;
}

/**
 * Index of the price break used for the given quantity (the largest break not above it,
 * or the first one when the quantity is below the minimum). -1 if there are no breaks.
 */
export function activeBreakIndex(quantities: number[], quantity: number): number {
  let index = quantities.length ? 0 : -1;

  quantities.forEach((q, i) => {
    if (q <= quantity) {
      index = i;
    }
  });

  return index;
}

export function formatPrice(value: number | null, currency: string): string {
  if (value === null || Number.isNaN(value)) {
    return '-';
  }

  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'EUR',
      minimumFractionDigits: 2,
      maximumFractionDigits: value < 1 ? 5 : 3,
    }).format(value);
  } catch {
    return `${value} ${currency}`;
  }
}

/**
 * Alternatives saved before 0.4.5 may have DigiKey site-relative URLs ('/en/products/...'):
 * without a host the browser would open them on the InvenTree server.
 */
export function absoluteUrl(url: string, supplier: string): string {
  if (url.startsWith('//')) {
    return `https:${url}`;
  }

  if (url.startsWith('/')) {
    const host =
      supplier === 'digikey' ? 'www.digikey.com' : supplier === 'mouser' ? 'www.mouser.com' : 'it.farnell.com';
    return `https://${host}${url}`;
  }

  return url;
}

const DAY = 86400000;

// Periods of the chart: months back from the last check (0 = all the history)
export const PERIODS: Record<string, number> = { '3M': 3, '6M': 6, '1Y': 12, All: 0 };

/**
 * Ticks of the time axis on calendar boundaries: days or weeks for short ranges,
 * otherwise the first day of every 1, 2, 3, 6, 12 or 24 months (at most 6 ticks).
 */
export function timeTicks(xMin: number, xMax: number): { ticks: number[]; label: (t: number) => string } {
  const span = xMax - xMin;

  if (span <= 62 * DAY) {
    const stepDays = span <= 10 * DAY ? 1 : span <= 31 * DAY ? 7 : 14;
    const first = new Date(xMin);
    first.setHours(0, 0, 0, 0);

    if (first.getTime() < xMin) {
      first.setDate(first.getDate() + 1);
    }

    const ticks: number[] = [];

    for (const d = first; d.getTime() <= xMax && ticks.length < 12; d.setDate(d.getDate() + stepDays)) {
      ticks.push(d.getTime());
    }

    return { ticks, label: (t) => new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) };
  }

  const months = span / (30.44 * DAY);
  const step = [1, 2, 3, 6, 12, 24].find((s) => months / s <= 6) ?? 24;
  const start = new Date(xMin);
  const d = new Date(start.getFullYear(), start.getMonth(), 1);

  // First month boundary after xMin, aligned to the step (Jan, Apr, Jul, Oct for 3 months)
  while (d.getTime() < xMin || d.getMonth() % Math.min(step, 12) !== 0) {
    d.setMonth(d.getMonth() + 1);
  }

  const ticks: number[] = [];

  for (; d.getTime() <= xMax; d.setMonth(d.getMonth() + step)) {
    ticks.push(d.getTime());
  }

  return { ticks, label: (t) => new Date(t).toLocaleDateString(undefined, { month: 'short', year: '2-digit' }) };
}

export type ExchangeRates = { base_currency: string; exchange_rates: Record<string, number> };

/**
 * Convert a price with the InvenTree exchange rates (units of each currency for one unit
 * of the base currency). null if a rate is missing.
 */
export function convertPrice(value: number, from: string, to: string, rates: ExchangeRates | null): number | null {
  if (!from || !to || from === to) {
    return value;
  }

  const fromRate = rates?.exchange_rates[from];
  const toRate = rates?.exchange_rates[to];

  if (!fromRate || !toRate) {
    return null;
  }

  return (value / fromRate) * toRate;
}

export function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString() : '-';
}

export function errorMessage(error: any): string {
  return error?.response?.data?.detail ?? error?.message ?? String(error);
}
