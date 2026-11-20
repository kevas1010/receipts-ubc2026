import {useEffect, useState} from 'react';

import {Layout, LayoutContent, VStack} from '@astryxdesign/core/Layout';
import {TopNav, TopNavHeading, TopNavItem} from '@astryxdesign/core/TopNav';
import {Banner} from '@astryxdesign/core/Banner';
import {Text} from '@astryxdesign/core/Text';
import {Link} from '@astryxdesign/core/Link';

import {api, type Config} from './api';
import LeaderboardPage from './pages/Leaderboard';
import ProfilePage from './pages/Profile';
import TryPage from './pages/TryIt';

function route(path: string) {
  const m = path.match(/^\/c\/([^/]+)/);
  if (m) {
    return {page: 'profile' as const, handle: decodeURIComponent(m[1])};
  }
  if (path.startsWith('/try')) {
    return {page: 'try' as const};
  }
  return {page: 'leaderboard' as const};
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const r = route(window.location.pathname);

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);

  return (
    <Layout
      height="fill"
      contentWidth={1120}
      header={
        <TopNav
          heading={
            <TopNavHeading heading="Receipts" headingHref="/" subheading="Graded on Gemini Predictions" />
          }
          startContent={
            <>
              <TopNavItem label="Leaderboard" href="/" isSelected={r.page === 'leaderboard'} />
              <TopNavItem label="Try a pick" href="/try" isSelected={r.page === 'try'} />
            </>
          }
        />
      }
      content={
        <LayoutContent padding={0}>
          <VStack padding={4} gap={6}>
            {config?.demo_mode && (
              <Banner
                status="info"
                title="Demo window"
                description={`A month can't happen in 48 hours. This demo verifies over ${config.window_days} days (${config.min_picks}+ graded picks, ${config.min_active_days}+ active days), seeded from real settled Gemini games. The real badge needs 30 days.`}
              />
            )}
            {r.page === 'leaderboard' && <LeaderboardPage config={config} />}
            {r.page === 'profile' && <ProfilePage handle={r.handle} config={config} />}
            {r.page === 'try' && <TryPage />}
            <Text type="supporting">
              Prices, closing lines and results come from{' '}
              <Link href="https://www.gemini.com/predictions" isExternalLink>
                Gemini Predictions
              </Link>
              . Gemini doesn't endorse any capper. Proofs are keccak commits on Arc testnet.
            </Text>
          </VStack>
        </LayoutContent>
      }
    />
  );
}
