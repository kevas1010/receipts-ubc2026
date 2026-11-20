import {useState} from 'react';

import {VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {TextInput} from '@astryxdesign/core/TextInput';

import {api, saveSession, type Role, type Session} from '../api';

const ROLE_COPY: Record<Role, {title: string; body: string}> = {
  tailer: {
    title: 'I tail picks',
    body: "See how good every pick seller in your Discord channels really is. Receipts grades the picks they've posted against Gemini Predictions results, as you scroll.",
  },
  analyst: {
    title: 'I post picks',
    body: 'Claim your Discord account to get Verified. From then on every pick you post is tracked and graded on Gemini, and you get a badge with your real win rate to show buyers.',
  },
};

export default function Login({session, onDone}: {session: Session; onDone: (s: Session) => void}) {
  const [role, setRole] = useState<Role>('tailer');
  const [name, setName] = useState('');
  const [apiBase, setApiBase] = useState(session.apiBase);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveSession({apiBase: apiBase.trim().replace(/\/$/, '')});
      const res = await api.login(role, name || (role === 'tailer' ? 'Tailer' : 'Analyst'));
      onDone(await saveSession({token: res.token, role, name: res.user.name}));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <VStack gap={5}>
      <VStack gap={2}>
        <Heading level={1}>Every pick has a receipt</Heading>
        <Text color="secondary">Gemini-graded records for sports pick sellers, right inside Discord.</Text>
      </VStack>

      <SegmentedControl label="Sign in as" value={role} onChange={v => setRole(v as Role)}>
        <SegmentedControlItem value="tailer" label="Tailer" />
        <SegmentedControlItem value="analyst" label="Analyst" />
      </SegmentedControl>

      <VStack gap={1}>
        <Heading level={3}>{ROLE_COPY[role].title}</Heading>
        <Text type="supporting">{ROLE_COPY[role].body}</Text>
      </VStack>

      <TextInput label="Your name" value={name} onChange={setName} placeholder={role === 'tailer' ? 'Tailer' : 'Your capper name'} />
      <TextInput
        label="Receipts server"
        description="Where the Receipts API runs. Leave as is for the local demo."
        value={apiBase}
        onChange={setApiBase}
      />
      {error && <Banner status="error" title="Couldn't sign in" description={error} />}
      <Button label={`Continue as ${role === 'tailer' ? 'a tailer' : 'an analyst'}`} variant="primary" onClick={submit} isLoading={busy} />
    </VStack>
  );
}
