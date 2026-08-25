import React, { useState } from 'react';
import { ShieldAlert, CheckCircle, ArrowRight, AlertTriangle, Lock, Sparkles } from 'lucide-react';
import { RemediationOption } from '../types/api';

interface ApprovalModalProps {
  isOpen: boolean;
  options: RemediationOption[];
  onApprove: (optionId: string) => void;
  isExecuting: boolean;
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  isOpen,
  options,
  onApprove,
  isExecuting,
}) => {
  const [selectedOptionId, setSelectedOptionId] = useState<string>(options[0]?.option_id || 'opt-01');

  if (!isOpen || options.length === 0) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fadeIn">
      <div className="bg-studio-surface border border-studio-border rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-5">
        {/* Header */}
        <div className="flex items-center space-x-3 pb-4 border-b border-studio-border">
          <div className="p-2.5 bg-amber-500/10 border border-amber-500/30 rounded-xl text-amber-400">
            <Lock className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-white flex items-center space-x-2">
              <span>Human Approval Gate: Remediation Options</span>
              <span className="bg-studio-accent/20 text-studio-accent text-[10px] font-mono px-2 py-0.5 rounded uppercase font-bold">
                Verification Gated
              </span>
            </h2>
            <p className="text-xs text-slate-400">
              The agent has investigated the anomaly and proposed ranked actions. Approval is mandatory to execute mutations.
            </p>
          </div>
        </div>

        {/* Options List */}
        <div className="space-y-3">
          {options.map((opt) => {
            const isSelected = selectedOptionId === opt.option_id;
            const isRecommended = opt.option_id === 'opt-01';

            return (
              <div
                key={opt.option_id}
                onClick={() => setSelectedOptionId(opt.option_id)}
                className={`border rounded-xl p-4 cursor-pointer transition-all ${
                  isSelected
                    ? 'bg-studio-accent/10 border-studio-accent shadow-md shadow-blue-500/10 ring-1 ring-studio-accent'
                    : 'bg-studio-card/80 border-studio-border/60 hover:border-studio-border'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <span className="text-sm font-semibold text-white">{opt.title}</span>
                    {isRecommended && (
                      <span className="bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] px-1.5 py-0.2 rounded font-bold flex items-center space-x-1">
                        <Sparkles className="w-2.5 h-2.5" />
                        <span>RECOMMENDED</span>
                      </span>
                    )}
                  </div>
                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold uppercase ${
                      opt.risk_level === 'LOW'
                        ? 'bg-emerald-500/20 text-emerald-300'
                        : 'bg-amber-500/20 text-amber-300'
                    }`}
                  >
                    {opt.risk_level} RISK | ~{opt.estimated_recovery_minutes}m ETA
                  </span>
                </div>

                <p className="text-xs text-slate-300 mt-2 leading-relaxed">
                  {opt.description}
                </p>

                <div className="mt-2.5 pt-2 border-t border-studio-border/40 text-[11px] font-mono text-slate-400">
                  <span className="text-slate-300 font-semibold">Production Consequence: </span>
                  <span>{opt.production_consequence}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-end space-x-3 pt-4 border-t border-studio-border">
          <button
            onClick={() => onApprove(selectedOptionId)}
            disabled={isExecuting}
            className={`flex items-center space-x-2 px-5 py-2.5 rounded-lg text-xs font-semibold text-white transition shadow-lg ${
              isExecuting
                ? 'bg-emerald-600/50 cursor-not-allowed'
                : 'bg-emerald-600 hover:bg-emerald-500 shadow-emerald-500/20 active:scale-95'
            }`}
          >
            <CheckCircle className="w-4 h-4" />
            <span>{isExecuting ? 'Applying & Verifying...' : 'Approve & Execute Remediation'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
