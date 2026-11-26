// The start page: sign-in, your games, join by code, create a game.
import { accountApi, authConfig, browserZone, serverDefaults } from '../api.js';
import { claimSeat, mountGoogleButton, signOut } from '../auth.js';
import { configForm } from '../config-form.js';
import { $, h } from '../dom.js';
import { toast } from '../notify.js';
import { account, saved } from '../storage.js';
import { friendlyWarning, nameInput, spinner } from './shared.js';

export async function renderHome() {
  document.title = 'Tank Tactics';
  const app = $('#app');
  app.replaceChildren(spinner());
  let defaults, allowDebug, auth;
  try {
    let timezones;
    [{ config: defaults, allow_debug: allowDebug, timezones }, auth] = await Promise.all([serverDefaults(), authConfig()]);
    // A new game's schedule starts in this browser's time zone (the server default is UTC).
    defaults = structuredClone(defaults);
    const zone = browserZone();
    if (zone && timezones?.includes(zone)) defaults.ap.timezone = zone;
  } catch (e) {
    app.replaceChildren(h('div', { class: 'card' }, e.message));
    return;
  }
  const signInOn = !!auth.google_client_id;
  let remote = [];
  if (signInOn && account.token()) {
    try { remote = await accountApi('GET', '/api/me/games'); } catch (e) { if (!account.token()) toast(e.message, 'error'); }
  }
  const user = signInOn ? account.get()?.user : null;
  const gButton = signInOn && !user ? h('div', { class: 'gsi-button' }) : null;

  app.replaceChildren(h('div', { class: 'home' },
    h('section', { class: 'hero' }, h('h1', {}, 'Tank Tactics'),
      h('p', {}, 'Everyone gets one tank and one action point a day. Spend AP to move, shoot, upgrade range or gift it to a friend. Make alliances. Break them. Last tank standing wins.')),
    signInOn ? accountCard(user, gButton) : null,
    myGamesCard(remote, user),
    joinCard(),
    createCard(defaults, allowDebug, user)));
  if (gButton) mountGoogleButton(gButton, auth.google_client_id);
}

function accountCard(user, gButton) {
  if (user) {
    return h('div', { class: 'card row account' },
      h('span', {}, 'Signed in as ', h('b', {}, user.name)),
      h('button', { class: 'shrink', onclick: signOut }, 'Sign out'));
  }
  return h('div', { class: 'account account-signin' },
    h('p', { class: 'muted small' }, 'Optional: sign in to keep your games across devices.'), gButton);
}

/** Games saved on this device (newest first), then any other seats linked to the signed-in account. */
function myGamesCard(remote, user) {
  const local = saved.all();
  const games = Object.entries(local)
    .sort((a, b) => (b[1].savedAt || 0) - (a[1].savedAt || 0))
    .map(([gid, v]) => ({ gid, name: v.name, status: remote.find((g) => g.game_id === gid)?.status }));
  for (const g of remote) if (!local[g.game_id]) games.push({ gid: g.game_id, name: g.name, status: g.status, remote: true });
  if (!games.length) return null;

  const open = async (g) => {
    if (g.remote) {
      try { await claimSeat(g.gid, g.name); } catch (e) { toast(e.message, 'error'); return; }
    }
    location.hash = `#/g/${g.gid}`;
  };
  return h('div', { class: 'card home-games' }, h('h2', {}, user ? 'Your games' : 'Your games on this device'),
    h('ul', { class: 'plist' }, games.map((g) => h('li', { class: 'clickable', onclick: () => open(g) },
      h('span', { class: 'pname' }, g.gid,
        g.name ? h('span', { class: 'muted' }, ` · ${g.name}`) : null,
        g.status ? h('span', { class: 'muted small' }, ` · ${g.status}`) : null),
      h('span', { class: 'pstats' }, 'Open ›')))));
}

function joinCard() {
  const codeIn = h('input', { placeholder: 'Game code, e.g. k7mq2x', autocapitalize: 'off', autocomplete: 'off' });
  const onsubmit = (ev) => {
    ev.preventDefault();
    // Accept a bare code or a pasted invite link.
    const code = codeIn.value.trim().replace(/^.*\/join\//, '').replace(/[^a-z0-9]/gi, '').toLowerCase();
    if (code) location.hash = `#/join/${code}`;
  };
  return h('form', { class: 'card stack home-join', onsubmit },
    h('h2', {}, 'Join a game'), h('div', { class: 'row' }, codeIn, h('button', { class: 'shrink', type: 'submit' }, 'Join')));
}

function createCard(defaults, allowDebug, user) {
  const nameIn = nameInput(user);
  const form = configForm(defaults, { allowDebug });
  const err = h('div', { class: 'error-text' });
  const createBtn = h('button', { class: 'primary', type: 'submit' }, 'Create game');
  const onsubmit = async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    createBtn.disabled = true;
    try {
      const res = await accountApi('POST', '/api/games', { name: nameIn.value, config: form.getValues() });
      saved.set(res.game_id, { token: res.token, playerId: res.player_id, name: nameIn.value.trim() });
      location.hash = `#/g/${res.game_id}`;
    } catch (e) { err.textContent = e.message; } finally { createBtn.disabled = false; }
  };
  return h('form', { class: 'card home-create', onsubmit },
    // Folded by default: most visitors come to join or reopen a game, and the form is long.
    h('details', { class: 'stack create-fold' },
      h('summary', {}, h('h2', {}, 'Create a game')),
      friendlyWarning(),
      h('label', {}, h('span', { class: 'muted small' }, 'Your name'), nameIn),
      form, h('div', { class: 'home-create-actions' }, err, createBtn)));
}
