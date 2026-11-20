import {useEffect, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Avatar} from '@astryxdesign/core/Avatar';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Code} from '@astryxdesign/core/Code';
import {Divider} from '@astryxdesign/core/Divider';
import {Link} from '@astryxdesign/core/Link';
import {List, ListItem} from '@astryxdesign/core/List';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Spinner} from '@astryxdesign/core/Spinner';

import {api, betLabel, clvPts, geminiSide, pct, record, shortHash, units, when, type Pick, type Profile} from '../api';
import {IntegrityBadge, ResultText, Sparkline, TierBadge} from './bits';

function PickRow({pick}: {pick: Pick}) {
  return (
    <ListItem
      itemKey={pick.id}
      align="start"
      label={
        <HStack gap={2} vAlign="center" wrap="wrap">
          <Text weight="semibold">{`${betLabel(pick)} · ${+pick.units.toFixed(2)}u`}</Text>
          <IntegrityBadge pick={pick} />
        </HStack>
      }
      description={
        <VStack gap={1}>
          <Text type="supporting" maxLines={1}>{`${pick.event_title} · Gemini ${geminiSide(pick)}`}</Text>
          <Text type="supporting">
            {`Posted ${when(pick.posted_at)} · Gemini ${pct(pick.lock_price)} → close ${pct(pick.close_price)} (${clvPts(pick.clv)})`}
          </Text>
          <HStack gap={3} vAlign="center" wrap="wrap">
            <Sparkline pick={pick} />
            <Link href={pick.gemini_url} isExternalLink>View on Gemini</Link>
            {pick.tx_url ? (
              <Link href={pick.tx_url} isExternalLink>Arc proof</Link>
            ) : (
              <Text type="supporting" maxLines={1}>
                <Code size="inherit">{shortHash(pick.commit_hash)}</Code>
              </Text>
            )}
          </HStack>
          {pick.note && <Text type="supporting">{pick.note}</Text>}
        </VStack>
      }
      endContent={
        <VStack gap={0.5}>
          <Text justify="end" display="block"><ResultText result={pick.result} /></Text>
          <Text type="supporting" hasTabularNumbers justify="end" display="block">
            {pick.result === 'win' || pick.result === 'loss' ? units(pick.profit) : ''}
          </Text>
        </VStack>
      }
    />
  );
}

export function PickList({picks, empty}: {picks: Pick[]; empty: string}) {
  if (!picks.length) {
    return <Text type="supporting">{empty}</Text>;
  }
  return (
    <List hasDividers density="compact">
      {picks.map(p => (
        <PickRow key={p.id} pick={p} />
      ))}
    </List>
  );
}

export default function AnalystDetail({handle, onBack, badgeSrc}: {handle: string; onBack?: () => void; badgeSrc?: string}) {
  const [data, setData] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api.profile(handle).then(setData).catch(e => setError(String(e.message ?? e)));
  }, [handle]);

  if (error) {
    return <Banner status="error" title="Couldn't load this analyst" description={error} />;
  }
  if (!data) {
    return <Spinner label="Loading record" />;
  }
  const {capper, record: rec, picks, earlier} = data;
  const verified = rec.verified;
  const failing = rec.rules.filter(r => !r.passed);

  return (
    <VStack gap={5}>
      {onBack && (
        <HStack>
          <Button label="Back" variant="ghost" size="sm" onClick={onBack} />
        </HStack>
      )}
      <HStack gap={3} vAlign="center">
        <Avatar name={capper.display_name} src={capper.avatar_url ?? undefined} size="lg" tooltip={false} />
        <StackItem size="fill">
          <VStack gap={1}>
            <Heading level={2}>{capper.display_name}</Heading>
            <HStack gap={2} vAlign="center" wrap="wrap">
              <TierBadge tier={rec.tier} />
              {capper.demo ? <Text type="supporting">Demo analyst</Text> : null}
            </HStack>
          </VStack>
        </StackItem>
      </HStack>

      <MetadataList columns="multi" orientation="vertical" label={{position: 'top'}}>
        <MetadataListItem label={verified ? 'Tracked record' : 'Observed record'}>{record(rec)}</MetadataListItem>
        <MetadataListItem label="Win rate">{pct(rec.win_rate)}</MetadataListItem>
        <MetadataListItem label="Units at Gemini prices">{units(rec.units)}</MetadataListItem>
        <MetadataListItem label="ROI">{pct(rec.roi)}</MetadataListItem>
        <MetadataListItem label="Avg closing line">{clvPts(rec.avg_clv)}</MetadataListItem>
        <MetadataListItem label="Pending">{String(rec.pending)}</MetadataListItem>
      </MetadataList>

      {verified ? (
        <Text type="supporting">
          {`Verified ${rec.verified_at ? when(rec.verified_at) : ''}. Receipts stopped scraping this analyst then; this record is every pick they've posted since, graded on Gemini Predictions.`}
        </Text>
      ) : (
        <Banner
          status="info"
          title="Observed record"
          description={`Graded from ${rec.graded + rec.pending} picks tailers' extensions have seen in ${rec.channels || 1} channel${rec.channels === 1 ? '' : 's'}. It may be incomplete. Not registering doesn't mean an analyst is hiding anything.`}
        />
      )}

      {badgeSrc && verified && <img src={badgeSrc} alt={`${capper.display_name} Receipts badge`} height={56} />}

      {capper.storefront_url && (
        <Link href={capper.storefront_url} isExternalLink isStandalone>
          Subscribe to {capper.display_name}
        </Link>
      )}

      {verified && rec.rules.length > 0 && (
        <VStack gap={2}>
          <Heading level={3}>Verified Sharp</Heading>
          <Text type="supporting">
            {failing.length === 0
              ? 'Earned: a full window of untouched, pregame picks with positive units and closing line value.'
              : `Not yet: ${failing.map(r => `${r.label.toLowerCase()} (${r.actual}, needs ${r.required})`).join('; ')}.`}
          </Text>
        </VStack>
      )}

      <Divider />
      <VStack gap={2}>
        <Heading level={3}>{verified ? 'Picks since verification' : 'Observed picks'}</Heading>
        <PickList picks={picks} empty={verified ? 'No picks posted since verification yet.' : 'No picks observed yet.'} />
      </VStack>

      {verified && earlier.length > 0 && (
        <VStack gap={2}>
          <Heading level={3}>Before verification</Heading>
          <Text type="supporting">Scraped history from before they claimed the account. Not part of the verified record.</Text>
          <PickList picks={earlier} empty="" />
        </VStack>
      )}
    </VStack>
  );
}
