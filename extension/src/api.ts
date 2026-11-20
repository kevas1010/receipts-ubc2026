/// <reference types="chrome" />
// Side-panel client. Inside the extension it uses chrome.storage and talks to the Discord tab through the
// content script; opened as a plain page (for development) it falls back to localStorage.

export type Tier = 'sharp' | 'verified' | 'unverified';
export type Role = 'tailer' | 'analyst';

export interface Rule {
  key: string;
  label: string;
  required: string;
  actual: string;
  passed: boolean;
}

export interface RecordStats {
  tier: Tier;
  record_kind: 'observed' | 'tracked';
  verified: boolean;
  sharp: boolean;
  verified_at: string | null;
  wins: number;
  losses: number;
  voids: number;
  pending: number;
  graded: number;
  units: number;
  roi: number;
  avg_clv: number | null;
  edits: number;
  deletes: number;
  active_days: number;
  channels: number;
  win_rate: number | null;
  rules: Rule[];
}

export interface Capper {
  id: string;
  handle: string;
  display_name: string;
  avatar_url: string | null;
  demo: number;
  verified_at: string | null;
  storefront_url: string | null;
  first_seen: string | null;
  last_seen: string | null;
}

export type AnalystRow = Capper & RecordStats & Record<string, unknown>;

export interface VenueQuote {
  venue: string;
  price: number | null;
  url: string | null;
  primary: boolean;
  cheapest: boolean;
}

export interface Pick extends Record<string, unknown> {
  id: number;
  source: string;
  text: string;
  units: number;
  event_title: string;
  side_label: string;
  start_time: string;
  posted_at: string;
  lock_price: number | null;
  close_price: number | null;
  result: 'pending' | 'win' | 'loss' | 'void';
  commit_hash: string;
  tx_url: string | null;
  gemini_url: string;
  status: 'locked' | 'edited' | 'deleted';
  mode: 'live' | 'scraped';
  channel_name: string | null;
  edited_flag: number;
  note: string | null;
  profit: number;
  clv: number | null;
  venues: VenueQuote[];
}

export interface Profile {
  capper: Capper;
  record: RecordStats;
  picks: Pick[];
  earlier: Pick[];
}

export interface Me {
  user: {id: number; role: Role; name: string; capper_id: string | null; claim_code: string | null; claim_handle: string | null};
  capper: Capper | null;
  record: RecordStats | null;
}

export interface Session {
  apiBase: string;
  token: string | null;
  role: Role | null;
  name: string | null;
  capture: boolean;
}

export interface ScanResult {
  context: {guildId: string | null; channelId: string | null; channelName: string | null};
  visible: number;
  sent: number;
  result: {counts?: Record<string, number>; error?: string; skipped?: boolean} | null;
}

const DEFAULT_API = 'http://localhost:8000';
const hasChrome = typeof chrome !== 'undefined' && !!chrome.storage?.local;

// ---- session storage ----------------------------------------------------
export async function loadSession(): Promise<Session> {
  const keys = ['apiBase', 'token', 'role', 'name', 'capture'];
  let s: Record<string, unknown> = {};
  if (hasChrome) {
    s = await chrome.storage.local.get(keys);
  } else {
    try {
      s = JSON.parse(localStorage.getItem('receipts') || '{}');
    } catch {
      s = {};
    }
  }
  return {
    apiBase: ((s.apiBase as string) || DEFAULT_API).replace(/\/$/, ''),
    token: (s.token as string) || null,
    role: (s.role as Role) || null,
    name: (s.name as string) || null,
    capture: s.capture !== false,
  };
}

export async function saveSession(patch: Partial<Session>): Promise<Session> {
  const next = {...(await loadSession()), ...patch};
  if (hasChrome) {
    await chrome.storage.local.set(next);
  } else {
    try {
      localStorage.setItem('receipts', JSON.stringify(next));
    } catch {
      /* private window: session lasts until reload */
    }
  }
  return next;
}

