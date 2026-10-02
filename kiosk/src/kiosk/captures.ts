// Loader for the recorded real MCP exchanges under public/captures/.
// Captures are genuine responses from the live `token_cop` MCP tool,
// recorded at build time (refresh procedure in docs/kiosk-demo.md).

export interface CaptureToolCall {
  name: string;
  summary: string;
}

export interface CaptureUsage {
  inputTokens: number | null;
  outputTokens: number | null;
  estCostUsd: number | null;
  durationMs: number | null;
}

export interface CapturePacing {
  typeMsPerChar: number;
  streamMsPerChar: number;
  toolChipMs: number;
}

export interface Capture {
  id: string;
  capturedAt: string;
  tool: string;
  backend: string;
  prompt: string;
  toolCalls: CaptureToolCall[];
  responseMarkdown: string;
  usage?: CaptureUsage;
  pacing: CapturePacing;
}

export class CaptureMissingError extends Error {}

const REQUIRED: Array<keyof Capture> = [
  'id', 'capturedAt', 'tool', 'backend', 'prompt', 'toolCalls', 'responseMarkdown', 'pacing',
];

export async function loadCapture(name: string): Promise<Capture> {
  let data: unknown;
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}captures/${name}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    throw new CaptureMissingError(`capture "${name}" unavailable: ${String(err)}`);
  }
  const capture = data as Capture;
  const missing = REQUIRED.filter((key) => !(key in capture));
  if (missing.length || !capture.responseMarkdown?.trim() || !Array.isArray(capture.toolCalls)) {
    throw new CaptureMissingError(`capture "${name}" malformed (missing: ${missing.join(', ')})`);
  }
  return capture;
}
