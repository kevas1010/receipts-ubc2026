import {useEffect, useState} from 'react';

import {Badge} from '@astryxdesign/core/Badge';
import {Avatar} from '@astryxdesign/core/Avatar';
import {List, ListItem} from '@astryxdesign/core/List';
import {Spinner} from '@astryxdesign/core/Spinner';
import {Text} from '@astryxdesign/core/Text';

import {api, pct, record, type AnalystRow, type Pick, type Tier} from '../api';

/** Tier is a category, so it uses colour variants, not the loud semantic ones. */
export function TierBadge({tier}: {tier: Tier}) {
  if (tier === 'sharp') {
    return <Badge variant="green" label="Verified · Sharp" />;
  }
  if (tier === 'verified') {
    return <Badge variant="blue" label="Verified" />;
  }
  return <Badge variant="neutral" label="Unverified" />;
}

export function ResultText({result}: {result: Pick['result']}) {
  const label = {win: 'Win', loss: 'Loss', void: 'Void', pending: 'Pending'}[result];
  return (
    <Text weight={result === 'win' ? 'semibold' : 'normal'} color={result === 'pending' || result === 'void' ? 'secondary' : 'primary'}>
      {label}
    </Text>
  );
}

/** Edits and deletes are the exceptions worth flagging, so only they get a status badge. */
export function IntegrityBadge({pick}: {pick: Pick}) {
  if (pick.status === 'deleted') {
    return <Badge variant="error" label="Deleted" />;
  }
  if (pick.status === 'edited' || pick.edited_flag) {
    return <Badge variant="warning" label="Edited" />;
  }
  return null;
}

/** One row per analyst: who, status, and the record at Gemini prices. */
export function AnalystList({rows, onOpen, empty}: {rows: AnalystRow[]; onOpen: (handle: string) => void; empty: string}) {
  if (rows.length === 0) {
    return <Text type="supporting">{empty}</Text>;
  }
  return (
    <List hasDividers density="balanced">
      {rows.map(r => (
        <ListItem
          key={r.id}
          itemKey={r.id}
          onClick={() => onOpen(r.handle)}
          startContent={<Avatar name={r.display_name} src={r.avatar_url ?? undefined} size="md" tooltip={false} />}
          label={r.display_name}
          description={
            r.graded || r.pending
              ? `${record(r)} · ${pct(r.win_rate)} win · ${r.units > 0 ? '+' : ''}${r.units.toFixed(1)}u${r.record_kind === 'observed' ? ' · observed' : ''}`
              : 'No graded picks yet'
          }
          endContent={<TierBadge tier={r.tier} />}
        />
      ))}
    </List>
  );
}

/**
 * Gemini hourly price for the picked side up to game start.
 * Dashed line = Gemini price when the pick was made; the dot is the close.
 */
export function Sparkline({pick, width = 96, height = 24}: {pick: Pick; width?: number; height?: number}) {
  const [data, setData] = useState<{t: number; close: number}[] | null>(null);
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
    return null;
  }
  if (data == null) {
    return <Spinner size="sm" aria-label="Loading Gemini price history" />;
  }
  const from = new Date(pick.posted_at).getTime() - 6 * 3600_000;
  const pts = data.filter(c => c.t >= from);
  if (pts.length < 2) {
    return null;
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
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={`Gemini price went from ${Math.round((pick.lock_price ?? pts[0].close) * 100)}% to ${Math.round(last * 100)}%`}>
      {pick.lock_price != null && (
        <line x1={0} x2={width} y1={y(pick.lock_price)} y2={y(pick.lock_price)} stroke="var(--color-border-emphasized)" strokeDasharray="3 3" />
      )}
      <path d={d} fill="none" strokeWidth={1.75} stroke={up ? 'var(--color-data-categorical-green)' : 'var(--color-data-categorical-orange)'} />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill="var(--color-text-primary)" />
    </svg>
  );
}
