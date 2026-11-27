// Bits shared by the home and join pages.
import { h } from '../dom.js';

export const spinner = (text = 'Loading…') => h('div', { class: 'spinner' }, text);

export const friendlyWarning = () =>
  h('p', { class: 'notice' }, 'This game can get personal. Play with people who’ll still be your friends afterwards.');

/** The display-name field, prefilled with the signed-in `user`'s name. */
export const nameInput = (user, attrs = {}) => h('input', {
  maxlength: 24, placeholder: 'Your display name', autocomplete: 'nickname', dir: 'auto', value: user?.name?.slice(0, 24) || '', ...attrs,
});
