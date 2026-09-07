import React, { useState } from 'react';
import { Clapperboard, Loader2, LockKeyhole } from 'lucide-react';
import { login } from '../lib/auth';

interface LoginProps {
  /** Called once the credential has been exchanged for a stored token. */
  onSignedIn: () => void;
}

/** The single-credential sign-in gate shown before the command centre loads. */
export const Login: React.FC<LoginProps> = ({ onSignedIn }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onSignedIn();
    } catch (err: any) {
      setError(err?.message || 'Sign in failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-studio-bg text-slate-100 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="flex items-center space-x-2.5 mb-6">
          <Clapperboard className="w-6 h-6 text-emerald-400" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Studio Production Commander</h1>
            <p className="text-xs text-slate-400 font-mono">Render farm operations</p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-xl space-y-4"
        >
          <div className="flex items-center space-x-2 text-slate-300">
            <LockKeyhole className="w-4 h-4 text-slate-400" />
            <h2 className="text-sm font-semibold tracking-wide">OPERATOR SIGN IN</h2>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="username" className="block text-xs text-slate-400 font-medium">
              Username
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-studio-card border border-studio-border/80 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/60"
              placeholder="supervisor"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="block text-xs text-slate-400 font-medium">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-studio-card border border-studio-border/80 rounded-lg px-3 py-2 text-sm font-mono text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/60"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <p role="alert" className="text-[11px] font-mono text-red-400 bg-red-500/10 border border-red-500/30 rounded-md px-2.5 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !username || !password}
            className="w-full flex items-center justify-center space-x-2 bg-emerald-500/90 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-slate-950 font-semibold text-sm rounded-lg px-3 py-2 transition-colors"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            <span>{busy ? 'Signing in' : 'Sign in'}</span>
          </button>
        </form>

        <p className="text-[11px] text-slate-500 font-mono mt-3 text-center">
          One shared operator credential. Set APP_USERNAME and APP_PASSWORD.
        </p>
      </div>
    </div>
  );
};
