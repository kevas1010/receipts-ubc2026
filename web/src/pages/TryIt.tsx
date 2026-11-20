import {useEffect, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Link} from '@astryxdesign/core/Link';
import {TextInput} from '@astryxdesign/core/TextInput';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';
import type {TableColumn} from '@astryxdesign/core/Table';

import {api, pct, when, type Preview, type VenueQuote} from '../api';

type QuoteRow = VenueQuote & Record<string, unknown>;

const quoteColumns: TableColumn<QuoteRow>[] = [
  {
    key: 'venue',
    header: 'Venue',
    width: proportional(2),
    renderCell: q => (
      <HStack gap={2} vAlign="center">
        <Text weight={q.primary ? 'semibold' : 'normal'}>{q.venue}</Text>
        {q.primary && <Text type="supporting">grades this pick</Text>}
      </HStack>
    ),
  },
  {
    key: 'price',
    header: 'Price (implied)',
    width: pixel(130),
    align: 'end',
    renderCell: q => <Text hasTabularNumbers>{pct(q.price)}</Text>,
  },
  {
    key: 'cheapest',
    header: 'Cheapest',
    width: pixel(100),
    align: 'end',
    renderCell: q => <Text>{q.cheapest ? '★' : ''}</Text>,
  },
  {
    key: 'url',
    header: '',
    width: pixel(130),
    align: 'end',
    renderCell: q =>
      q.url ? (
        <Link href={q.url} isExternalLink>
          {q.primary ? 'Tail on Gemini' : 'Open'}
        </Link>
      ) : null,
  },
];

const FIXED_EXAMPLES = ['Lakers -4.5 2u', '3-leg parlay Duke ML'];

export default function TryPage() {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<string[]>([]);

  // Examples come from tonight's real Gemini moneylines, so they always lock.
  useEffect(() => {
    api
      .markets()
      .then(ms => {
        const picks = ms
          .filter(m => m.sides.every(s => s.price != null))
          .slice(0, 3)
          .map((m, i) => `${m.sides[i % 2].label} ${i === 1 ? 'moneyline' : 'ML'} ${i + 1}u`);
        setLive(picks);
      })
      .catch(() => setLive([]));
  }, []);

  const run = (value = text) => {
    if (!value.trim()) {
      return;
    }
    setText(value);
    setBusy(true);
    setError(null);
    api
      .preview(value)
      .then(setResult)
      .catch(e => setError(String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <VStack gap={6}>
      <VStack gap={2}>
        <Heading level={1}>Try a pick</Heading>
        <Text color="secondary">
          Type a pick the way a capper would post it in Discord. Receipts parses it, finds the live Gemini moneyline
          and shows the price it would lock at. Nothing is saved.
        </Text>
      </VStack>

      <HStack gap={2} vAlign="end" wrap="wrap">
        <StackItem size="fill">
          <TextInput
            label="Pick"
            value={text}
            onChange={setText}
            placeholder={live[0] ?? "Arizona ML 2u"}
            onKeyDown={(e: React.KeyboardEvent) => {
              if (e.key === 'Enter') {
                run();
              }
            }}
          />
        </StackItem>
        <Button label="Check on Gemini" variant="primary" onClick={() => run()} isLoading={busy} />
      </HStack>
      <HStack gap={2} wrap="wrap" vAlign="center">
        <Text type="supporting">Examples:</Text>
        {[...live, ...FIXED_EXAMPLES].map(ex => (
          <Button key={ex} label={ex} variant="ghost" size="sm" onClick={() => run(ex)} />
        ))}
      </HStack>

      {error && <Banner status="error" title="Request failed" description={error} />}
      {result && !result.matches && (
        <Banner status={result.ok ? 'success' : 'warning'} title={result.ok ? 'Would lock' : 'Would not lock'} description={result.message} />
      )}
      {result?.matches?.length ? (
        <VStack gap={4}>
          <Banner
            status={result.ok ? 'success' : 'info'}
            title={result.ok ? `Would lock: ${result.matches[0].side} ML · ${result.units}u` : result.message}
            description={result.ok ? `${result.matches[0].title} · starts ${result.matches[0].start_time ? when(result.matches[0].start_time) : 'TBD'}` : undefined}
          />
          {result.ok ? (
            <VStack gap={2}>
              <Heading level={2}>Where to tail</Heading>
              <Text type="supporting">
                Gemini is listed first because it grades the pick. The star goes to whichever venue is actually
                cheapest. Verified cappers' lock receipts show this comparison.
              </Text>
              <Table<QuoteRow>
                data={result.matches[0].venues as QuoteRow[]}
                columns={quoteColumns}
                idKey="venue"
                density="balanced"
              />
            </VStack>
          ) : (
            <VStack gap={2}>
              {result.matches.map(m => (
                <Text key={m.ticker}>
                  {`${m.side} in ${m.title} · ${pct(m.price)} on Gemini`}
                </Text>
              ))}
            </VStack>
          )}
        </VStack>
      ) : null}
    </VStack>
  );
}
