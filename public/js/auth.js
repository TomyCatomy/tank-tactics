// Optional "Sign in with Google".
import { accountApi, api, once } from './api.js';
import { fill, h } from './dom.js';
import { toast } from './notify.js';
import { route } from './router.js';
import { account, saved } from './storage.js';

const loadGoogleScript = once(() => new Promise((resolve, reject) => {
  const s = h('script', { src: 'https://accounts.google.com/gsi/client', async: true });
  s.onload = () => resolve(window.google);
  s.onerror = () => reject(new Error('Could not load Google sign-in.'));
  document.head.append(s);
}));

async function onCredential(resp, onSignedIn) {
  let res;
  try {
    res = await api('POST', '/api/auth/google', { credential: resp.credential });
  } catch (e) { toast(e.message, 'error'); return; }
  account.set({ token: res.token, user: res.user });
  toast(`Signed in as ${res.user.name}`, 'good');
  await (onSignedIn ? onSignedIn(res.user) : route());
}

/**
 * Render Google's sign-in button into `el`. Signing in stores the session, then runs `onSignedIn(user)`
 * if given, or re-renders the page.
 */
export async function mountGoogleButton(el, clientId, onSignedIn) {
  try {
    const google = await loadGoogleScript();
    google.accounts.id.initialize({ client_id: clientId, callback: (resp) => onCredential(resp, onSignedIn) });
    // The standard dark pill, in English like the rest of the site. `medium` matters: at `large`, Google swaps in its
    // personalized "Sign in as <name>" button whenever the browser has a Google session.
    google.accounts.id.renderButton(el, { type: 'standard', theme: 'filled_black', shape: 'pill', size: 'medium', text: 'signin_with', locale: 'en' });
  } catch (e) { fill(el, h('span', { class: 'muted small' }, e.message)); }
}

export async function signOut() {
  const token = account.token();
  account.clear();
  try { window.google?.accounts.id.disableAutoSelect(); } catch { }
  if (token) await api('POST', '/api/auth/logout', undefined, token).catch(() => { });
  route();
}

/** Get a seat token for the account's seat in `gid` and remember it on this device. */
export async function claimSeat(gid, name) {
  const res = await accountApi('POST', `/api/me/games/${encodeURIComponent(gid)}/claim`);
  saved.set(gid, { token: res.token, playerId: res.player_id, name });
  return res;
}

/**
 * Link the seats saved on this device to the account, so seats joined before signing in show up on
 * the account's other devices too. The server skips seats that are already linked, so this is safe to repeat.
 */
export async function linkSavedSeats() {
  const tokens = Object.keys(saved.all()).flatMap((gid) => saved.seats(gid).map((s) => s.token)).slice(0, 500);
  if (tokens.length) await accountApi('POST', '/api/me/seats/link', { tokens });
}
