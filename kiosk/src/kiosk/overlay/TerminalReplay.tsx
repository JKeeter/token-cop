import { useEffect, useRef } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useKiosk } from '../state';

// Replays a genuine recorded MCP exchange as a Claude Code-style terminal:
// typewriter prompt → tool chips → streamed markdown → usage footer.
export default function TerminalReplay() {
  const replay = useKiosk((s) => s.replay);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [replay?.streamed, replay?.toolsShown]);

  if (!replay) return <div className="h-full bg-ink-950" />;
  const { capture, phase, typed, toolsShown, streamed } = replay;
  const capturedDate = capture.capturedAt.slice(0, 10);

  return (
    <div className="flex h-full items-center justify-center bg-ink-950 px-[8vw] pb-[13vh] pt-[4vh]">
      <div className="flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-ink-700 bg-[#0a0f1c] shadow-2xl">
        {/* title bar */}
        <div className="flex items-center gap-3 border-b border-ink-700 px-5 py-3">
          <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
          <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
          <span className="h-3 w-3 rounded-full bg-[#28c840]" />
          <span className="ml-3 font-mono text-sm text-slate-400">claude — token-cop</span>
          <span className="ml-auto rounded-md border border-cop-amber/40 bg-cop-amber/10 px-2.5 py-0.5 font-mono text-[11px] font-bold tracking-widest text-cop-amber">
            RECORDED · {capturedDate}
          </span>
        </div>

        {/* body */}
        <div ref={bodyRef} className="no-scrollbar flex-1 overflow-y-auto px-8 py-6 font-mono text-[15px] leading-relaxed">
          <div className="flex items-start gap-3">
            <span className="font-bold text-cop-teal">❯</span>
            <span className="text-slate-100">
              <span className="text-cop-teal">/tokcop </span>
              {capture.prompt.slice(0, typed)}
              {phase === 'typing' && <span className="ml-0.5 inline-block h-5 w-2.5 translate-y-1 animate-caret bg-slate-200" />}
            </span>
          </div>

          {capture.toolCalls.slice(0, toolsShown).map((tc) => (
            <div key={tc.name} className="mt-3 flex animate-fadeup items-center gap-3 pl-6 text-sm">
              <span className="text-cop-amber">⏺</span>
              <span className="font-semibold text-slate-200">{tc.name}</span>
              <span className="text-slate-500">{tc.summary}</span>
              <span className="text-cop-teal">✓</span>
            </div>
          ))}

          {(phase === 'streaming' || phase === 'done') && (
            <div className="md mt-5 border-t border-ink-700 pt-5 font-sans text-[15px]">
              <Markdown remarkPlugins={[remarkGfm]}>{capture.responseMarkdown.slice(0, streamed)}</Markdown>
            </div>
          )}
        </div>

        {/* usage footer */}
        <div className="flex items-center gap-5 border-t border-ink-700 bg-ink-900/60 px-6 py-2.5 font-mono text-xs text-slate-500">
          <span>tool: {capture.tool.replace('mcp__token-cop__', '')}</span>
          <span>backend: {capture.backend}</span>
          <span className="ml-auto">AgentCore Runtime · Claude Sonnet on Bedrock</span>
        </div>
      </div>
    </div>
  );
}
