import React from 'react';
import { Microscope, CheckCircle, XCircle, ShieldCheck, AlertCircle } from 'lucide-react';
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
          Agent evaluates telemetry against 6 falsifiable scientific criteria during investigation.
        </p>
      </div>
    );
  }

  const isHighConfidence = hypothesis.confidence === 'HIGH';

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
            className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold uppercase border ${
              isHighConfidence
                ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                : 'bg-amber-500/20 text-amber-300 border-amber-500/30'
            }`}
          >
            {hypothesis.confidence} CONFIDENCE
          </span>
        </div>
      </div>

      {/* Primary Hypothesis Summary */}
      <div className="bg-studio-card/80 border border-studio-border/70 rounded-lg p-3.5">
        <span className="text-[11px] font-mono text-studio-accent font-semibold block mb-1">
          Primary Root-Cause Hypothesis:
        </span>
        <p className="text-xs text-slate-200 leading-relaxed font-medium">
          {hypothesis.primary_hypothesis}
        </p>
      </div>

      {/* 6 Falsifiable Test Breakdown */}
      <div className="space-y-2.5">
        {hypothesis.tests.map((test) => (
          <div
            key={test.test_id}
            className={`border rounded-lg p-3 text-xs transition-all ${
              test.passed
                ? 'bg-emerald-950/10 border-emerald-500/30'
                : 'bg-red-950/10 border-red-500/30'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                {test.passed ? (
                  <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                )}
                <span className="font-semibold text-white">{test.name}</span>
              </div>
              <span className="text-[10px] font-mono text-slate-400">{test.evidence_source}</span>
            </div>

            <p className="text-slate-300 text-[11px] mt-1.5 pl-6 font-mono text-emerald-300/90">
              "{test.evidence_snippet}"
            </p>
            <p className="text-slate-400 text-[11px] mt-1 pl-6">
              {test.explanation}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};
