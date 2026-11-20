import {useCallback, useEffect, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Code} from '@astryxdesign/core/Code';
import {Divider} from '@astryxdesign/core/Divider';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {Spinner} from '@astryxdesign/core/Spinner';
import {TextInput} from '@astryxdesign/core/TextInput';

import {api, badgeUrl, pct, record, units, when, type Me, type Pick, betLabel, geminiSide} from '../api';
import AnalystDetail from '../components/AnalystDetail';

function ClaimCard({me, onChange}: {me: Me; onChange: () => void}) {
  const [handle, setHandle] = useState(me.user.claim_handle ?? '');
  const [code, setCode] = useState<string | null>(me.user.claim_code);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // While a code is out, check every few seconds whether an extension has seen it posted.
  useEffect(() => {
    if (!code) {
      return;
    }
    const t = setInterval(onChange, 4000);
    return () => clearInterval(t);
  }, [code, onChange]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setCode((await api.claim(handle)).code);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <VStack gap={4}>
      <VStack gap={1}>
        <Heading level={2}>Get Verified</Heading>
        <Text color="secondary">
          Verification is free and can't be bought. Prove the Discord account is yours and you're Verified right away. After
          that, Receipts stops scraping your old posts: your record becomes every pick you post from now on.
        </Text>
      </VStack>
      <TextInput label="Your Discord username" value={handle} onChange={setHandle} placeholder="e.g. sharpsam" />
      <Button label={code ? 'Get a new code' : 'Get my code'} variant={code ? 'secondary' : 'primary'} onClick={start} isLoading={busy} />
      {error && <Banner status="error" title="Couldn't start" description={error} />}
      {code && (
        <VStack gap={2}>
          <Text weight="semibold">Post this in any Discord channel, with this extension on:</Text>
          <Text type="large">
            <Code>{code}</Code>
          </Text>
          <HStack gap={2} vAlign="center">
            <Button label="Copy code" variant="secondary" size="sm" onClick={() => void navigator.clipboard?.writeText(code)} />
            <Spinner size="sm" aria-label="Waiting for the code to show up in Discord" />
            <Text type="supporting">Waiting to see it…</Text>
          </HStack>
          <Text type="supporting">
            Whoever posts the code becomes the verified account, so only post it from your own Discord account. You can delete
            the message afterwards.
          </Text>
        </VStack>
      )}
    </VStack>
  );
}

function PostPick({onPosted}: {onPosted: () => void}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ok: boolean; message?: string; pick?: Pick; choices?: {ticker: string; title: string; side: string; start_time: string}[]} | null>(null);

  const submit = async (ticker?: string) => {
    setBusy(true);
    try {
      const r = await api.postPick(text, ticker);
      setResult(r);
      if (r.ok) {
        setText('');
        onPosted();
      }
    } catch (e) {
      setResult({ok: false, message: e instanceof Error ? e.message : String(e)});
    } finally {
      setBusy(false);
    }
  };

  return (
    <VStack gap={3}>
      <Heading level={2}>Post a pick</Heading>
      <Text type="supporting">
        Picks you post in Discord are tracked automatically. You can also lock one here: the time is the server's clock and the
        price is Gemini's live price.
      </Text>
      <HStack gap={2} vAlign="end">
        <StackItem size="fill">
          <TextInput label="Pick (moneyline, spread, total or player prop)" value={text} onChange={setText} placeholder="Pistons -5.5 2u · Maxey over 5.5 assists" />
        </StackItem>
        <Button label="Lock" variant="primary" onClick={() => submit()} isLoading={busy} isDisabled={!text.trim()} />
      </HStack>
      {result?.ok && result.pick && (
        <Banner
          status="success"
          title={`Locked: ${betLabel(result.pick)} · ${result.pick.units}u`}
          description={`${result.pick.event_title} · Gemini ${geminiSide(result.pick)} at ${pct(result.pick.lock_price)} · starts ${when(result.pick.start_time)}`}
        />
      )}
      {result && !result.ok && (
        <Banner
          status="warning"
          title="Not locked"
          description={result.message}
          endContent={
            result.choices?.length ? (
              <VStack gap={1}>
                {result.choices.map(c => (
                  <Button key={c.ticker} label={`${c.side}: ${c.title}`} variant="secondary" size="sm" onClick={() => submit(c.ticker)} />
                ))}
              </VStack>
            ) : undefined
          }
        />
      )}
    </VStack>
  );
}

