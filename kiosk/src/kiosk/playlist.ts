// The declarative scene playlist — 11 scenes, ~284s at speed 1.
// Dwells are tuned to the narration clips in kiosk/narration.txt: each scene
// outlasts its spoken paragraph by ~2s so the loop never sits silent.
// Mirrors the Ledgerly kiosk Scene schema: every scene has a watchdog budget
// (maxMs) above its intended dwell so a wedged scene can never stall the loop.

export type SceneType = 'attract' | 'media' | 'stat' | 'arch' | 'replay';
export type Badge = 'RECORDED' | 'SLIDE' | null;
export type SceneComponent = 'attract' | 'closing' | 'snowball' | 'arch-mcp' | 'arch-platform';

export interface Caption {
  atMs: number;
  text: string;
}

export interface Scene {
  id: string;
  type: SceneType;
  badge: Badge;
  dwellMs: number;
  maxMs: number;
  captions: Caption[];
  assets?: string[];
  capture?: string;
  component?: SceneComponent;
}

export const KIOSK_PLAYLIST: Scene[] = [
  {
    id: 'attract',
    type: 'attract',
    badge: null,
    dwellMs: 20_000,
    maxMs: 35_000,
    component: 'attract',
    captions: [
      { atMs: 0, text: 'Token Cop — an AI cost-cop agent on Amazon Bedrock AgentCore. It watches every token your agents spend.' },
      { atMs: 12_000, text: 'Touch to pause · scan the QR for the repo. Next showing starts now.' },
    ],
  },
  {
    id: 'hook-uber',
    type: 'media',
    badge: 'SLIDE',
    dwellMs: 25_000,
    maxMs: 42_000,
    assets: ['slides/hook-uber.png'],
    captions: [
      { atMs: 0, text: 'The 2026 headlines: inference is draining enterprise AI budgets — overspending became a meme.' },
      { atMs: 10_000, text: 'Adoption soared. Nobody could say which user, model, or workload spent what.' },
    ],
  },
  {
    id: 'cost-anatomy',
    type: 'media',
    badge: 'SLIDE',
    dwellMs: 16_500,
    maxMs: 31_000,
    assets: ['slides/cost-anatomy.png'],
    captions: [
      { atMs: 0, text: 'Anatomy of an agent dollar: roughly $700 of every $1,000 is LLM inference.' },
      { atMs: 9_000, text: 'That is the line Token Cop polices.' },
    ],
  },
  {
    id: 'snowball',
    type: 'stat',
    badge: 'SLIDE',
    dwellMs: 23_000,
    maxMs: 38_000,
    component: 'snowball',
    captions: [
      { atMs: 0, text: 'Context snowballs: turn 1 of a chat costs about 3,000 tokens…' },
      { atMs: 6_500, text: '…by turn 4, every turn costs 6,600 — the whole history rides along each time.' },
      { atMs: 12_500, text: 'Caching discounts the re-reads 90% — yet cached context was still 64% of our real bill.' },
    ],
  },
  {
    id: 'arch-mcp',
    type: 'arch',
    badge: 'SLIDE',
    dwellMs: 43_000,
    maxMs: 61_000,
    component: 'arch-mcp',
    captions: [
      { atMs: 0, text: 'A developer asks a question — /tokcop — right inside Claude Code.' },
      { atMs: 6_500, text: 'A local MCP server signs the call with Cognito: a verified JWT, not a prompt claim.' },
      { atMs: 13_000, text: 'The request crosses the AgentCore MCP Gateway to its Lambda target.' },
      { atMs: 20_000, text: 'AgentCore Runtime hosts the Strands agent — Claude Sonnet on Amazon Bedrock.' },
      { atMs: 27_000, text: 'Thirteen tools: CloudWatch, Cost Explorer, S3 invocation logs, provider APIs, AgentCore Memory.' },
      { atMs: 34_000, text: 'The answer comes back scrubbed of secrets. Seconds, not spreadsheets.' },
    ],
  },
  {
    id: 'replay-spend',
    type: 'replay',
    badge: 'RECORDED',
    dwellMs: 30_000,
    maxMs: 48_000,
    capture: 'spend-by-model',
    captions: [
      { atMs: 0, text: 'This is a real exchange, recorded from the live MCP tool — not a mock-up.' },
      { atMs: 11_000, text: 'Token Cop pulls CloudWatch Bedrock metrics and prices every model from its pricing table.' },
      { atMs: 22_000, text: 'Per-model spend for 7 days — $188 and an 88% cache hit rate — from one question.' },
    ],
  },
  {
    id: 'replay-audit',
    type: 'replay',
    badge: 'RECORDED',
    dwellMs: 30_000,
    maxMs: 48_000,
    capture: 'token-audit',
    captions: [
      { atMs: 0, text: 'Next, a full token audit — recorded live. Six efficiency dimensions.' },
      { atMs: 12_000, text: 'It grades real usage: cache utilization, model mix, context efficiency, output ratios.' },
      { atMs: 22_000, text: 'Every recommendation comes with the receipts.' },
    ],
  },
  {
    id: 'replay-recommend',
    type: 'replay',
    badge: 'RECORDED',
    dwellMs: 26_000,
    maxMs: 44_000,
    capture: 'recommend-model',
    captions: [
      { atMs: 0, text: '"Summarize 500 support tickets daily — which model?" Token Cop classifies the task into a tier.' },
      { atMs: 11_000, text: "Polish-tier work doesn't need a frontier model: ~$0.08 a day instead of ~$22.50." },
      { atMs: 19_000, text: "Same job, 95% cheaper. That's a smart token." },
    ],
  },
  {
    id: 'arch-platform',
    type: 'arch',
    badge: 'SLIDE',
    dwellMs: 37_000,
    maxMs: 55_000,
    component: 'arch-platform',
    captions: [
      { atMs: 0, text: 'One agent, a whole platform: AgentCore Runtime with Memory and the MCP Gateway.' },
      { atMs: 7_000, text: 'Cognito identity and Cedar policies gate every tool call — permit, forbid, audit.' },
      { atMs: 14_000, text: 'OpenTelemetry spans flow to X-Ray and CloudWatch: every token, every tool, traced.' },
      { atMs: 21_000, text: 'A harness twin runs the same agent as pure config — 11.9s versus 24.6s in the container.' },
      { atMs: 28_000, text: 'Over budget? A meter Lambda flips an IAM deny. Enforcement, not just a dashboard.' },
    ],
  },
  {
    id: 'dashboards',
    type: 'media',
    badge: 'RECORDED',
    dwellMs: 19_000,
    maxMs: 34_000,
    assets: ['dash/overview.png', 'dash/recommendations.png', 'dash/per-user.png'],
    captions: [
      { atMs: 0, text: 'The team dashboard: live spend, model mix, cache utilization.' },
      { atMs: 6_500, text: 'Recommendations rank the cheapest safe model for each workload.' },
      { atMs: 13_000, text: 'And per-model efficiency scores — proof each tier earns its cost.' },
    ],
  },
  {
    id: 'closing',
    type: 'attract',
    badge: null,
    dwellMs: 14_000,
    maxMs: 29_000,
    component: 'closing',
    captions: [
      { atMs: 0, text: 'More tokens is FINE — they need to be SMART tokens.' },
      { atMs: 8_000, text: 'github.com/JKeeter/token-cop — the loop restarts in a moment.' },
    ],
  },
];

interface PlaylistOverrideEntry {
  id: string;
  maxMs?: number;
  dwellMs?: number;
}

// localStorage override (tokencop.kiosk.playlist) lets tests/debugging pin a
// subset of scenes, optionally with tighter budgets.
export function resolvePlaylist(): Scene[] {
  const raw = window.localStorage.getItem('tokencop.kiosk.playlist');
  if (!raw) return KIOSK_PLAYLIST;
  try {
    const entries = JSON.parse(raw) as PlaylistOverrideEntry[];
    const scenes = entries
      .map((entry) => {
        const base = KIOSK_PLAYLIST.find((s) => s.id === entry.id);
        if (!base) return null;
        return { ...base, maxMs: entry.maxMs ?? base.maxMs, dwellMs: entry.dwellMs ?? base.dwellMs };
      })
      .filter((s): s is Scene => s !== null);
    return scenes.length ? scenes : KIOSK_PLAYLIST;
  } catch {
    return KIOSK_PLAYLIST;
  }
}
