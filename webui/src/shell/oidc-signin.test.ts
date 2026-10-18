import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { OIDC_START_URL, initOidcSignIn, resumeOidcConnect } from './oidc-signin';

vi.mock('./my-accounts', () => ({ openMyAccountsModal: vi.fn() }));

/** the login screen's oidc button: shown when on, labelled, a plain navigation */
function dom() {
  document.body.innerHTML = `
    <div id="login-oidc" class="login-oidc" hidden>
      <button id="login-oidc-button" type="button">Sign in with OIDC</button>
    </div>
    <p id="login-error" style="display:none"></p>`;
}

function available(body: Record<string, unknown>) {
  server.use(
    http.get('*/api/auth/oidc/available', () => HttpResponse.json({ success: true, ...body })),
  );
}

beforeEach(() => dom());
afterEach(() => {
  server.resetHandlers();
  document.body.innerHTML = '';
});

describe('the oidc button', () => {
  it('stays hidden when the admin has not turned it on', async () => {
    available({ enabled: false, label: 'Sign in with OIDC', error: null });
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(true);
  });

  it("shows with the admin's label", async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: null });
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(false);
    expect(document.getElementById('login-oidc-button')!.textContent).toBe(
      'Sign in with Pocket ID',
    );
  });

  it('a click goes to the provider through the server', async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: null });
    const go = vi.fn();
    await initOidcSignIn(go);
    const button = document.getElementById('login-oidc-button') as HTMLButtonElement;
    button.click();
    expect(go).toHaveBeenCalledWith(OIDC_START_URL);
    expect(button.disabled).toBe(true);
  });

  it('shows why the last attempt failed', async () => {
    available({ enabled: true, label: 'Sign in with Pocket ID', error: 'Sign-in was cancelled.' });
    await initOidcSignIn(vi.fn());
    const err = document.getElementById('login-error')!;
    expect(err.textContent).toBe('Sign-in was cancelled.');
    expect(err.style.display).toBe('block');
  });

  it('an unreachable server keeps the button hidden', async () => {
    server.use(http.get('*/api/auth/oidc/available', () => HttpResponse.error()));
    await initOidcSignIn(vi.fn());
    expect(document.getElementById('login-oidc')!.hidden).toBe(true);
  });
});

describe('coming back from a connect', () => {
  it('reopens my account and drops the marker', () => {
    const open = vi.fn();
    const replace = vi.fn();
    const loc = { search: '?oidc=connect&x=1', pathname: '/', hash: '#a' } as Location;
    expect(
      resumeOidcConnect(open, loc, { state: null, replaceState: replace } as unknown as History),
    ).toBe(true);
    expect(open).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith(null, '', '/?x=1#a');
  });

  it('does nothing on an ordinary load', () => {
    const open = vi.fn();
    const loc = { search: '', pathname: '/', hash: '' } as Location;
    expect(resumeOidcConnect(open, loc, { replaceState: vi.fn() } as unknown as History)).toBe(
      false,
    );
    expect(open).not.toHaveBeenCalled();
  });
});
