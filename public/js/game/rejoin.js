// The player's personal rejoin link, and resetting it if it may have leaked.
import { api } from '../api.js';
import { linkSavedSeats } from '../auth.js';
import { fill, h } from '../dom.js';
import { copyRow, openSheet, toast } from '../notify.js';
import { account } from '../storage.js';
import { G, reconnectNow, replaceToken } from './session.js';

const LABEL = 'Your personal rejoin link (keep it secret: it controls your tank)';

/** The rejoin link with a Copy button, and a button to replace it. Refilled after a reset. */
export function rejoinBlock() {
  return fillRejoin(h('div', { class: 'stack rejoin' }));
}

function fillRejoin(el) {
  const url = `${location.origin}/#/g/${encodeURIComponent(G.gid)}?t=${encodeURIComponent(G.token)}`;
  return fill(el, copyRow(LABEL, url),
    h('div', { class: 'row' },
      h('span', { class: 'muted small' }, 'Shared it by mistake, or lost a device?'),
      h('button', { class: 'small shrink', type: 'button', onclick: resetLink }, 'Reset link')));
}

/** In a match the link lives in a sheet, opened from the Me tab. */
export function openRejoinSheet() {
  openSheet(h('h3', {}, 'Your rejoin link'),
    h('p', { class: 'muted small' }, 'Open it on another device to keep playing there. Anyone with it controls your tank and reads your private messages.'),
    rejoinBlock());
}

async function resetLink(ev) {
  const signedIn = !!account.token();
  if (!confirm('Reset your rejoin link? Every other copy stops working: links you shared or saved, and other '
    + 'devices with this game open. This device gets a new link.'
    + (signedIn ? ' Your other signed-in devices can reopen the game from your account.' : ''))) return;
  const btn = ev.currentTarget;
  btn.disabled = true;
  const session = G;
  session.resetting = true; // the server drops our socket too; don't treat that as being thrown out
  let res;
  try {
    res = await api('POST', `/api/games/${encodeURIComponent(session.gid)}/reset-link`, undefined, session.token);
  } catch (e) {
    toast(e.message, 'error');
    btn.disabled = false;
    reconnectNow(session);
    return;
  }
  replaceToken(session, res.token); // saved even if the player has left the page meanwhile
  // The reset unlinked the seat from any account; link it back to the one signed in here.
  if (signedIn) await linkSavedSeats().catch(() => { });
  if (G !== session) return;
  for (const el of document.querySelectorAll('.rejoin')) fillRejoin(el);
  toast('Link reset. Older copies no longer work.', 'good');
}
