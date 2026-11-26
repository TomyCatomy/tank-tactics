// Toasts, browser notifications and the bottom action sheet.
import { $, h } from './dom.js';

export function toast(text, kind = 'info', ms = 4000) {
  const el = h('div', { class: `toast ${kind}` }, text);
  $('#toasts').append(el);
  setTimeout(() => el.remove(), ms);
}

/** A toast, plus a system notification when the tab is in the background and they're allowed. */
export function notify(text, kind) {
  toast(text, kind);
  try {
    if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
      new Notification('Tank Tactics', { body: text, tag: 'tt-' + Date.now() });
    }
  } catch { /* some mobile browsers only allow notifications from a service worker */ }
}

/** Copy `text` to the clipboard; on failure run `fallback` (if any). Returns whether it worked. */
export async function copyText(text, fallback) {
  try { await navigator.clipboard.writeText(text); return true; } catch { fallback?.(); return false; }
}

/** A read-only field with a Copy button. */
export function copyRow(label, value) {
  const input = h('input', { readonly: true, value });
  const copy = async () => { if (await copyText(value, () => input.select())) toast('Copied.', 'good', 1500); };
  return h('label', {}, h('span', { class: 'muted small' }, label),
    h('div', { class: 'copy-row' }, input, h('button', { class: 'shrink', type: 'button', onclick: copy }, 'Copy')));
}

// ----- Action sheet (one at a time, in #sheet-root) -----

const escClose = (e) => { if (e.key === 'Escape') closeSheet(); };

export function closeSheet() {
  $('#sheet-root')?.replaceChildren();
  document.removeEventListener('keydown', escClose);
}

export function openSheet(...content) {
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' }, ...content,
    h('div', { style: { marginTop: '12px' } }, h('button', { class: 'ghost', style: { width: '100%' }, onclick: closeSheet }, 'Close')));
  const backdrop = h('div', { class: 'sheet-backdrop', onclick: (e) => { if (e.target === backdrop) closeSheet(); } }, sheet);
  $('#sheet-root').replaceChildren(backdrop);
  document.addEventListener('keydown', escClose);
  sheet.querySelector('button:not(:disabled), input')?.focus();
}
