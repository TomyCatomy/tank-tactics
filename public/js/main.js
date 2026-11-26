// Tank Tactics client entry point. Plain JS modules, no build step.
// The server is the source of truth: the client only renders the per-viewer state the server
// sends and posts intents back. Never trust anything here.
import { $ } from './dom.js';
import { chatOnScreen, phoneLobby, renderChat } from './game/chat.js';
import { G } from './game/session.js';
import { route } from './router.js';

// ----- Rules modal
// Lives outside #app and the top-bar slots, so state pushes never close or reset it.
const rulesModal = $('#rules-modal');
let pressedBackdrop = false;
$('#rules-open').addEventListener('click', () => {
  rulesModal.showModal();
  $('#rules-body').scrollTop = 0;
});
$('#rules-close').addEventListener('click', () => rulesModal.close());
// Require press and release on the backdrop, so dragging a text selection out doesn't close it.
rulesModal.addEventListener('pointerdown', (e) => { pressedBackdrop = e.target === rulesModal; });
rulesModal.addEventListener('click', (e) => {
  if (e.target === rulesModal && pressedBackdrop) rulesModal.close();
  pressedBackdrop = false;
});

// ----- Chat visibility: coming back to the tab, or rotating / resizing into the lobby's
// side-by-side layout, can bring the chat into view (and clear its unread count).
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && G?.state && G.nodes?.chat && chatOnScreen()) renderChat();
});
phoneLobby.addEventListener('change', () => { if (G?.state && G.layout === 'lobby') renderChat(); });

window.addEventListener('hashchange', route);
route();
