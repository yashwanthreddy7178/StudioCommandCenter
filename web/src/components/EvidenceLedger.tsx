import React, { useState } from 'react';
import { Terminal, ChevronDown, ChevronRight, Zap } from 'lucide-react';
import { StepEvent } from '../types/api';

interface EvidenceLedgerProps {
  events: StepEvent[];
}

export const EvidenceLedger: React.FC<EvidenceLedgerProps> = ({ events }) => {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const toggleExpand = (seq: number) => {
    setExpandedId(expandedId === seq ? null : seq);
  };

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-lg flex flex-col h-full">
      <div className="flex items-center justify-between pb-3 border-b border-studio-border/60">
        <div className="flex items-center space-x-2">
          <Terminal className="w-4 h-4 text-studio-cyan" />
          <h3 className="text-sm font-semibold text-white uppercase font-mono tracking-wide">
            Autonomous Evidence Ledger ({events.length} Steps)
          </h3>
        </div>
        <span className="text-[11px] font-mono text-slate-400">Stream: SSE Live</span>
      </div>

      <div className="mt-4 flex-1 overflow-y-auto space-y-3 max-h-[480px] pr-1">
        {events.length === 0 ? (
          <div className="text-center py-12 text-slate-500 font-mono text-xs">
            No active investigation. Click "Launch Agent" to begin autonomous investigation.
          </div>
        ) : (
          events.map((event) => {
            const isExpanded = expandedId === event.seq;
            const isCacheHit = event.payload?.cache_hit ?? false;
            const toolName = event.payload?.tool_name;

            return (
              <div
                key={event.seq}
                className="bg-studio-card/80 border border-studio-border/70 rounded-lg p-3.5 hover:border-studio-border transition-all font-mono text-xs"
              >
                <div
                  className="flex items-start justify-between cursor-pointer"
                  onClick={() => toggleExpand(event.seq)}
                >
                  <div className="flex items-start space-x-2.5">
                    <span className="bg-studio-bg text-slate-400 px-1.5 py-0.5 rounded text-[10px] font-bold">
                      #{event.seq}
                    </span>
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold text-white">{event.title}</span>
                        {toolName && (
                          <span className="bg-studio-border text-studio-cyan text-[10px] px-1.5 py-0.2 rounded font-bold">
                            {toolName}
                          </span>
                        )}
                        {isCacheHit && (
                          <span className="bg-blue-500/20 text-blue-300 border border-blue-500/30 text-[9px] px-1.5 py-0.2 rounded font-bold flex items-center space-x-1">
                            <Zap className="w-2.5 h-2.5" />
                            <span>CACHE HIT</span>
                          </span>
                        )}
                      </div>
                      <p className="text-slate-300 text-xs mt-1 font-sans leading-relaxed">
                        {event.description}
                      </p>
                    </div>
                  </div>

                  <button className="text-slate-400 hover:text-white p-1">
                    {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  </button>
                </div>

                {/* Expanded Payload Viewer */}
                {isExpanded && event.payload && (
                  <div className="mt-3 pt-3 border-t border-studio-border/50">
                    <div className="bg-studio-bg rounded p-2.5 text-[11px] text-slate-300 overflow-x-auto max-h-48 border border-studio-border/40">
                      <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
