import {useEffect, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Avatar} from '@astryxdesign/core/Avatar';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Code} from '@astryxdesign/core/Code';
import {Divider} from '@astryxdesign/core/Divider';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Link} from '@astryxdesign/core/Link';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Spinner} from '@astryxdesign/core/Spinner';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';
import type {TableColumn} from '@astryxdesign/core/Table';

import {api, clvPts, pct, shortHash, units, when, type Config, type Pick, type Profile, type Rule} from '../api';
import {ResultText, Sparkline, StatusBadge, TierBadge} from '../components/bits';

const SOURCE = {discord: 'Discord', x: 'X import', demo: 'Demo seed'};

const pickColumns: TableColumn<Pick>[] = [
  {
    key: 'posted_at',
    header: 'Posted',
    width: pixel(120),
    renderCell: p => (
      <VStack gap={0.5}>
        <Text hasTabularNumbers>{when(p.posted_at)}</Text>
        <Text type="supporting">{SOURCE[p.source]}</Text>
      </VStack>
    ),
  },
  {
    key: 'pick',
    header: 'Pick',
    width: proportional(2),
    renderCell: p => (
      <VStack gap={0.5}>
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Text weight="semibold">{`${p.side_label} ML · ${p.units}u`}</Text>
          <StatusBadge status={p.status} />
        </HStack>
        <Text type="supporting" maxLines={1}>
          {`${p.event_title} · starts ${when(p.start_time)}`}
        </Text>
      </VStack>
    ),
  },
  {
    key: 'lock_price',
    header: 'Gemini at lock',
    width: pixel(110),
    align: 'end',
    renderCell: p => (
      <Link href={p.gemini_url} isExternalLink>
        {pct(p.lock_price)}
      </Link>
    ),
  },
  {
    key: 'line',
    header: 'Gemini line',
    width: pixel(140),
    renderCell: p => <Sparkline pick={p} />,
  },
  {
    key: 'clv',
    header: 'Close · CLV',
    width: pixel(120),
    align: 'end',
    renderCell: p => (
      <VStack gap={0.5}>
        <Text hasTabularNumbers justify="end" display="block">
          {pct(p.close_price)}
        </Text>
        <Text type="supporting" hasTabularNumbers justify="end" display="block">
          {clvPts(p.clv)}
        </Text>
      </VStack>
    ),
  },
  {
    key: 'result',
    header: 'Result',
    width: pixel(100),
    align: 'end',
    renderCell: p => (
      <VStack gap={0.5}>
        <Text justify="end" display="block">
          <ResultText result={p.result} />
        </Text>
        <Text type="supporting" hasTabularNumbers justify="end" display="block">
          {p.result === 'win' || p.result === 'loss' ? units(p.profit) : '–'}
        </Text>
      </VStack>
    ),
  },
  {
    key: 'proof',
    header: 'Proof',
    width: pixel(150),
    renderCell: p => (
      <VStack gap={0.5}>
        <Text maxLines={1}>
          <Code size="inherit">{shortHash(p.commit_hash)}</Code>
        </Text>
        {p.tx_url ? (
          <Link href={p.tx_url} isExternalLink>
            Arc transaction
          </Link>
        ) : (
          <Text type="supporting">{p.source === 'demo' ? 'Demo, not anchored' : 'Unanchored'}</Text>
        )}
      </VStack>
    ),
  },
];

const ruleColumns: TableColumn<Rule & Record<string, unknown>>[] = [
  {key: 'label', header: 'Rule', width: proportional(3), renderCell: r => <Text>{r.label}</Text>},
  {
    key: 'required',
    header: 'Needs',
    width: pixel(90),
    align: 'end',
    renderCell: r => <Text hasTabularNumbers>{r.key === 'untouched' ? `≤ ${r.required}` : r.required}</Text>,
  },
  {key: 'actual', header: 'Has', width: pixel(90), align: 'end', renderCell: r => <Text hasTabularNumbers>{r.actual}</Text>},
  {
    key: 'passed',
    header: '',
    width: pixel(90),
    align: 'end',
    renderCell: r => (
      <Text weight="semibold" color={r.passed ? 'primary' : 'secondary'}>
        {r.passed ? 'Pass' : r.key === 'untouched' || r.key === 'pregame' ? 'Fails' : 'Not yet'}
      </Text>
    ),
  },
];

