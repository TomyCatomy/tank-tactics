// Choosing your tank's colour in the lobby: a palette, or any colour from the system picker.
// The server rejects colours too dark for the board or too close to another player's.
import { serverDefaults } from '../api.js';
import { dot, h } from '../dom.js';
import { isolate } from '../format.js';
import { closeSheet, openSheet } from '../notify.js';
import { G, me, postAndRefresh } from './session.js';

/** The button on your own row in the lobby that opens the picker. */
export const colorButton = () => h('button', {
  type: 'button', class: 'small', 'aria-label': 'Change your tank’s colour', onclick: openColorSheet,
}, 'Colour');

async function choose(color) {
  if (await postAndRefresh('color', { color })) closeSheet();
}

export async function openColorSheet() {
  let palette = [];
  try { palette = (await serverDefaults()).colors || []; } catch { }
  const my = me();
  if (!my) return;
  const owner = (c) => G.state.players.find((p) => p.color.toLowerCase() === c);
  const swatch = (c) => {
    const taken = owner(c), mine = taken?.id === my.id;
    return h('button', {
      type: 'button', class: mine ? 'swatch current' : 'swatch', style: { background: c },
      disabled: !!taken && !mine, title: taken && !mine ? `Taken by ${isolate(taken.name)}` : c,
      'aria-label': `Colour ${c}${taken && !mine ? `, taken by ${isolate(taken.name)}` : ''}`, onclick: () => choose(c),
    });
  };
  const custom = h('input', { type: 'color', value: /^#[0-9a-f]{6}$/i.test(my.color) ? my.color : '#3cb44b', 'aria-label': 'Custom colour' });
  custom.addEventListener('change', () => choose(custom.value));
  openSheet(h('h2', {}, dot(my.color), 'Your tank’s colour'),
    h('div', { class: 'swatches' }, palette.map(swatch)),
    h('label', { class: 'row custom-color' }, h('span', {}, 'Or pick any colour'), custom),
    h('p', { class: 'muted small' }, 'Colours too dark for the board, or too close to another player’s, are refused.'));
}
