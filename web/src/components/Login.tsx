import React, { useState } from 'react';
import { Clapperboard, Loader2, LockKeyhole, Sun, Moon } from 'lucide-react';
import { Theme } from '../lib/theme';
import { login } from '../lib/auth';

interface LoginProps {
  /** Called once the credential has been exchanged for a stored token. */
  onSignedIn: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

/** The single-credential sign-in gate shown before the command centre loads. */
export const Login: React.FC<LoginProps> = ({ onSignedIn, theme, onToggleTheme }) => {
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
    <div className="min-h-screen bg-studio-bg text-studio-fg flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5 mb-6">
          <Clapperboard className="w-6 h-6 text-studio-success shrink-0" />
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-semibold tracking-tight">Studio Production Commander</h1>
            <p className="text-xs text-studio-fg3 font-mono">Render farm operations</p>
          </div>
          <button
            type="button"
            onClick={onToggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            className="flex items-center justify-center w-8 h-8 rounded-md border border-studio-border bg-studio-card text-studio-fg3 hover:text-studio-fg transition"
          >
            {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
          </button>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel space-y-4"
        >
          <div className="flex items-center space-x-2 text-studio-fg2">
            <LockKeyhole className="w-4 h-4 text-studio-fg3" />
            <h2 className="text-sm font-semibold tracking-wide">OPERATOR SIGN IN</h2>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="username" className="block text-xs text-studio-fg3 font-medium">
              Username
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-studio-card border border-studio-border/80 rounded-lg px-3 py-2 text-sm font-mono text-studio-fg placeholder-studio-fg4 focus:outline-none focus:border-studio-success/60"
              placeholder="supervisor"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="block text-xs text-studio-fg3 font-medium">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-studio-card border border-studio-border/80 rounded-lg px-3 py-2 text-sm font-mono text-studio-fg placeholder-studio-fg4 focus:outline-none focus:border-studio-success/60"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <p role="alert" className="text-[11px] font-mono text-studio-danger bg-studio-danger/10 border border-studio-danger/30 rounded-md px-2.5 py-2">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !username || !password}
            className="w-full flex items-center justify-center space-x-2 bg-studio-success/90 hover:bg-studio-success disabled:bg-studio-border disabled:text-studio-fg4 text-studio-on-accent font-semibold text-sm rounded-lg px-3 py-2 transition-colors"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            <span>{busy ? 'Signing in' : 'Sign in'}</span>
          </button>
        </form>

        <div className="mt-3 text-center">
          <p className="text-[10px] uppercase tracking-wider text-studio-fg4 font-medium">
            Demo credentials
          </p>
          <p className="text-xs font-mono text-studio-fg2 mt-1">
            <span className="select-all">supervisor</span>
            <span className="text-studio-fg4"> / </span>
            <span className="select-all">shadow-protocol</span>
          </p>
        </div>
      </div>
    </div>
  );
};
