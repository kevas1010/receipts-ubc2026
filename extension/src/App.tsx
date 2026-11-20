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
    return <Spinner label="Starting Receipts" />;
  }

  const signedIn = !!session.token && !!session.role;
  return (
    <Layout
      height="fill"
      content={
        <LayoutContent padding={0}>
          <VStack padding={4} gap={4}>
            <HStack gap={2} vAlign="center">
              <StackItem size="fill">
                <VStack gap={0.5}>
                  <Heading level={2}>Receipts</Heading>
                  <Text type="supporting">
                    {signedIn ? `${session.name} · ${session.role === 'tailer' ? 'Tailer' : 'Analyst'}` : 'Graded on Gemini Predictions'}
                  </Text>
                </VStack>
              </StackItem>
              {signedIn && (
                <Button
                  label="Sign out"
                  variant="ghost"
                  size="sm"
                  onClick={() => void saveSession({token: null, role: null, name: null}).then(setSession)}
                />
              )}
            </HStack>

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
