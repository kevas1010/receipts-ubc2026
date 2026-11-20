import {useCallback, useEffect, useState} from 'react';

import {HStack, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Code} from '@astryxdesign/core/Code';
import {Divider} from '@astryxdesign/core/Divider';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {Spinner} from '@astryxdesign/core/Spinner';
import {List, ListItem} from '@astryxdesign/core/List';
import {Step, Stepper} from '@astryxdesign/core/Stepper';
import {TextInput} from '@astryxdesign/core/TextInput';

import {api, badgeUrl, pct, record, units, when, type Me, type Pick, type SimMarket, type SimResult, betLabel, geminiSide} from '../api';
import AnalystDetail from '../components/AnalystDetail';

function Steps({active}: {active: number}) {
  return (
    <Stepper activeStep={active} orientation="vertical" density="compact" label="Verification">
      <Step step={0} label="Claim your Discord account" description="Post a one-time code from the account you sell picks on." />
      <Step step={1} label="Get your PickProof credential" description="A public hash of your account and verification time." />
      <Step step={2} label="Post picks through PickProof" description="Each pick is hash-committed, priced and graded on Gemini." />
    </Stepper>
  );
}

function ClaimCard({me, onChange}: {me: Me; onChange: () => void}) {
  const [handle, setHandle] = useState(me.user.claim_handle ?? '');
  const [code, setCode] = useState<string | null>(me.user.claim_code);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    api.config().then(c => setDemo(c.demo_mode)).catch(() => setDemo(false));
  }, []);

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

  const demoVerify = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.demoVerify();
      onChange();
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
          Verification is free and can't be bought. Once you're verified, the customer scraper stops reading your Discord
          posts: your public record becomes the hash-committed picks you post through PickProof.
        </Text>
      </VStack>
      <Steps active={0} />
      <TextInput label="Your Discord username" value={handle} onChange={setHandle} placeholder="e.g. sharpsam" />
      <Button label={code ? 'Get a new code' : 'Get my code'} variant={code ? 'secondary' : 'primary'} onClick={start} isLoading={busy} />
      {demo && (
        <Button label="Verify instantly (demo)" variant="secondary" onClick={() => void demoVerify()} isLoading={busy} />
      )}
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

function Credential({me}: {me: Me}) {
  const c = me.capper!;
  return (
    <VStack gap={2}>
      <Steps active={2} />
      <Banner
        status="success"
        title={me.record?.sharp ? 'Verified Sharp' : 'Verified'}
        description={`As ${c.display_name}${c.demo ? ' (demo)' : ''} since ${c.verified_at ? when(c.verified_at) : 'today'}. Customers see this credential instead of a scraped record.`}
      />
      {c.credential && (
        <VStack gap={1}>
          <Text weight="semibold">PickProof credential</Text>
          <Text maxLines={2}>
            <Code>{c.credential}</Code>
          </Text>
          <Text type="supporting">keccak256(&quot;pickproof:v1:&lt;your id&gt;:&lt;verified at&gt;&quot;). Anyone can recompute it.</Text>
          <Button label="Copy credential" variant="secondary" size="sm" onClick={() => void navigator.clipboard?.writeText(c.credential!)} />
        </VStack>
      )}
    </VStack>
  );
}

const KINDS = [
  {value: 'prop', label: 'Props'},
  {value: 'spread', label: 'Spreads'},
  {value: 'total', label: 'Totals'},
  {value: 'moneyline', label: 'Moneylines'},
];

function Simulator() {
  const [kind, setKind] = useState('prop');
  const [markets, setMarkets] = useState<SimMarket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<SimMarket | null>(null);
  const [result, setResult] = useState<SimResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMarkets(null);
    setOpen(null);
    setResult(null);
    api.simMarkets(kind).then(setMarkets).catch(e => setError(String(e.message ?? e)));
  }, [kind]);

  const play = async (label: string, position: 'yes' | 'no') => {
    if (!open) {
      return;
    }
    setBusy(true);
    try {
      setResult(await api.simPick(open.ticker, label, position, 1));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <VStack gap={3}>
      <Heading level={2}>Practice on last week</Heading>
      <Text type="supporting">
        Real settled Gemini markets from the past 7 days, priced one hour before start. See how a pick would have graded and
        the hash it would get. Practice picks are never added to your record.
      </Text>
      <SegmentedControl label="Market" value={kind} onChange={v => setKind(String(v))}>
        {KINDS.map(k => (
          <SegmentedControlItem key={k.value} value={k.value} label={k.label} />
        ))}
      </SegmentedControl>
      {error && <Banner status="error" title="Simulator unavailable" description={error} />}
      {!markets && !error && <Spinner label="Loading last week's Gemini markets" />}
      {result && (
        <Banner
          status={result.result === 'win' ? 'success' : 'warning'}
          title={`${result.result.toUpperCase()} · ${result.position.toUpperCase()} ${result.contract} at ${pct(result.price)}${result.profit != null ? ` · ${units(result.profit)}` : ''}`}
          description={`${result.title}. ${result.note} Hash ${result.hash.slice(0, 18)}…`}
          isDismissable
          onDismiss={() => setResult(null)}
        />
      )}
      {open ? (
        <VStack gap={2}>
          <HStack gap={2} vAlign="center">
            <Button label="Back" variant="ghost" size="sm" onClick={() => setOpen(null)} />
            <Text weight="semibold" maxLines={1}>{open.title}</Text>
          </HStack>
          <List density="compact">
            {open.contracts.map(c => (
              <ListItem
                key={c.label}
                itemKey={c.label}
                label={c.label}
                endContent={
                  <HStack gap={1}>
                    <Button label="YES" size="sm" variant="secondary" onClick={() => void play(c.label, 'yes')} isLoading={busy} />
                    <Button label="NO" size="sm" variant="secondary" onClick={() => void play(c.label, 'no')} isLoading={busy} />
                  </HStack>
                }
              />
            ))}
          </List>
        </VStack>
      ) : (
        markets && (
          <List density="compact">
            {markets.slice(0, 25).map(m => (
              <ListItem
                key={m.ticker}
                itemKey={m.ticker}
                label={m.title}
                description={`${m.league} · ${when(m.start_time)} · ${m.contracts.length} contracts`}
                onClick={() => {
                  setResult(null);
                  setOpen(m);
                }}
              />
            ))}
          </List>
        )
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
        Lock a pick here before the game: the time is the server's clock, the price is Gemini's live price, and the pick gets a
        hash commitment nobody can edit.
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
  const line = `PickProof ${rec.sharp ? 'Verified Sharp' : 'Verified'} · ${record(rec)} (${pct(rec.win_rate)}) · ${units(rec.units)} at Gemini prices${src ? ` · ${src}` : ''}`;

  return (
    <VStack gap={3}>
      <Heading level={2}>Show it off</Heading>
      {src && <img src={src} alt="Your PickProof badge" height={56} />}
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
            onClick={() => void navigator.clipboard?.writeText(`<img src="${src}" alt="PickProof verified record">`)}
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
          <Credential me={me} />
          <Text type="supporting">
            {`Tracked record: ${record(me.record)}, ${pct(me.record.win_rate)} win rate, ${units(me.record.units)} at Gemini prices.`}
          </Text>
          <PostPick onPosted={() => {
            setTick(t => t + 1);
            load();
          }} />
          <Divider />
          <Simulator />
          <Divider />
          <ShareCard me={me} key={tick} />
        </VStack>
      ) : (
        <AnalystDetail handle={me.capper.handle} />
      )}
    </VStack>
  );
}
