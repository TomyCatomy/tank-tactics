// Local storage: per-device convenience only (a seat token can be re-shared with its rejoin link).
// Every access is guarded: storage can be unavailable, and the session still works until reload.

function read(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function write(key, value) {
  try {
    if (value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch { /* storage unavailable */ }
}

/**
 * Seats saved on this device, by game id: {token, playerId, name, savedAt, seats?}.
 * With debug "local seats" one browser controls several players of a game: the entry's own
 * token/playerId/name is the active seat, and `seats` holds the others by player id.
 */
export const saved = {
  all: () => read('tt.games', {}),
  get(gid) { return this.all()[gid] || null; },
  set(gid, v) { write('tt.games', { ...this.all(), [gid]: { ...v, savedAt: Date.now() } }); },
  del(gid) {
    const all = this.all();
    delete all[gid];
    write('tt.games', all);
  },

  /** Every seat in `gid` controlled from here, the active one included: [{playerId, token, name}]. */
  seats(gid) {
    const v = this.get(gid);
    if (!v?.token) return [];
    const seats = { ...v.seats };
    if (v.playerId) seats[v.playerId] = { token: v.token, name: v.name };
    return Object.entries(seats).map(([playerId, s]) => ({ playerId, ...s }));
  },
  addSeat(gid, seat) {
    const v = this.get(gid) || {};
    const seats = { ...v.seats };
    if (v.playerId) seats[v.playerId] = { token: v.token, name: v.name };
    seats[seat.playerId] = { token: seat.token, name: seat.name };
    this.set(gid, { ...v, seats });
  },
  /**
   * Forget the seat with this token (idempotent: the WebSocket "you're out" message and the
   * Leave button can both report the same departure). If it was the active seat, another
   * local seat becomes active. Returns true if any seat for this game remains.
   */
  dropSeat(gid, token) {
    const v = this.get(gid);
    if (!v) return false;
    const rest = this.seats(gid).filter((x) => x.token !== token);
    if (!rest.length) { this.del(gid); return false; }
    const seats = Object.fromEntries(rest.map((x) => [x.playerId, { token: x.token, name: x.name }]));
    const active = rest.find((x) => x.token === v.token) || rest[0];
    this.set(gid, { ...v, seats, token: active.token, playerId: active.playerId, name: active.name });
    return true;
  },
  /** Swap one of this game's seat tokens for a new one (after a link reset). */
  replaceToken(gid, oldToken, token) {
    const v = this.get(gid);
    if (!v) return;
    const seats = Object.fromEntries(Object.entries(v.seats || {})
      .map(([pid, s]) => [pid, s.token === oldToken ? { ...s, token } : s]));
    this.set(gid, { ...v, seats, token: v.token === oldToken ? token : v.token });
  },
  useSeat(gid, playerId) {
    const v = this.get(gid);
    const seat = this.seats(gid).find((x) => x.playerId === playerId);
    if (v && seat) this.set(gid, { ...v, token: seat.token, playerId, name: seat.name });
  },
};

/**
 * The optional Google sign-in session: {token, user}. It only identifies the account (to list
 * and claim seats, and to link new seats); game requests keep using the seat tokens in `saved`.
 */
export const account = {
  get: () => read('tt.session', null),
  set: (v) => write('tt.session', v),
  clear: () => write('tt.session', undefined),
  token() { return this.get()?.token || null; },
};
