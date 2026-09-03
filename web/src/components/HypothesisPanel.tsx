import React from 'react';
import { Microscope, CheckCircle, XCircle, MinusCircle } from 'lucide-react';
import { HypothesisScorecard } from '../types/api';

interface HypothesisPanelProps {
  hypothesis: HypothesisScorecard | null;
}

export const HypothesisPanel: React.FC<HypothesisPanelProps> = ({ hypothesis }) => {
  if (!hypothesis) {
    return (
      <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-lg flex flex-col justify-center items-center text-center py-12">
        <Microscope className="w-8 h-8 text-slate-600 mb-2" />
        <span className="text-sm font-semibold text-slate-400 font-mono">
          Hypothesis Matrix Pending
        </span>
        <p className="text-xs text-slate-500 max-w-sm mt-1">
          Agent evaluates telemetry against falsifiable scientific criteria during investigation.
        </p>
      </div>
    );
  }

  // A low score means two different things. Evidence that refutes the
  // hypothesis is a finding; evidence that never arrived is the absence of one.
  // Leading with the raw score presented a healthy fleet as a failed
  // investigation, six red crosses and all.
  const verdict = hypothesis.verdict ?? 'INCONCLUSIVE';
  const badge = {
    SUPPORTED: {
      label: 'REGRESSION CONFIRMED',
      cls: 'bg-red-500/15 text-red-300 border-red-500/30',
    },
    REJECTED: {
      label: 'NO REGRESSION FOUND',
      cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    },
    INCONCLUSIVE: {
      label: 'INCONCLUSIVE',
      cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    },
  }[verdict];

  // The denominator is the count of criteria that could be tested at all, so the
  // tooltip has to say which ones were left out; otherwise "5/5" silently hides
  // that a criterion was never attempted.
  const skipped = hypothesis.skipped_tests ?? [];
  const scoreTitle =
    `${hypothesis.confidence} confidence, ${hypothesis.passed_count} of ` +
    `${hypothesis.total_tests} applicable tests passed` +
    (skipped.length > 0 ? `. Skipped: ${skipped.join(', ')}` : '');

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-lg space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-studio-border/60">
        <div className="flex items-center space-x-2">
          <Microscope className="w-4 h-4 text-studio-cyan" />
          <h3 className="text-sm font-semibold text-white uppercase font-mono tracking-wide">
            Falsifiable Hypothesis Scorecard
          </h3>
        </div>
        <div className="flex items-center space-x-2">
          <span className="text-xs font-mono text-slate-400">
            Score: <span className="text-white font-bold">{hypothesis.passed_count}/{hypothesis.total_tests}</span>
          </span>
          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold uppercase border ${badge.cls}`}
            title={scoreTitle}
          >
            {badge.label}
          </span>
        </div>
      </div>

      {hypothesis.headline && (
        <p className="text-sm text-slate-200 leading-relaxed">{hypothesis.headline}</p>
      )}

      {/* Primary Hypothesis Summary */}
      <div className="bg-studio-card/80 border border-studio-border/70 rounded-lg p-3.5">
        <span className="text-[11px] font-mono text-studio-accent font-semibold block mb-1">
          Primary Root-Cause Hypothesis:
        </span>
        <p className="text-xs text-slate-200 leading-relaxed font-medium">
          {hypothesis.primary_hypothesis}
        </p>
      </div>

      {/* Falsifiable test breakdown */}
      <div className="space-y-2.5">
        {hypothesis.tests.map((test) => {
          // Three states, not two. A criterion the server cannot supply evidence
          // for is neither passed nor refuted, and showing it as a red cross next
          // to a full score reads as a contradiction.
          const isSkipped = test.applicable === false;

          return (
            <div
              key={test.test_id}
              className={`border rounded-lg p-3 text-xs transition-all ${
                isSkipped
                  ? 'bg-slate-800/20 border-slate-600/30'
                  : test.passed
                  ? 'bg-emerald-950/10 border-emerald-500/30'
                  : 'bg-red-950/10 border-red-500/30'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  {isSkipped ? (
                    <MinusCircle className="w-4 h-4 text-slate-500 shrink-0" />
                  ) : test.passed ? (
                    <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                  )}
                  <span
                    className={`font-semibold ${isSkipped ? 'text-slate-300' : 'text-white'}`}
                  >
                    {test.name}
                  </span>
                  {isSkipped && (
                    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-slate-600/50 text-slate-400 uppercase tracking-wide">
                      Not applicable
                    </span>
                  )}
                </div>
                <span className="text-[10px] font-mono text-slate-400">{test.evidence_source}</span>
              </div>

              {/* A skipped criterion has no evidence, so an empty quote would be
                  rendered as a pair of bare quotation marks. */}
              {test.evidence_snippet && (
                <p className="text-[11px] mt-1.5 pl-6 font-mono text-emerald-300/90">
                  "{test.evidence_snippet}"
                </p>
              )}
              <p className="text-slate-400 text-[11px] mt-1 pl-6">
                {test.explanation}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
