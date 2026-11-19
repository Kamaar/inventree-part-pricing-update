import { Group, Text } from '@mantine/core';
import { useMemo } from 'react';

import { formatDate, formatPrice, timeTicks } from './pricing';

export type ChartSeries = {
  label: string;
  color: string;
  // Each step is a price valid from 'start' to 'end'; NaN = no prices in that period
  // (e.g. product not sold): the line is interrupted
  steps: { start: number; end: number; value: number }[];
};

const WIDTH = 800;
const HEIGHT = 260;
const PAD = { top: 16, right: 16, bottom: 32, left: 72 };
const DAY = 86400000;

function valueTicks(min: number, max: number, count = 5): number[] {
  if (min === max) {
    return [min];
  }

  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, i) => min + i * step);
}

/**
 * Step chart of the price history (dependency free SVG).
 *
 * since: show only the steps after this time (the ones across it are cut), null = all
 */
export default function PriceChart({
  series,
  currency,
  since,
  textColor,
  gridColor,
}: {
  series: ChartSeries[];
  currency: string;
  since: number | null;
  textColor: string;
  gridColor: string;
}) {
  // Steps inside the period, with the original start kept for the tooltips
  const visible = useMemo(
    () =>
      series.map((s) => ({
        ...s,
        steps: s.steps
          .filter((step) => since === null || step.end >= since)
          .map((step) => ({
            ...step,
            from: step.start,
            start: since === null ? step.start : Math.max(step.start, since),
          })),
      })),
    [series, since],
  );

  const bounds = useMemo(() => {
    const steps = visible.flatMap((s) => s.steps);
    const priced = steps.filter((s) => !Number.isNaN(s.value));

    if (priced.length === 0) {
      return null;
    }

    let xMin = since ?? Math.min(...steps.map((s) => s.start));
    let xMax = Math.max(...steps.map((s) => s.end));
    let yMin = Math.min(...priced.map((s) => s.value));
    let yMax = Math.max(...priced.map((s) => s.value));

    // Always show at least one day, and some margin around the prices
    if (xMax - xMin < DAY) {
      xMin -= DAY / 2;
      xMax += DAY / 2;
    }

    const margin = (yMax - yMin) * 0.1 || yMax * 0.1 || 1;
    yMin = Math.max(0, yMin - margin);
    yMax += margin;

    return { xMin, xMax, yMin, yMax };
  }, [visible, since]);

  if (!bounds) {
    return (
      <Text c='dimmed' ta='center' size='sm'>
        No prices in this period.
      </Text>
    );
  }

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (t: number) => PAD.left + ((t - bounds.xMin) / (bounds.xMax - bounds.xMin)) * plotW;
  const y = (v: number) => PAD.top + plotH - ((v - bounds.yMin) / (bounds.yMax - bounds.yMin)) * plotH;
  const time = timeTicks(bounds.xMin, bounds.xMax);

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width='100%' role='img' aria-label='Price history'>
        {valueTicks(bounds.yMin, bounds.yMax).map((v) => (
          <g key={`y-${v}`}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(v)} y2={y(v)} stroke={gridColor} strokeWidth={1} />
            <text x={PAD.left - 8} y={y(v) + 4} textAnchor='end' fontSize={11} fill={textColor}>
              {formatPrice(v, currency)}
            </text>
          </g>
        ))}
        {time.ticks.map((t) => (
          <g key={`x-${t}`}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={PAD.top}
              y2={PAD.top + plotH}
              stroke={gridColor}
              strokeWidth={1}
              strokeDasharray='2 4'
            />
            <text x={x(t)} y={HEIGHT - 10} textAnchor='middle' fontSize={11} fill={textColor}>
              {time.label(t)}
            </text>
          </g>
        ))}
        {visible.map((s) => {
          // One path for each run of steps with prices: the periods without prices are gaps
          const runs: (typeof s.steps)[] = [];

          for (const step of s.steps) {
            if (Number.isNaN(step.value)) {
              runs.push([]);
            } else if (runs.length === 0) {
              runs.push([step]);
            } else {
              runs[runs.length - 1].push(step);
            }
          }

          return (
            <g key={s.label}>
              {runs
                .filter((run) => run.length > 0)
                .map((run) => {
                  let d = `M ${x(run[0].start)} ${y(run[0].value)}`;

                  for (const step of run) {
                    d += ` V ${y(step.value)} H ${x(step.end)}`;
                  }

                  return (
                    <path
                      key={run[0].start}
                      d={d}
                      fill='none'
                      stroke={s.color}
                      strokeWidth={2.5}
                      strokeLinejoin='round'
                    />
                  );
                })}
              {s.steps
                .filter((step) => !Number.isNaN(step.value))
                .map((step) => (
                  <circle key={step.start} cx={x(step.start)} cy={y(step.value)} r={4} fill={s.color}>
                    <title>{`${s.label}: ${formatPrice(step.value, currency)} (from ${formatDate(new Date(step.from).toISOString())})`}</title>
                  </circle>
                ))}
              {s.steps
                .filter((step) => Number.isNaN(step.value))
                .map((step) => (
                  <line
                    key={`gap-${step.start}`}
                    x1={x(step.start)}
                    x2={x(step.end)}
                    y1={PAD.top + plotH}
                    y2={PAD.top + plotH}
                    stroke={s.color}
                    strokeWidth={3}
                    strokeDasharray='4 4'
                  >
                    <title>{`${s.label}: no prices from ${formatDate(new Date(step.from).toISOString())}`}</title>
                  </line>
                ))}
            </g>
          );
        })}
      </svg>
      <Group gap='md' justify='center'>
        {series.map((s) => (
          <Group key={s.label} gap={6}>
            <svg width={12} height={12} aria-hidden='true'>
              <rect width={12} height={12} rx={2} fill={s.color} />
            </svg>
            <Text size='sm'>{s.label}</Text>
          </Group>
        ))}
      </Group>
    </div>
  );
}
