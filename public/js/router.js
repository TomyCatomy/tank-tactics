// Hash router: #/ (home), #/join/<id>, #/g/<id>[?t=<rejoin token>].
import { $ } from './dom.js';
import { startGame, stopGame } from './game/session.js';
import { closeSheet } from './notify.js';
import { renderHome } from './pages/home.js';
import { renderJoin } from './pages/join.js';
import { saved } from './storage.js';

/** Render the page for the current URL from scratch (also used to re-render after a change). */
export function route() {
  stopGame();
  closeSheet();
  $('#topbar-info').replaceChildren();
  $('#topbar-game').replaceChildren();
  window.scrollTo(0, 0); // a new page starts at the top, not wherever the last one was scrolled to
  const [path, query] = (location.hash.slice(1) || '/').split('?');
  const [page, gid] = path.split('/').filter(Boolean);
  if (page === 'join' && gid) return renderJoin(gid);
  if (page === 'g' && gid) {
    const token = new URLSearchParams(query || '').get('t');
    if (token) {
      // Rejoin link: store the token, then drop it from the URL bar.
      saved.set(gid, { token });
      history.replaceState(null, '', `#/g/${gid}`);
    }
    return startGame(gid);
  }
  return renderHome();
}