// ---- HTTP ----------------------------------------------------------------
async function request<T>(path: string, init: {method?: string; body?: unknown} = {}): Promise<T> {
  const s = await loadSession();
  const res = await fetch(s.apiBase + path, {
    method: init.method ?? 'GET',
    headers: {'Content-Type': 'application/json', ...(s.token ? {Authorization: `Bearer ${s.token}`} : {})},
    body: init.body ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(String(detail));
  }
  return (await res.json()) as T;
}

export const api = {
  login: (role: Role, name: string) =>
    request<{token: string; user: Me['user']}>('/api/auth/login', {method: 'POST', body: {role, name}}),
  me: () => request<Me>('/api/me'),
  claim: (handle: string) => request<{code: string}>('/api/analyst/claim', {method: 'POST', body: {handle}}),
  storefront: (url: string) => request<{ok: boolean}>('/api/analyst/storefront', {method: 'POST', body: {url}}),
  postPick: (text: string, ticker?: string) =>
    request<{ok: boolean; message?: string; pick?: Pick; choices?: {ticker: string; title: string; side: string; start_time: string}[]}>(
      '/api/analyst/picks',
      {method: 'POST', body: {text, ticker}},
    ),
  observe: (messages: unknown[]) =>
    request<{counts: Record<string, number>}>('/api/observations', {method: 'POST', body: {messages}}),
  analysts: (channelId?: string | null) =>
    request<AnalystRow[]>(channelId ? `/api/analysts?channel_id=${encodeURIComponent(channelId)}` : '/api/analysts'),
  profile: (handle: string) => request<Profile>(`/api/analysts/${encodeURIComponent(handle)}`),
  candles: (id: number) => request<{candles: {t: number; close: number}[]}>(`/api/picks/${id}/candles`),
  config: () => request<{window_days: number; demo_mode: boolean}>('/api/config'),
};

export async function badgeUrl(handle: string) {
  return `${(await loadSession()).apiBase}/badge/${handle}.svg`;
}

// ---- the Discord tab ------------------------------------------------------
async function discordTab(): Promise<chrome.tabs.Tab | null> {
  if (!hasChrome || !chrome.tabs) {
    return null;
  }
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  return tab && tab.url?.startsWith('https://discord.com/') ? tab : null;
}

/** Ask the content script to read the visible messages now and report them. */
export async function scanDiscord(): Promise<ScanResult | null> {
  const tab = await discordTab();
  if (!tab?.id) {
    return null;
  }
  try {
    return (await chrome.tabs.sendMessage(tab.id, {type: 'SCAN'})) as ScanResult;
  } catch {
    return null; // content script not loaded yet (tab opened before install): a reload fixes it
  }
}

export function onFocusAnalyst(cb: (handle: string) => void): () => void {
  if (!hasChrome || !chrome.storage?.session) {
    return () => {};
  }
  const check = (changes: Record<string, chrome.storage.StorageChange>) => {
    if (changes.focusAnalyst?.newValue) {
      cb(String(changes.focusAnalyst.newValue));
    }
  };
  chrome.storage.session.get('focusAnalyst').then(v => v.focusAnalyst && cb(String(v.focusAnalyst)));
  chrome.storage.session.onChanged.addListener(check);
  return () => chrome.storage.session.onChanged.removeListener(check);
}

// ---- Discord export import (DiscordChatExporter JSON) -----------------------
interface ExportFile {
  guild?: {id?: string};
  channel?: {id?: string; name?: string};
  messages?: {id: string; content?: string; timestampEdited?: string | null; author?: {id?: string; name?: string; nickname?: string; avatarUrl?: string}}[];
}

export function messagesFromExport(json: ExportFile) {
  return (json.messages ?? [])
    .filter(m => m.id && m.content)
    .map(m => ({
      message_id: m.id,
      channel_id: json.channel?.id ?? null,
      guild_id: json.guild?.id ?? null,
      channel_name: json.channel?.name ?? null,
      author_id: m.author?.id ?? null,
      author_name: m.author?.nickname || m.author?.name || 'unknown',
      avatar_url: m.author?.avatarUrl ?? null,
      text: m.content,
      edited: !!m.timestampEdited,
    }));
}

// ---- formatting -------------------------------------------------------------
export const pct = (p: number | null | undefined) => (p == null ? '–' : `${Math.round(p * 100)}%`);
export const units = (u: number) => `${u > 0 ? '+' : ''}${u.toFixed(2)}u`;
export const clvPts = (c: number | null | undefined) => (c == null ? '–' : `${c > 0 ? '+' : ''}${(c * 100).toFixed(1)} pts`);
const dateFmt = new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
export const when = (iso: string) => dateFmt.format(new Date(iso));
export const shortHash = (h: string) => `${h.slice(0, 10)}…${h.slice(-4)}`;
export const record = (r: {wins: number; losses: number}) => `${r.wins}-${r.losses}`;
