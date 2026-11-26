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
