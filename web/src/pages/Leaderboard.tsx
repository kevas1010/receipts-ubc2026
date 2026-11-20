import {useEffect, useMemo, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Avatar} from '@astryxdesign/core/Avatar';
import {Link} from '@astryxdesign/core/Link';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Spinner} from '@astryxdesign/core/Spinner';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {
  Table,
  pixel,
  proportional,
  useTableSortable,
  useTableSortableState,
} from '@astryxdesign/core/Table';
import type {TableColumn, TableSortComparator} from '@astryxdesign/core/Table';

import {api, clvPts, pct, units, type Config, type LeaderRow} from '../api';
import {TierBadge} from '../components/bits';

const TIER_RANK = {sharp: 0, verified: 1, unverified: 2};

const columns: TableColumn<LeaderRow>[] = [
  {
    key: 'display_name',
    header: 'Capper',
    width: proportional(2),
    sortable: true,
    renderCell: row => (
      <HStack gap={2} vAlign="center">
        <Avatar name={row.display_name} src={row.avatar_url ?? undefined} size="sm" tooltip={false} />
        <Link href={`/c/${row.handle}`}>{row.display_name}</Link>
      </HStack>
    ),
  },
  {
    key: 'tier',
    header: 'Status',
    width: pixel(150),
    sortable: true,
    renderCell: row => <TierBadge tier={row.tier} />,
  },
  {
    key: 'record',
    header: 'Record',
    width: pixel(90),
    align: 'end',
    sortable: true,
    renderCell: row => <Text hasTabularNumbers>{`${row.wins}-${row.losses}`}</Text>,
  },
  {
    key: 'units',
    header: 'Units (Gemini prices)',
    width: pixel(170),
    align: 'end',
    sortable: true,
    renderCell: row => (
      <Text hasTabularNumbers weight="semibold">
        {units(row.units)}
      </Text>
    ),
  },
  {
    key: 'roi',
    header: 'ROI',
    width: pixel(80),
    align: 'end',
    sortable: true,
    renderCell: row => <Text hasTabularNumbers>{pct(row.roi)}</Text>,
  },
  {
    key: 'avg_clv',
    header: 'Avg CLV',
    width: pixel(100),
    align: 'end',
    sortable: true,
    renderCell: row => <Text hasTabularNumbers>{clvPts(row.avg_clv)}</Text>,
  },
  {
    key: 'rules',
    header: 'Rules passed',
    width: pixel(120),
    align: 'end',
    renderCell: row => {
      const passed = row.rules.filter(r => r.passed).length;
      const failed = row.rules.find(r => !r.passed);
      return (
        <Text hasTabularNumbers color={failed ? 'secondary' : 'primary'} maxLines={1}>
          {`${passed}/${row.rules.length}`}
        </Text>
      );
    },
  },
];

const COMPARATORS: Partial<Record<string, TableSortComparator<LeaderRow>>> = {
  display_name: (a, b) => a.display_name.localeCompare(b.display_name),
  tier: (a, b) => TIER_RANK[a.tier] - TIER_RANK[b.tier],
  record: (a, b) => a.wins - a.losses - (b.wins - b.losses),
  units: (a, b) => a.units - b.units,
  roi: (a, b) => a.roi - b.roi,
  avg_clv: (a, b) => (a.avg_clv ?? -1) - (b.avg_clv ?? -1),
};

export default function LeaderboardPage({config}: {config: Config | null}) {
  const [rows, setRows] = useState<LeaderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.leaderboard().then(setRows).catch(e => setError(String(e)));
  }, []);

  const data = useMemo(() => rows ?? [], [rows]);
  const {sortedData, sortConfig} = useTableSortableState<LeaderRow>({data, comparators: COMPARATORS});
  const sortPlugin = useTableSortable<LeaderRow>(sortConfig);

  const verified = data.filter(r => r.verified).length;
  const totalPicks = data.reduce((n, r) => n + r.graded + r.pending, 0);

  return (
    <VStack gap={6}>
      <VStack gap={3}>
        <HStack gap={3} vAlign="center" wrap="wrap">
          <StackItem size="fill">
            <Heading level={1}>Every pick, locked before tip-off</Heading>
          </StackItem>
        </HStack>
        <Text color="secondary">
          Cappers post moneylines in Discord as usual. Receipts locks each one with the live Gemini price, catches
          edits and deletes, and grades it from Gemini settlement. The badge goes to a complete, untouched month,
          not a hot streak.
        </Text>
      </VStack>

      <MetadataList columns="multi" orientation="vertical" label={{position: 'top'}}>
        <MetadataListItem label="Cappers tracked">{String(data.length)}</MetadataListItem>
        <MetadataListItem label="Verified">{String(verified)}</MetadataListItem>
        <MetadataListItem label="Picks on record">{String(totalPicks)}</MetadataListItem>
        <MetadataListItem label="Window">
          {config ? `${config.window_days} days${config.demo_mode ? ' (demo)' : ''}` : '–'}
        </MetadataListItem>
        <MetadataListItem label="Grader">Gemini Predictions</MetadataListItem>
      </MetadataList>

      {error ? (
        <EmptyState title="Couldn't load the leaderboard" description={error} />
      ) : rows == null ? (
        <Spinner label="Loading cappers" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No picks yet"
          description="Post a moneyline in a #moneyline channel with the bot installed, or run the demo seed."
        />
      ) : (
        <Table<LeaderRow>
          data={sortedData}
          columns={columns}
          idKey="id"
          density="balanced"
          hasHover
          textOverflow="truncate"
          plugins={{sort: sortPlugin}}
        />
      )}
    </VStack>
  );
}
