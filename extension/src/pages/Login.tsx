import {useState} from 'react';

import {HStack, VStack} from '@astryxdesign/core/Layout';
import {Heading, Text} from '@astryxdesign/core/Text';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {ClickableCard} from '@astryxdesign/core/ClickableCard';
import {List, ListItem} from '@astryxdesign/core/List';
import {TextInput} from '@astryxdesign/core/TextInput';

import {api, saveSession, type Role, type Session} from '../api';

const PATHS: Record<Role, {title: string; tagline: string; points: string[]; cta: string; placeholder: string}> = {
  tailer: {
    title: 'For customers',
    tagline: 'Before you pay for picks, see the real record.',
    points: [
      'Reads the picks analysts posted in Discord channels you can already see, including bet slip screenshots',
      'Matches every leg to the Gemini Predictions market that settles it, at Gemini prices',
      'Ranks analysts by what actually cashed, not their highlight reel',
    ],
    cta: 'Continue as a customer',
    placeholder: 'Your name',
  },
  analyst: {
    title: 'For analysts',
    tagline: 'Prove you are the real deal, then post picks nobody can edit.',
    points: [
      'Verify your Discord account and get a PickProof credential (a public hash anyone can check)',
      'Every pick you post is hash-committed and graded on Gemini; scrapers stop guessing your record',
      'Practice on last week’s settled Gemini markets before you go live',
    ],
    cta: 'Continue as an analyst',
    placeholder: 'Your capper name',
  },
};

export default function Login({session, onDone}: {session: Session; onDone: (s: Session) => void}) {
  const [role, setRole] = useState<Role | null>(null);
  const [name, setName] = useState('');
  const [apiBase, setApiBase] = useState(session.apiBase);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!role) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await saveSession({apiBase: apiBase.trim().replace(/\/$/, '')});
      const res = await api.login(role, name || (role === 'tailer' ? 'Customer' : 'Analyst'));
      onDone(await saveSession({token: res.token, role, name: res.user.name}));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!role) {
    return (
      <VStack gap={5}>
        <VStack gap={2}>
          <Heading level={1}>Proof before you tail</Heading>
          <Text color="secondary">Gemini-graded records for sports pick sellers, right inside Discord.</Text>
        </VStack>
        {(['tailer', 'analyst'] as Role[]).map(r => (
          <ClickableCard key={r} label={PATHS[r].title} onClick={() => setRole(r)} padding={4}>
            <VStack gap={2}>
              <Heading level={2}>{PATHS[r].title}</Heading>
              <Text weight="semibold">{PATHS[r].tagline}</Text>
              <List density="compact">
                {PATHS[r].points.map(p => (
                  <ListItem key={p} itemKey={p} label={<Text type="supporting">{p}</Text>} />
                ))}
              </List>
            </VStack>
          </ClickableCard>
        ))}
      </VStack>
    );
  }

  const path = PATHS[role];
  return (
    <VStack gap={4}>
      <HStack gap={2} vAlign="center">
        <Button label="Back" variant="ghost" size="sm" onClick={() => setRole(null)} />
        <Heading level={2}>{path.title}</Heading>
      </HStack>
      <Text color="secondary">{path.tagline}</Text>
      <TextInput label="Your name" value={name} onChange={setName} placeholder={path.placeholder} />
      <TextInput
        label="PickProof server"
        description="Where the PickProof API runs. Leave as is for the local demo."
        value={apiBase}
        onChange={setApiBase}
      />
      {error && <Banner status="error" title="Couldn't sign in" description={error} />}
      <Button label={path.cta} variant="primary" onClick={submit} isLoading={busy} />
    </VStack>
  );
}