function ShareCard({me}: {me: Me}) {
  const [src, setSrc] = useState<string | null>(null);
  const [url, setUrl] = useState(me.capper?.storefront_url ?? '');
  const [saved, setSaved] = useState<string | null>(null);
  const rec = me.record!;
  useEffect(() => {
    if (me.capper) {
      void badgeUrl(me.capper.handle).then(setSrc);
    }
  }, [me.capper]);
  const line = `Receipts ${rec.sharp ? 'Verified Sharp' : 'Verified'} · ${record(rec)} (${pct(rec.win_rate)}) · ${units(rec.units)} at Gemini prices${src ? ` · ${src}` : ''}`;

  return (
    <VStack gap={3}>
      <Heading level={2}>Show it off</Heading>
      {src && <img src={src} alt="Your Receipts badge" height={56} />}
      <Text maxLines={3}>
        <Code>{line}</Code>
      </Text>
      <HStack gap={2} wrap="wrap">
        <Button label="Copy share text" variant="secondary" size="sm" onClick={() => void navigator.clipboard?.writeText(line)} />
        {src && (
          <Button
            label="Copy badge embed"
            variant="secondary"
            size="sm"
            onClick={() => void navigator.clipboard?.writeText(`<img src="${src}" alt="Receipts verified record">`)}
          />
        )}
      </HStack>
      <HStack gap={2} vAlign="end">
        <StackItem size="fill">
          <TextInput label="Storefront link (Whop, Discord, Telegram)" value={url} onChange={setUrl} placeholder="https://whop.com/your-store" />
        </StackItem>
        <Button
          label="Save"
          variant="secondary"
          onClick={() =>
            api
              .storefront(url)
              .then(() => setSaved('Saved. Tailers see it on your record.'))
              .catch(e => setSaved(String(e.message ?? e)))
          }
        />
      </HStack>
      {saved && <Text type="supporting">{saved}</Text>}
    </VStack>
  );
}

type View = 'home' | 'record';

export default function Analyst() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>('home');
  const [tick, setTick] = useState(0);

  const load = useCallback(() => {
    api.me().then(setMe).catch(e => setError(String(e.message ?? e)));
  }, []);
  useEffect(load, [load]);

  if (error) {
    return <Banner status="error" title="Couldn't load your account" description={error} />;
  }
  if (!me) {
    return <Spinner label="Loading your account" />;
  }
  if (!me.capper || !me.record) {
    return <ClaimCard me={me} onChange={load} />;
  }
  return (
    <VStack gap={4}>
      <SegmentedControl label="View" value={view} onChange={v => setView(v as View)}>
        <SegmentedControlItem value="home" label="Home" />
        <SegmentedControlItem value="record" label="My record" />
      </SegmentedControl>
      {view === 'home' ? (
        <VStack gap={5}>
          <Banner
            status="success"
            title={me.record.sharp ? 'You are Verified Sharp' : 'You are Verified'}
            description={`As ${me.capper.display_name} on Discord. Tracked record: ${record(me.record)}, ${pct(me.record.win_rate)} win rate, ${units(me.record.units)} at Gemini prices.`}
          />
          <PostPick onPosted={() => {
            setTick(t => t + 1);
            load();
          }} />
          <Divider />
          <ShareCard me={me} key={tick} />
        </VStack>
      ) : (
        <AnalystDetail handle={me.capper.handle} />
      )}
    </VStack>
  );
}
