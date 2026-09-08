import React from 'react';
import { Microscope, CheckCircle, XCircle, MinusCircle } from 'lucide-react';
import { HypothesisScorecard } from '../types/api';

interface HypothesisPanelProps {
  hypothesis: HypothesisScorecard | null;
}

export const HypothesisPanel: React.FC<HypothesisPanelProps> = ({ hypothesis }) => {
  if (!hypothesis) {
    return (
      <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel flex flex-col justify-center items-center text-center py-12">
        <Microscope className="w-8 h-8 text-studio-fg4 mb-2" />
        <span className="text-sm font-semibold text-studio-fg3 font-mono">
          Hypothesis Matrix Pending
        </span>
        <p className="text-xs text-studio-fg4 max-w-sm mt-1">
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
      cls: 'bg-studio-danger/15 text-studio-danger border-studio-danger/30',
    },
    REJECTED: {
      label: 'NO REGRESSION FOUND',
      cls: 'bg-studio-success/15 text-studio-success border-studio-success/30',
    },
    INCONCLUSIVE: {
      label: 'INCONCLUSIVE',
      cls: 'bg-studio-warning/15 text-studio-warning border-studio-warning/30',
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
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-studio-border/60">
        <div className="flex items-center space-x-2">
          <Microscope className="w-4 h-4 text-studio-cyan" />
          <h3 className="text-sm font-semibold text-studio-fg uppercase font-mono tracking-wide">
            Falsifiable Hypothesis Scorecard
          </h3>
        </div>
        <div className="flex items-center space-x-2">
          <span className="text-xs font-mono text-studio-fg3">
            Score: <span className="text-studio-fg font-bold">{hypothesis.passed_count}/{hypothesis.total_tests}</span>
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
        <p className="text-sm text-studio-fg leading-relaxed">{hypothesis.headline}</p>
      )}

      {/* Primary Hypothesis Summary */}
      <div className="bg-studio-card/80 border border-studio-border/70 rounded-lg p-3.5">
        <span className="text-[11px] font-mono text-studio-accent font-semibold block mb-1">
          Primary Root-Cause Hypothesis:
        </span>
        <p className="text-xs text-studio-fg leading-relaxed font-medium">
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
                  ? 'bg-studio-card/60 border-studio-border/30'
                  : test.passed
                  ? 'bg-studio-success/10 border-studio-success/30'
                  : 'bg-studio-danger/10 border-studio-danger/30'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  {isSkipped ? (
                    <MinusCircle className="w-4 h-4 text-studio-fg4 shrink-0" />
                  ) : test.passed ? (
                    <CheckCircle className="w-4 h-4 text-studio-success shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 text-studio-danger shrink-0" />
                  )}
                  <span
                    className={`font-semibold ${isSkipped ? 'text-studio-fg2' : 'text-studio-fg'}`}
                  >
                    {test.name}
                  </span>
                  {isSkipped && (
                    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-studio-border/50 text-studio-fg3 uppercase tracking-wide">
                      Not applicable
                    </span>
                  )}
                </div>
                <span className="text-[10px] font-mono text-studio-fg3">{test.evidence_source}</span>
              </div>

              {/* A skipped criterion has no evidence, so an empty quote would be
                  rendered as a pair of bare quotation marks. */}
              {test.evidence_snippet && (
                <p className="text-[11px] mt-1.5 pl-6 font-mono text-studio-success/90">
                  "{test.evidence_snippet}"
                </p>
              )}
              <p className="text-studio-fg3 text-[11px] mt-1 pl-6">
                {test.explanation}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
