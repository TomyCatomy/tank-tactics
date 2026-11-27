// Tiny DOM helpers. Never use innerHTML with user data: build nodes with h() / sv().

export const $ = (sel, root = document) => root.querySelector(sel);

const SVGNS = 'http://www.w3.org/2000/svg';
const isEmpty = (v) => v === null || v === undefined || v === false;

function build(el, attrs, kids) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (isEmpty(v)) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.setAttribute('class', v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k in el && !(el instanceof SVGElement) && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (!isEmpty(kid)) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/** An HTML element. Attributes `on*` add listeners; null/false attributes and children are skipped. */
export const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);

/** An SVG element, same rules as h(). */
export const sv = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);

/** Like el.replaceChildren(), but skips null/false (replaceChildren would render them as text). */
export const fill = (el, ...kids) => { el.replaceChildren(); return build(el, {}, kids); };

const CONTROLS = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);
/** A control's identity: its label, or failing that its text. Controls only morph into the same control. */
const sameControl = (a, b) => !CONTROLS.has(a.nodeName)
  || (a.getAttribute('aria-label') || a.textContent) === (b.getAttribute('aria-label') || b.textContent);

/**
 * Make `el` look like `next` (a freshly built copy of it), changing as little of the DOM as
 * possible: matching elements keep their identity, focus, typed value and listeners, so `next`
 * must come from the same builder. A control whose label changed is replaced, listeners and all.
 */
export function morph(el, next) {
  if (el.nodeName !== next.nodeName || (el.nodeType === Node.ELEMENT_NODE && !sameControl(el, next))) { el.replaceWith(next); return; }
  if (el.nodeType !== Node.ELEMENT_NODE) { if (el.nodeValue !== next.nodeValue) el.nodeValue = next.nodeValue; return; }
  for (const { name } of [...el.attributes]) if (!next.hasAttribute(name)) el.removeAttribute(name);
  for (const { name, value } of next.attributes) if (el.getAttribute(name) !== value) el.setAttribute(name, value);
  const kids = [...el.childNodes], nextKids = [...next.childNodes];
  nextKids.forEach((n, i) => (i < kids.length ? morph(kids[i], n) : el.append(n)));
  for (const k of kids.slice(nextKids.length)) k.remove();
}

/**
 * A whole-number <input type=number> that never holds a negative: '-', '+', 'e' and '.' can't be
 * typed, and a pasted or autofilled value is turned into a whole, non-negative number.
 */
export function wholeNumberInput(attrs) {
  const input = h('input', { type: 'number', inputmode: 'numeric', min: 0, step: 1, ...attrs });
  input.addEventListener('keydown', (e) => { if (['-', '+', 'e', 'E', '.', ','].includes(e.key)) e.preventDefault(); });
  input.addEventListener('input', () => {
    if (input.value === '' || /^\d+$/.test(input.value)) return;
    const n = Math.trunc(Math.abs(Number(input.value)));
    input.value = Number.isFinite(n) ? String(n) : '';
  });
  return input;
}

/** A colour dot for a player. */
export const dot = (color) => h('span', { class: 'dot', style: { background: color } });

/** A card with a heading, a paragraph (a string shows muted) and a link back to the start page. */
export const messageCard = (title, text, { back = 'Back', narrow = false } = {}) =>
  h('div', { class: narrow ? 'card stack' : 'card', style: narrow ? { maxWidth: '520px', margin: '0 auto' } : null },
    h('h2', {}, title), typeof text === 'string' ? h('p', { class: 'muted' }, text) : text, h('a', { href: '#/' }, back));

/** A row of tab buttons; `tabs` is [[key, label], ...]. The chat tab gets an unread badge. */
export const tabButtons = (tabs, active, onSelect, unread = 0) => tabs.map(([k, label]) => h('button', {
  type: 'button', role: 'tab', 'aria-selected': String(active === k), onclick: () => onSelect(k),
}, label, k === 'chat' && unread ? h('span', { class: 'count' }, String(unread)) : null));

let tipCount = 0;
/**
 * A "?" circle that shows `text` in a tooltip on hover, keyboard focus or tap (Safari doesn't
 * focus buttons on tap, so a click toggles it too). Place it inside a positioned container:
 * the bubble spans that container's width below it, so it never runs off a phone screen.
 */
export function helpTip(text) {
  const id = `tip-${++tipCount}`;
  const tip = h('span', { class: 'help-tip-text', role: 'tooltip', id }, text);
  const btn = h('button', {
    type: 'button', class: 'help-tip', 'aria-label': 'More info', 'aria-describedby': id,
    onclick: () => btn.classList.toggle('open'),
    onblur: () => btn.classList.remove('open'),
    onkeydown: (e) => { if (e.key === 'Escape') { btn.classList.remove('open'); btn.blur(); } },
  }, h('span', { class: 'help-tip-icon', 'aria-hidden': 'true' }, '?'));
  return h('span', { class: 'help-tip-wrap' }, btn, tip);
}
