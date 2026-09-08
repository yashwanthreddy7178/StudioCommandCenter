import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Guards the credentials printed on the sign-in screen against drift.
 *
 * They are literal text in Login.tsx, which is what makes the screen simple and
 * dependency-free -- and also what lets it go on confidently displaying a
 * password that no longer works. A rotated APP_PASSWORD is the expected case,
 * not a hypothetical: the public deployment is meant to run one.
 *
 * So the literals are checked against the values the app actually authenticates
 * with. Nothing here is a runtime dependency; it fails at test time, which is
 * early enough to fix in the same breath as the rotation.
 */

const REPO_ROOT = resolve(__dirname, '../../..');

function loginSource(): string {
  return readFileSync(resolve(__dirname, 'Login.tsx'), 'utf-8');
}

/** The pair rendered under the Sign in button. */
function displayedCredentials(): { username: string; password: string } {
  const source = loginSource();
  const block = source.slice(source.indexOf('Demo credentials'));
  const values = [...block.matchAll(/className="select-all">([^<]+)</g)].map((m) => m[1]);
  if (values.length < 2) {
    throw new Error('Could not find the credential pair under the Sign in button');
  }
  return { username: values[0], password: values[1] };
}

/** Reads a key from a dotenv-style file, or null. */
function envValue(file: string, key: string): string | null {
  if (!existsSync(file)) return null;
  for (const line of readFileSync(file, 'utf-8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    if (trimmed.slice(0, eq).trim() !== key) continue;
    return trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
  }
  return null;
}

/** The fallbacks in services/common/auth.py, used when nothing overrides them. */
function codeDefaults(): { username: string; password: string } {
  const source = readFileSync(resolve(REPO_ROOT, 'services/common/auth.py'), 'utf-8');
  const grab = (name: string) => {
    const match = source.match(
      new RegExp(`os\\.environ\\.get\\("${name}",\\s*"([^"]+)"\\)`),
    );
    if (!match) throw new Error(`No default found for ${name} in services/common/auth.py`);
    return match[1];
  };
  return { username: grab('APP_USERNAME'), password: grab('APP_PASSWORD') };
}

describe('the credentials shown on the sign-in screen', () => {
  it('match the defaults the services fall back to', () => {
    // These are what a deployment authenticates with when nothing overrides
    // them, so a change to either side has to move both.
    expect(displayedCredentials()).toEqual(codeDefaults());
  });

  it('match .env when this machine has one', () => {
    // .env is the source deploy.sh reads to populate Secret Manager, so a
    // rotation lands here first. Skipped where there is no .env, such as CI.
    const envFile = resolve(REPO_ROOT, '.env');
    const username = envValue(envFile, 'APP_USERNAME');
    const password = envValue(envFile, 'APP_PASSWORD');
    if (username === null && password === null) return;

    const shown = displayedCredentials();
    if (username !== null) expect(shown.username).toBe(username);
    if (password !== null) {
      expect(
        shown.password,
        'APP_PASSWORD in .env no longer matches the password printed on the ' +
          'sign-in screen. Update the literal in Login.tsx, or stop printing it.',
      ).toBe(password);
    }
  });
});
