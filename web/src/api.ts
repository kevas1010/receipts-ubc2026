export type Tier = 'sharp' | 'verified' | 'unverified';

export interface Rule {
  key: string;
  label: string;
  required: string;
  actual: string;
  passed: boolean;
}

export interface RecordStats {
  tier: Tier;
  verified: boolean;
  wins: number;
  losses: number;
  voids: number;
  pending: number;
  graded: number;
  units: number;
  staked: number;
  roi: number;
  avg_clv: number | null;
  edits: number;
  deletes: number;
  active_days: number;
  win_rate: number | null;
  rules: Rule[];
}

export interface Capper {
  id: string;
  handle: string;
  display_name: string;
  avatar_url: string | null;
  x_handle: string | null;
  demo: number;
}

export type LeaderRow = Capper & RecordStats & Record<string, unknown>;

export interface VenueQuote {
  venue: string;
  price: number | null;
  url: string | null;
  primary: boolean;
  cheapest: boolean;
  note: string | null;
}

export interface Pick extends Record<string, unknown> {
  id: number;
  source: 'discord' | 'x' | 'demo';
  post_id: string;
  text: string;
  units: number;
  event_ticker: string;
  event_title: string;
  league: string | null;
  side_label: string;
  instrument_symbol: string;
  start_time: string;
  posted_at: string;
  lock_price: number | null;
  close_price: number | null;
  result: 'pending' | 'win' | 'loss' | 'void';
  commit_hash: string;
  tx_hash: string | null;
  tx_url: string | null;
  gemini_url: string;
  status: 'locked' | 'edited' | 'deleted';
  profit: number;
  clv: number | null;
  venues: VenueQuote[];
  events: {kind: 'edit' | 'delete'; at: string; new_text: string | null}[];
}

export interface Profile {
  capper: Capper;
  record: RecordStats;
  picks: Pick[];
}

export interface Config {
  window_days: number;
  min_picks: number;
  min_active_days: number;
  demo_mode: boolean;
  show_other_venues: boolean;
  chain_enabled: boolean;
  contract: string;
  explorer: string;
}

export interface Candle {
  t: number;
  close: number;
}

export interface PreviewMatch {
  ticker: string;
  title: string;
  start_time: string | null;
  side: string;
  price: number | null;
  gemini_url: string;
  venues: VenueQuote[];
}

export interface Preview {
  ok: boolean;
  message: string;
  team?: string;
  units?: number;
  matches?: PreviewMatch[];
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  config: () => get<Config>('/api/config'),
  leaderboard: () => get<LeaderRow[]>('/api/leaderboard'),
  profile: (handle: string) => get<Profile>(`/api/cappers/${encodeURIComponent(handle)}`),
  candles: (id: number) =>
    get<{candles: Candle[]; posted_at: string; start_time: string}>(`/api/picks/${id}/candles`),
  markets: () =>
    get<{ticker: string; title: string; league: string; sides: {label: string; price: number | null}[]}[]>('/api/markets'),
  preview: (text: string) => get<Preview>(`/api/preview?text=${encodeURIComponent(text)}`),
};

// ---- formatting ---------------------------------------------------------
export const pct = (p: number | null | undefined) => (p == null ? '–' : `${Math.round(p * 100)}%`);
export const units = (u: number) => `${u > 0 ? '+' : ''}${u.toFixed(2)}u`;
export const clvPts = (c: number | null | undefined) =>
  c == null ? '–' : `${c > 0 ? '+' : ''}${(c * 100).toFixed(1)} pts`;
const dateFmt = new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
export const when = (iso: string) => dateFmt.format(new Date(iso));
export const shortHash = (h: string) => `${h.slice(0, 10)}…${h.slice(-4)}`;
