import {useCallback, useEffect, useState} from 'react';

import {HStack, Layout, LayoutContent, StackItem, VStack} from '@astryxdesign/core/Layout';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Divider} from '@astryxdesign/core/Divider';
import {Spinner} from '@astryxdesign/core/Spinner';
import {Switch} from '@astryxdesign/core/Switch';
import {Heading, Text} from '@astryxdesign/core/Text';

import {api, loadSession, onFocusAnalyst, saveSession, type Session} from './api';
import Login from './pages/Login';
import Tailer from './pages/Tailer';
import Analyst from './pages/Analyst';

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [demo, setDemo] = useState<{window_days: number} | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    void loadSession().then(setSession);
    return onFocusAnalyst(setFocus);
  }, []);

  useEffect(() => {
    if (session?.token) {
      api.config().then(c => setDemo(c.demo_mode ? c : null)).catch(() => setDemo(null));
    }
  }, [session?.token]);

  const clearFocus = useCallback(() => setFocus(null), []);

  if (!session) {
    return <Spinner label="Starting PickProof" />;
  }

  const signedIn = !!session.token && !!session.role;
  return (
    <Layout
      height="fill"
      content={
        <LayoutContent padding={0}>
          <VStack padding={4} gap={4}>
            <VStack gap={2}>
              <HStack gap={2} vAlign="center">
                <StackItem size="fill">
                  <Text type="label" color="secondary">
                    Vol. 1 · {new Date().toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'})}
                  </Text>
                </StackItem>
                <Text type="label" color="secondary">Late city final</Text>
              </HStack>
              <Divider />
              <HStack gap={2} vAlign="center">
                <StackItem size="fill">
                  <Heading level={1} type="display-2">PickProof.</Heading>
                </StackItem>
                {signedIn && (
                  <Button
                    label="Sign out"
                    variant="secondary"
                    size="sm"
                    onClick={() => void saveSession({token: null, role: null, name: null}).then(setSession)}
                  />
                )}
              </HStack>
              <Divider />
              <Text type="label">
                {signedIn ? `${session.name} · ${session.role === 'tailer' ? 'Tailer edition' : 'Analyst desk'}` : 'The record of every pick · Graded on Gemini Predictions'}
              </Text>
              <Divider />
            </VStack>

            {demo && (
              <Banner
                status="info"
                title="Demo data included"
                description={`Demo analysts are seeded from real settled Gemini games and labelled. Verified Sharp uses a ${demo.window_days}-day demo window instead of 30.`}
                isDismissable
              />
            )}

            {!signedIn ? (
              <Login session={session} onDone={setSession} />
            ) : session.role === 'tailer' ? (
              <Tailer focus={focus} onFocusDone={clearFocus} />
            ) : (
              <Analyst />
            )}

            {signedIn && (
              <VStack gap={3}>
                <Divider />
                <Switch
                  label="Read picks in Discord"
                  description="Reads only messages already on your screen. Nothing is posted or sent to Discord."
                  value={session.capture}
                  onChange={v => void saveSession({capture: v}).then(setSession)}
                  size="sm"
                />
                <Text type="supporting">
                  Prices, closing lines and results come from Gemini Predictions. Gemini doesn't endorse any analyst.
                </Text>
              </VStack>
            )}
          </VStack>
        </LayoutContent>
      }
    />
  );
}
