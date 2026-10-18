/**
 * "sign in with oidc" on the login screen. the button only shows when the
 * admin turned it on and filled in a provider, and it carries the label they
 * chose ("Sign in with Pocket ID"). a click is a plain page navigation: the
 * server sends the browser to the provider and the provider sends it back,
 * so there is no popup and nothing to poll. when it comes back without
 * signing in, the server hands over why once, and it shows here.
 *
 * a connect from my account comes back with ?oidc=connect; my account opens
 * again so its outcome is seen.
 */

import { openMyAccountsModal } from './my-accounts';

const WRAP_ID = 'login-oidc';
const BUTTON_ID = 'login-oidc-button';
const ERROR_ID = 'login-error';
export const OIDC_START_URL = '/api/auth/oidc/start';

function showError(message: string): void {
  const el = document.getElementById(ERROR_ID);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

/** a connect just came back: drop the marker from the address, reopen my account */
export function resumeOidcConnect(
  open: () => void = openMyAccountsModal,
  loc: Location = window.location,
  hist: History = window.history,
): boolean {
  const params = new URLSearchParams(loc.search);
  if (params.get('oidc') !== 'connect') return false;
  params.delete('oidc');
  const rest = params.toString();
  hist.replaceState(hist.state, '', loc.pathname + (rest ? `?${rest}` : '') + loc.hash);
  open();
  return true;
}

/** show the button when the admin turned oidc sign-in on, and wire it once */
export async function initOidcSignIn(
  go: (url: string) => void = (url) => window.location.assign(url),
): Promise<void> {
  resumeOidcConnect();
  const wrap = document.getElementById(WRAP_ID);
  const button = document.getElementById(BUTTON_ID) as HTMLButtonElement | null;
  if (!wrap || !button || button.dataset.wired === '1') return;
  button.dataset.wired = '1';
  try {
    const resp = await fetch('/api/auth/oidc/available', {
      headers: { Accept: 'application/json' },
    });
    const data = resp.ok
      ? ((await resp.json()) as { enabled?: boolean; label?: string; error?: string | null })
      : {};
    wrap.hidden = data.enabled !== true;
    if (typeof data.label === 'string' && data.label) button.textContent = data.label;
    if (typeof data.error === 'string' && data.error) showError(data.error);
  } catch {
    wrap.hidden = true;
  }
  button.addEventListener('click', () => {
    button.disabled = true;
    button.textContent = 'Redirecting...';
    go(OIDC_START_URL);
  });
}