export default function ProfilePage({handle, config}: {handle: string; config: Config | null}) {
  const [data, setData] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.profile(handle).then(setData).catch(e => setError(String(e)));
  }, [handle]);

  if (error) {
    return <EmptyState title="Capper not found" description={error} actions={<Button label="Back to leaderboard" href="/" variant="secondary" />} />;
  }
  if (!data) {
    return <Spinner label="Loading record" />;
  }

  const {capper, record, picks} = data;
  const badgeUrl = `${window.location.origin}/badge/${capper.handle}.svg`;
  const embed = `<a href="${window.location.origin}/c/${capper.handle}"><img src="${badgeUrl}" alt="Receipts: Gemini-graded record"></a>`;
  const failing = record.rules.filter(r => !r.passed);
  const flagged = picks.filter(p => p.status !== 'locked');

  return (
    <VStack gap={8}>
      <VStack gap={4}>
        <HStack gap={4} vAlign="center" wrap="wrap">
          <Avatar name={capper.display_name} src={capper.avatar_url ?? undefined} size="lg" tooltip={false} />
          <StackItem size="fill">
            <VStack gap={1}>
              <HStack gap={3} vAlign="center" wrap="wrap">
                <Heading level={1}>{capper.display_name}</Heading>
                <TierBadge tier={record.tier} />
              </HStack>
              <Text type="supporting">
                {`Gemini-graded record · last ${config?.window_days ?? 30} days${capper.demo ? ' · demo capper' : ''}${capper.x_handle ? ` · @${capper.x_handle} on X` : ''}`}
              </Text>
            </VStack>
          </StackItem>
          <img src={`/badge/${capper.handle}.svg`} alt={`${capper.display_name} Receipts badge`} height={56} />
        </HStack>

        <MetadataList columns="multi" orientation="vertical" label={{position: 'top'}}>
          <MetadataListItem label="Record">{`${record.wins}-${record.losses}${record.voids ? ` (${record.voids} void)` : ''}`}</MetadataListItem>
          <MetadataListItem label="Units at Gemini prices">{units(record.units)}</MetadataListItem>
          <MetadataListItem label="ROI">{pct(record.roi)}</MetadataListItem>
          <MetadataListItem label="Avg closing line value">{clvPts(record.avg_clv)}</MetadataListItem>
          <MetadataListItem label="Pending">{String(record.pending)}</MetadataListItem>
          <MetadataListItem label="Edits / deletes">{`${record.edits} / ${record.deletes}`}</MetadataListItem>
        </MetadataList>
      </VStack>

      {flagged.length > 0 && (
        <Banner
          status="warning"
          title={`${flagged.length} pick${flagged.length > 1 ? 's' : ''} changed after lock`}
          description="Edited and deleted picks stay on the record exactly as committed, and any edit or delete in the window blocks verification."
        />
      )}

      <VStack gap={3}>
        <Heading level={2}>Verification</Heading>
        <Text color="secondary">
          {failing.length === 0
            ? record.tier === 'sharp'
              ? 'Every rule passes, units are positive at Gemini prices, and the average closing line value is non-negative.'
              : 'Every rule passes. Sharp also needs positive units and non-negative closing line value.'
            : `Not verified yet: ${failing.map(r => r.label.toLowerCase()).join('; ')}.`}
        </Text>
        <Table<Rule & Record<string, unknown>>
          data={record.rules as (Rule & Record<string, unknown>)[]}
          columns={ruleColumns}
          idKey="key"
          density="compact"
        />
      </VStack>

      <VStack gap={3}>
        <Heading level={2}>Every pick</Heading>
        <Text color="secondary">
          The hash commits to the post ID (which encodes the post time), the text, the Gemini market and the Gemini
          price at lock. The dashed line in each chart is the lock price.
        </Text>
        {picks.length === 0 ? (
          <EmptyState title="No picks yet" description="Picks show up here the moment the bot locks them." />
        ) : (
          <Table<Pick> data={picks} columns={pickColumns} idKey="id" density="balanced" hasHover />
        )}
      </VStack>

      <Divider />

      <VStack gap={3}>
        <Heading level={2}>Embed the badge</Heading>
        <Text color="secondary">For a Whop storefront, X bio link or website. It updates live.</Text>
        <Text maxLines={2}>
          <Code>{embed}</Code>
        </Text>
        <HStack gap={2}>
          <Button
            label={copied ? 'Copied' : 'Copy embed code'}
            variant="secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(embed).then(() => setCopied(true));
            }}
          />
        </HStack>
      </VStack>
    </VStack>
  );
}
