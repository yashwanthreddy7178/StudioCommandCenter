import React from 'react';
import { AlertTriangle } from 'lucide-react';

interface PanelBoundaryProps {
  name: string;
  children: React.ReactNode;
}

interface PanelBoundaryState {
  error: Error | null;
}

/**
 * Contains a render failure to the panel that caused it.
 *
 * Without this, one unexpected field shape anywhere in the tree unmounts the
 * entire application and leaves a blank screen. Confining the failure keeps the
 * rest of the dashboard usable and names the panel that broke, which matters
 * most during a live demo.
 */
export class PanelBoundary extends React.Component<PanelBoundaryProps, PanelBoundaryState> {
  state: PanelBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): PanelBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error(`[${this.props.name}] render failed`, error, info.componentStack);
  }

  render(): React.ReactNode {
    if (!this.state.error) return this.props.children;

    return (
      <div className="bg-studio-surface border border-amber-500/40 rounded-xl p-4">
        <div className="flex items-center space-x-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <span className="text-sm font-semibold text-amber-300">
            {this.props.name} could not render
          </span>
        </div>
        <p className="text-xs text-slate-400 font-mono mt-2 break-words">
          {this.state.error.message}
        </p>
        <p className="text-[11px] text-slate-500 mt-2">
          The rest of the dashboard is unaffected. Details are in the browser console.
        </p>
      </div>
    );
  }
}
