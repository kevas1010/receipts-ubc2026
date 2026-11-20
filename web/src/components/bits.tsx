import {useEffect, useState} from 'react';

import {Badge} from '@astryxdesign/core/Badge';
import {Text} from '@astryxdesign/core/Text';
import {Spinner} from '@astryxdesign/core/Spinner';

import {api, type Candle, type Pick, type Tier} from '../api';

/** Tier is a category, so it uses colour variants, not the loud semantic ones. */
export function TierBadge({tier}: {tier: Tier}) {
  if (tier === 'sharp') {
    return <Badge variant="green" label="Verified · Sharp" />;
  }
  if (tier === 'verified') {
    return <Badge variant="blue" label="Verified" />;
  }
  return <Text type="supporting">Not verified</Text>;
}

export function ResultText({result}: {result: Pick['result']}) {
  const label = {win: 'Win', loss: 'Loss', void: 'Void', pending: 'Pending'}[result];
  return (
    <Text weight={result === 'win' ? 'semibold' : 'normal'} color={result === 'pending' || result === 'void' ? 'secondary' : 'primary'}>
      {label}
    </Text>
  );
}

/** Edits and deletes are the exception worth shouting about, so only they get a badge. */
export function StatusBadge({status}: {status: Pick['status']}) {
  if (status === 'deleted') {
    return <Badge variant="error" label="Deleted after lock" />;
  }
  if (status === 'edited') {
    return <Badge variant="warning" label="Edited after lock" />;
  }
  return null;
}

/**
 * Gemini hourly price for the picked side, from lock to game start.
 * Dashed line = Gemini price when the pick locked; the end of the line is the close.
 */
export function Sparkline({pick, width = 120, height = 28}: {pick: Pick; width?: number; height?: number}) {
  const [data, setData] = useState<Candle[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .candles(pick.id)
      .then(r => live && setData(r.candles))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [pick.id]);

  if (failed) {
    return <Text type="supporting">–</Text>;
  }
  if (data == null) {
    return <Spinner size="sm" aria-label="Loading Gemini price history" />;
  }
  const posted = new Date(pick.posted_at).getTime() - 6 * 3600_000;
  const pts = data.filter(c => c.t >= posted);
  if (pts.length < 2) {
    return <Text type="supporting">–</Text>;
  }
  const ys = pts.map(p => p.close).concat(pick.lock_price ?? []);
  const lo = Math.min(...ys) - 0.02;
  const hi = Math.max(...ys) + 0.02;
  const x = (i: number) => (i / (pts.length - 1)) * (width - 4) + 2;
  const y = (v: number) => height - 2 - ((v - lo) / (hi - lo)) * (height - 4);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.close).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1].close;
  const up = pick.lock_price != null && last >= pick.lock_price;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Gemini price moved from ${Math.round((pick.lock_price ?? pts[0].close) * 100)}% to ${Math.round(last * 100)}%`}>
      {pick.lock_price != null && (
        <line
          x1={0}
          x2={width}
          y1={y(pick.lock_price)}
          y2={y(pick.lock_price)}
          stroke="var(--color-border-emphasized)"
          strokeDasharray="3 3"
        />
      )}
      <path
        d={d}
        fill="none"
        strokeWidth={1.75}
        stroke={up ? 'var(--color-data-categorical-green)' : 'var(--color-data-categorical-orange)'}
      />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill="var(--color-text-primary)" />
    </svg>
  );
}
