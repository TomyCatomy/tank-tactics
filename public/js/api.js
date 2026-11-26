// HTTP API. Errors are thrown as Error objects with the server's `code` and the HTTP `status`.
import { account } from './storage.js';

export async function api(method, path, body, token) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw Object.assign(new Error('Network error. Is the server running?'), { code: 'network' });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = data.error || {};
    throw Object.assign(new Error(e.message || `Request failed (${res.status})`), { code: e.code, status: res.status });
  }
  return data;
}

/** api() with the account's session token. An expired session signs you out and rethrows. */
export async function accountApi(method, path, body) {
  try {
    return await api(method, path, body, account.token());
  } catch (e) {
    if (e.status === 401 && account.token()) {
      account.clear();
      e.message = 'Your sign-in has expired. Sign in again.';
    }
    throw e;
  }
}

/** Run `load` once and share its promise; a failure is forgotten so the next call retries. */
export function once(load) {
  let promise = null;
  return () => (promise ||= load().catch((e) => { promise = null; throw e; }));
}

/** GET /api/defaults: {config, allow_debug, timezones}. */
export const serverDefaults = once(() => api('GET', '/api/defaults'));

/** GET /api/auth/config: {google_client_id} (null when sign-in is off or the request failed). */
const loadAuthConfig = once(() => api('GET', '/api/auth/config'));
export const authConfig = () => loadAuthConfig().catch(() => ({ google_client_id: null }));

/** The browser's IANA time zone, or null if it has none. */
export function browserZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
}
