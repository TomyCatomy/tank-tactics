// The join page for an invite link: #/join/<game id>.
import { accountApi, api, authConfig } from '../api.js';
import { claimSeat, mountGoogleButton } from '../auth.js';
import { $, h, messageCard } from '../dom.js';
import { toast } from '../notify.js';
import { route } from '../router.js';
import { account, saved } from '../storage.js';
import { friendlyWarning, nameInput, spinner } from './shared.js';

export async function renderJoin(gid) {
  const app = $('#app');
  if (saved.get(gid)?.token) { location.hash = `#/g/${gid}`; return; }
  app.replaceChildren(spinner());
  const { google_client_id: clientId } = await authConfig();
  // Signed in and already seated in this game (e.g. from another device): just open it.
  if (account.token() && clientId) {
    try { await claimSeat(gid); location.hash = `#/g/${gid}`; return; } catch { /* not seated here */ }
  }
  let info;
  try {
    info = await api('GET', `/api/games/${encodeURIComponent(gid)}`);
  } catch (e) {
    app.replaceChildren(messageCard('Game not found', h('p', {}, e.message)));
    return;
  }
  if (info.status === 'closed') {
    app.replaceChildren(messageCard('This lobby was closed', 'The host cancelled this game before it started.'));
    return;
  }
  if (info.status !== 'lobby') {
    app.replaceChildren(messageCard('This game has already started',
      'New players can only join before the host starts the game. If you are already in it, open your personal rejoin link.'));
    return;
  }

  const nameIn = nameInput(account.get()?.user, { required: true });
  const err = h('div', { class: 'error-text' });
  const join = async (name) => {
    const res = await accountApi('POST', `/api/games/${gid}/join`, { name });
    saved.set(gid, { token: res.token, playerId: res.player_id, name: name.trim() });
    location.hash = `#/g/${gid}`;
  };
  const onsubmit = async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    try { await join(nameIn.value); } catch (e) { err.textContent = e.message; }
  };
  // Not signed in: Google sign-in joins straight away under the account's name, instead of typing one.
  const gButton = clientId && !account.token() ? h('div', { class: 'gsi-button' }) : null;
  const onSignedIn = async (user) => {
    try {
      // Already seated in this game from another device: open that seat rather than joining again.
      try { await claimSeat(gid, user.name); location.hash = `#/g/${gid}`; return; } catch { /* not seated here */ }
      await join(user.name.slice(0, 24));
    } catch (e) {
      route(); // shows the signed-in form, prefilled with the Google name, to pick another
      toast(e.message, 'error');
    }
  };
  app.replaceChildren(h('form', { class: 'card stack', style: { maxWidth: '480px', margin: '0 auto' }, onsubmit },
    h('h2', {}, `Join ${info.host}’s game`),
    h('p', { class: 'muted' }, `${info.player_count} / ${info.max_players} players so far.`),
    friendlyWarning(),
    nameIn, err, h('button', { class: 'primary', type: 'submit' }, 'Join game'),
    gButton ? h('div', { class: 'account-signin join-google' },
      h('p', { class: 'muted small' }, 'Or sign in with Google to join under your Google name and keep this game across devices.'),
      gButton) : null));
  if (gButton) mountGoogleButton(gButton, clientId, onSignedIn);
  nameIn.focus();
}
