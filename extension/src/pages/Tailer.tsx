import {useCallback, useEffect, useState} from 'react';

import {HStack, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {FileInput} from '@astryxdesign/core/FileInput';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {Spinner} from '@astryxdesign/core/Spinner';

import {api, messagesFromExport, scanDiscord, type AnalystRow, type ScanResult} from '../api';
import {AnalystList} from '../components/bits';
import AnalystDetail from '../components/AnalystDetail';

type View = 'channel' | 'all' | 'import';

const OUTCOME_LABEL: Record<string, string> = {
  scraped: 'graded',
  tracked: 'tracked (verified)',
  before_verification: 'skipped: before verification',
  not_a_pick: 'not picks',
  rejected: 'not moneylines',
  no_game: 'no Gemini game',
  ambiguous: 'ambiguous team',
  seen: 'already seen',
  edited: 'edited',
  claimed: 'claim codes',
};

function summarize(counts?: Record<string, number>) {
  if (!counts) {
    return '';
  }
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${OUTCOME_LABEL[k] ?? k}`)
    .join(' · ');
}

function ChannelView({onOpen}: {onOpen: (h: string) => void}) {
  const [scan, setScan] = useState<ScanResult | null | undefined>(undefined);
  const [rows, setRows] = useState<AnalystRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await scanDiscord();
      setScan(s);
      setRows(s?.context.channelId ? await api.analysts(s.context.channelId) : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (scan === undefined) {
    return <Spinner label="Reading this channel" />;
  }
  if (scan === null) {
    return (
      <Banner
        status="info"
        title="Open a Discord channel"
        description="Go to a channel on discord.com where analysts post picks. If Discord was already open before you installed Receipts, reload that tab once."
        endContent={<Button label="Try again" variant="secondary" onClick={refresh} />}
      />
    );
  }
  return (
    <VStack gap={4}>
      <HStack gap={2} vAlign="center" hAlign="between" wrap="wrap">
        <VStack gap={0.5}>
          <Heading level={2}>{scan.context.channelName ? `#${scan.context.channelName}` : 'This channel'}</Heading>
          <Text type="supporting">
            {`${scan.visible} messages on screen${scan.result?.counts ? ' · ' + summarize(scan.result.counts) : ''}`}
          </Text>
        </VStack>
        <Button label="Rescan" variant="secondary" size="sm" onClick={refresh} isLoading={busy} />
      </HStack>
      {scan.result?.error && <Banner status="error" title="Receipts server error" description={scan.result.error} />}
      {error && <Banner status="error" title="Couldn't load analysts" description={error} />}
      <Text type="supporting">
        Scroll up in Discord to load older messages; Receipts grades them as they appear.
      </Text>
      {rows ? (
        <AnalystList rows={rows} onOpen={onOpen} empty="No moneyline picks found in this channel yet." />
      ) : (
        <Spinner label="Grading picks" />
      )}
    </VStack>
  );
}

function AllView({onOpen}: {onOpen: (h: string) => void}) {
  const [rows, setRows] = useState<AnalystRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.analysts().then(setRows).catch(e => setError(String(e.message ?? e)));
  }, []);
  if (error) {
    return <Banner status="error" title="Couldn't load analysts" description={error} />;
  }
  if (!rows) {
    return <Spinner label="Loading analysts" />;
  }
  return (
    <VStack gap={3}>
      <Heading level={2}>All analysts</Heading>
      <Text type="supporting">Verified first, then by win rate. Unverified records are observed and may be incomplete.</Text>
      <AnalystList rows={rows} onOpen={onOpen} empty="No analysts yet. Open a Discord picks channel or import an export." />
    </VStack>
  );
}

function ImportView() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (f: File | null) => {
    setFile(f);
    if (!f) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const msgs = messagesFromExport(JSON.parse(await f.text()));
      const totals: Record<string, number> = {};
      for (let i = 0; i < msgs.length; i += 200) {
        const r = await api.observe(msgs.slice(i, i + 200));
        Object.entries(r.counts).forEach(([k, n]) => (totals[k] = (totals[k] ?? 0) + n));
        setStatus(`${Math.min(i + 200, msgs.length)} of ${msgs.length} messages · ${summarize(totals)}`);
      }
      if (!msgs.length) {
        setStatus('No messages with text found in that file.');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <VStack gap={3}>
      <Heading level={2}>Import a channel export</Heading>
      <Text type="supporting">
        For channels too long to scroll: a JSON export (DiscordChatExporter format) of a channel you can read. Post times
        still come from the message IDs.
      </Text>
      <FileInput label="Channel export (.json)" accept=".json,application/json" value={file} onChange={f => void run(f as File | null)} isLoading={busy} />
      {status && <Banner status="success" title="Imported" description={status} />}
      {error && <Banner status="error" title="Import failed" description={error} />}
    </VStack>
  );
}

export default function Tailer({focus, onFocusDone}: {focus: string | null; onFocusDone: () => void}) {
  const [view, setView] = useState<View>('channel');
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    if (focus) {
      setOpen(focus);
      onFocusDone();
    }
  }, [focus, onFocusDone]);

  if (open) {
    return <AnalystDetail handle={open} onBack={() => setOpen(null)} />;
  }
  return (
    <VStack gap={4}>
      <SegmentedControl label="View" value={view} onChange={v => setView(v as View)}>
        <SegmentedControlItem value="channel" label="This channel" />
        <SegmentedControlItem value="all" label="All analysts" />
        <SegmentedControlItem value="import" label="Import" />
      </SegmentedControl>
      {view === 'channel' && <ChannelView onOpen={setOpen} />}
      {view === 'all' && <AllView onOpen={setOpen} />}
      {view === 'import' && <ImportView />}
    </VStack>
  );
}
