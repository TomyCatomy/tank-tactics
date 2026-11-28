#!/usr/bin/env python
"""Visual inspection of the Tank Tactics client on desktop and phones, compared with a baseline version.

Starts two real servers, one on the working tree ("current", uncommitted changes included) and one on a
throwaway git worktree of the baseline ref, sets up the same games on both, and walks every screen
(home, join, lobby tabs, rules, game tabs, action sheets, dead/jury view, fog, game over, closed lobby) on
each device. For every screen it saves a side-by-side image (baseline | current) and runs layout checks
for things players notice: controls that can't be reached or are covered, pages locked from scrolling,
clipped text, sideways scroll, tiny tap targets, text fields that make iOS zoom, a top bar that scrolls
away, junk text and JavaScript errors. Findings that the baseline also has are reported separately.

    .venv/Scripts/python .claude/skills/playtest/scripts/inspect_ui.py --out <dir>
    ... inspect_ui.py --baseline HEAD~1 --out <dir>          # compare with another ref (default: merge-base with origin/staging)
    ... inspect_ui.py --baseline b8801a6 --current 9e02bea --out <dir>   # replay an old regression
    ... inspect_ui.py --baseline none --out <dir>            # no comparison, current only
    ... inspect_ui.py --devices iphone,desktop --only lobby,game --out <dir>
    ... inspect_ui.py --strict --out <dir>                   # CI: no WebKit is a harness error, not a fallback

Writes <out>/report.md (read this first), <out>/report.json and <out>/<device>/<screen>.png comparisons.
Exit code: 0 = no new findings, 1 = new findings, 2 = the harness could not run.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playtest import ROOT, Server  # noqa: E402

import httpx  # noqa: E402

DEVICES = {
    # name: (engine, width, height, phone). WebKit is the engine behind every iOS browser (Safari, Brave, Chrome).
    "desktop": ("chromium", 1280, 800, False),
    "iphone": ("webkit", 390, 664, True),
    "small-android": ("chromium", 320, 568, True),
    "iphone-landscape": ("webkit", 667, 375, True),
}

# Runs in the page. Returns findings: things a player would notice. Restores every scroll position it touches.
CHECKS_JS = r"""
([phone, ids]) => {
  const out = [];
  const add = (kind, el, detail) => out.push({kind, what: el ? desc(el) : '', detail: detail || ''});
  // Labels must match between the two versions, so strip what differs per run: server address, tokens, game codes.
  const norm = s => { s = s.replace(/https?:\/\/[^/\s]+/g, '<site>').replace(/([?&]t=)[^&\s]+/g, '$1<token>');
                      for (const id of ids) s = s.split(id).join('<code>'); return s; };
  const fieldName = el => {
    if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return '';
    const lab = el.closest('label') || (el.id && document.querySelector(`label[for="${el.id}"]`));
    const t = lab ? [...lab.childNodes].filter(n => n !== el && !(n.contains && n.contains(el))).map(n => n.textContent).join(' ') : '';
    return t.trim() || el.placeholder || el.name || '';
  };
  const label = el => norm(((el.getAttribute && el.getAttribute('aria-label')) || fieldName(el) || el.innerText
                            || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 40));
  const desc = el => `${el.tagName.toLowerCase()}${el.classList.length ? '.' + [...el.classList].join('.') : ''} "${label(el)}"`;
  const shown = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    // Content of a closed <details> still has a size in some engines, but nobody can see it.
    const closed = el.closest('details:not([open])');
    if (closed && !(el.tagName === 'SUMMARY' && el.parentElement === closed) && !el.closest('summary')) return false;
    if (el.checkVisibility && !el.checkVisibility({visibilityProperty: true, contentVisibilityAuto: true})) return false;
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !el.closest('[inert]');
  };
  const de = document.documentElement, vw = innerWidth, vh = innerHeight;
  const ov = el => { const s = getComputedStyle(el); return [s.overflowX, s.overflowY]; };

  // Every element that can scroll, with its position, so the checks can scroll freely and then put it all back.
  const scrollers = [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1);
  const saved = scrollers.map(e => [e, e.scrollLeft, e.scrollTop]);
  const winSaved = [scrollX, scrollY];
  const userScrollable = (e, axis) => {
    if (e === de || e === document.body) {
      const v = axis === 'x' ? 0 : 1;
      return ov(de)[v] !== 'hidden' && ov(de)[v] !== 'clip' && ov(document.body)[v] !== 'hidden' && ov(document.body)[v] !== 'clip';
    }
    const v = ov(e)[axis === 'x' ? 0 : 1];
    return v === 'auto' || v === 'scroll';
  };

  // Page-level
  if (de.scrollWidth > vw + 1) add('page-scrolls-sideways', null, `content is ${de.scrollWidth - vw}px wider than the screen`);
  const modalOpen = document.querySelector('dialog[open], #sheet-root .sheet-backdrop');
  if (!modalOpen && de.scrollHeight > vh + 1 && !userScrollable(de, 'y'))
    add('page-locked', null, `page can't scroll but its content is ${de.scrollHeight - vh}px taller than the screen`);
  const text = document.body.innerText;
  for (const junk of ['undefined', '[object Object]', 'NaN']) if (text.includes(junk)) add('junk-text', null, junk);
  if (/(^|[^\w])null([^\w]|$)/.test(text)) add('junk-text', null, 'null');

  // Controls: each must be reachable by the user's own scrolling and not covered by something else.
  const modal = modalOpen;
  const innerOnly = new Map();  // phone: scroll box -> controls that can only be reached by scrolling inside it
  const controls = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary, [role=tab]')]
    .filter(shown).filter(el => !modal || modal.contains(el));
  for (const el of controls) {
    const before = new Map(scrollers.map(e => [e, [e.scrollLeft, e.scrollTop]]));
    const winBefore = [scrollX, scrollY];
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const blocked = [];
    for (const e of scrollers) {
      const [l, t] = before.get(e);
      if (e.scrollLeft !== l && !userScrollable(e, 'x')) blocked.push(e);
      else if (e.scrollTop !== t && !userScrollable(e, 'y')) blocked.push(e);
    }
    if ((scrollY !== winBefore[1] && !userScrollable(de, 'y')) || (scrollX !== winBefore[0] && !userScrollable(de, 'x'))) blocked.push(de);
    if (phone && !modal && !userScrollable(de, 'y')) {
      for (const e of scrollers) {
        if (e === de || e === document.body) continue;
        const [l, t] = before.get(e);
        if (e.scrollTop !== t && userScrollable(e, 'y')) { innerOnly.set(e, (innerOnly.get(e) || 0) + 1); break; }
      }
    }
    if (blocked.length) add('unreachable', el, `only reachable by scrolling ${blocked.map(e => e === de ? 'the page' : desc(e)).join(', ')}, which the user can't scroll`);
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cx > vw || cy < 0 || cy > vh) { add('off-screen', el, `centre at (${Math.round(cx)}, ${Math.round(cy)}) on a ${vw}x${vh} screen`); continue; }
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) continue;  // often visually replaced
    for (const [px, py] of [[cx, cy], [r.left + Math.min(8, r.width / 3), cy], [r.right - Math.min(8, r.width / 3), cy]]) {
      if (px < 0 || px >= vw || py < 0 || py >= vh) continue;
      const hit = document.elementFromPoint(px, py);
      if (!hit || el === hit || el.contains(hit) || hit.contains(el)) continue;
      const lab = hit.closest('label');
      if (lab && lab.contains(el)) continue;
      add('covered', el, `covered by ${desc(hit)}`);
      break;
    }
  }
  for (const [e, l, t] of saved) { e.scrollLeft = l; e.scrollTop = t; }
  scrollTo(winSaved[0], winSaved[1]);
  // On a phone the page is the scroll people expect. A box that scrolls inside a page that can't is easy to miss,
  // and iOS sometimes won't scroll it at all.
  for (const [e, n] of innerOnly)
    add('inner-scroll-only', e, `the page can't scroll; ${n} control(s) are only reachable by scrolling inside this box`);

  // Text cut off by its own box (ellipsis is deliberate and fine).
  for (const el of document.querySelectorAll('body *')) {
    if (!shown(el) || ['INPUT', 'SELECT', 'TEXTAREA', 'svg', 'SVG'].includes(el.tagName) || el.closest('svg')) continue;
    if (modal && !modal.contains(el)) continue;
    const s = getComputedStyle(el);
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!hasText || s.textOverflow === 'ellipsis') continue;
    if ((s.overflowX === 'hidden' || s.overflowX === 'clip') && el.scrollWidth > el.clientWidth + 2) add('clipped-text', el, `${el.scrollWidth - el.clientWidth}px of text cut off`);
    else if ((s.overflowY === 'hidden' || s.overflowY === 'clip') && el.scrollHeight > el.clientHeight + 2 && s.webkitLineClamp === 'none')
      add('clipped-text', el, `${el.scrollHeight - el.clientHeight}px of text cut off at the bottom`);
  }

  if (phone) {
    for (const el of controls) {
      if (el.closest('p, li, .small') && el.tagName === 'A') continue;  // inline links in running text
      const r = el.getBoundingClientRect();
      if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) continue;
      if (r.height < 30 || r.width < 30) add('small-tap-target', el, `${Math.round(r.width)}x${Math.round(r.height)}px`);
    }
    for (const el of document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea')) {
      if (!shown(el)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 16) add('ios-zoom-on-focus', el, `font-size ${fs}px; iOS zooms in on focus below 16px and stays zoomed`);
    }
  }

  // The top bar should still be visible once the user has scrolled as far down as they can.
  const bar = document.querySelector('header.topbar');
  if (bar && !modal) {
    const s = scrollers.filter(e => userScrollable(e, 'y') && e.scrollHeight > e.clientHeight + 1 && !bar.contains(e) && shown(e));
    const sp = s.map(e => [e, e.scrollTop]);
    for (const e of s) e.scrollTop = e.scrollHeight;
    if (userScrollable(de, 'y')) scrollTo(0, de.scrollHeight);
    const b = bar.getBoundingClientRect();
    if (b.bottom <= 1 || b.top >= vh) add('top-bar-scrolls-away', bar, 'after scrolling to the bottom the top bar is off screen');
    for (const [e, t] of sp) e.scrollTop = t;
    scrollTo(winSaved[0], winSaved[1]);
  }
  return out;
}
"""

COMPOSE_HTML = """<!doctype html><html><body style="margin:0;background:#888;font:14px system-ui">
<div style="display:flex;gap:12px;padding:8px;align-items:flex-start">%s</div></body></html>"""

DIFF_JS = """async ([a, b]) => {
  const load = src => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  const [x, y] = await Promise.all([load(a), load(b)]);
  if (x.width !== y.width || x.height !== y.height) return 1;
  const c = document.createElement('canvas'); c.width = x.width; c.height = x.height;
  const g = c.getContext('2d');
  g.drawImage(x, 0, 0); const p = g.getImageData(0, 0, c.width, c.height).data;
  g.drawImage(y, 0, 0); const q = g.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < p.length; i += 4) if (Math.abs(p[i] - q[i]) + Math.abs(p[i+1] - q[i+1]) + Math.abs(p[i+2] - q[i+2]) > 30) n++;
  return n / (p.length / 4);
}"""


# == games set up identically on both servers ==============================================

class Api:
    def __init__(self, url: str):
        self.c = httpx.Client(base_url=url, timeout=20, trust_env=False)

    def call(self, method: str, path: str, seat: dict | None = None, **kw) -> dict:
        headers = {"Authorization": f"Bearer {seat['token']}"} if seat else {}
        r = self.c.request(method, path, headers=headers, **kw)
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    def lobby(self, names: list[str], cfg: dict) -> list[dict]:
        seats = [self.call("POST", "/api/games", json={"name": names[0], "config": cfg})]
        for n in names[1:]:
            seats.append(self.call("POST", f"/api/games/{seats[0]['game_id']}/join", json={"name": n}))
        return seats

    def post(self, seat: dict, what: str, body: dict | None = None) -> dict:
        return self.call("POST", f"/api/games/{seat['game_id']}/{what}", seat, **({"json": body} if body is not None else {}))

    def started(self, names: list[str], cfg: dict) -> list[dict]:
        seats = self.lobby(names, cfg)
        self.post(seats[0], "start")
        return seats


def make_fixtures(api: Api) -> dict:
    # No AP ticks or jury closes while screens are captured.
    quiet = {"ap": {"rrule": "FREQ=MONTHLY;BYMONTHDAY=1"}}
    fx: dict = {}
    fx["lobby"] = api.lobby(["Hosty", "Guesty"], quiet)
    api.post(fx["lobby"][1], "chat", {"text": "hi, ready when you are"})
    fx["closed"] = api.lobby(["Hosty", "Guesty"], quiet)
    api.post(fx["closed"][0], "close")

    cfg = {"board": {"size": 8}, "start": {"hp": 3, "ap": 6, "action_range": 12}, **quiet,
           "jury": {"enabled": True}}
    g = fx["game"] = api.started(["Ana", "Ben", "Cat", "Dan"], cfg)
    for _ in range(3):
        api.post(g[0], "action", {"type": "shoot", "target_id": g[3]["player_id"]})
    api.post(g[1], "action", {"type": "give_ap", "target_id": g[2]["player_id"], "amount": 1})
    api.post(g[0], "chat", {"text": "Dan is out. Truce, Ben?"})
    api.post(g[1], "chat", {"to_id": g[0]["player_id"], "text": "deal, for now"})

    f = fx["fog"] = api.started(["Ana", "Ben", "Cat"], {**cfg, "fog_of_war": "alliance", "jury": {"enabled": False}})
    api.post(f[1], "action", {"type": "give_ap", "target_id": f[2]["player_id"], "amount": 2})
    api.post(f[0], "action", {"type": "shoot", "target_id": f[1]["player_id"]})

    d = fx["finished"] = api.started(["Ana", "Ben"], {**cfg, "start": {"hp": 1, "ap": 2, "action_range": 12}, "jury": {"enabled": False}})
    api.post(d[0], "action", {"type": "shoot", "target_id": d[1]["player_id"]})
    return fx


def game_ids(fx: dict) -> list[str]:
    return sorted({s["game_id"] for seats in fx.values() for s in seats})


# == screens ===============================================================================
# Each group is a generator: it drives a fresh page and yields a screen name whenever there is something to capture.

def settle(pg, ms: int = 200) -> None:
    pg.wait_for_timeout(ms)


def open_seat(pg, url: str, seat: dict, ready: str = ".game-code") -> None:
    pg.goto(f"{url}/#/g/{seat['game_id']}?t={seat['token']}")
    pg.wait_for_selector(ready)
    settle(pg)


def tab_name(text: str) -> str:
    return re.sub(r"[^a-z]+", "-", re.sub(r"\(.*?\)|\d+", "", text.lower())).strip("-") or "tab"


def each_tab(pg, prefix: str) -> Iterator[str]:
    tabs = pg.get_by_role("tab")
    for i in range(tabs.count()):
        tab = tabs.nth(i)
        if not tab.is_visible():
            continue
        name = tab_name(tab.inner_text())
        tab.click()
        settle(pg)
        yield f"{prefix}-tab-{name}"


def tap_tank(pg, api: Api, viewer: dict, target: dict) -> bool:
    st = api.call("GET", f"/api/games/{viewer['game_id']}/state", viewer)
    me = next(p for p in st["players"] if p["id"] == target["player_id"])
    board = pg.locator("svg.board")
    if not board.count():
        return False
    board.scroll_into_view_if_needed()
    point = pg.evaluate("""([x, y]) => { const s = document.querySelector('svg.board');
        const p = s.createSVGPoint(); p.x = x + .5; p.y = y + .5;
        const q = p.matrixTransform(s.getScreenCTM()); return [q.x, q.y]; }""", [me["x"], me["y"]])
    pg.mouse.click(*point)
    try:
        pg.wait_for_selector(".sheet", timeout=3000)
    except Exception:
        return False
    settle(pg)
    return True


def close_sheet(pg) -> None:
    pg.keyboard.press("Escape")
    if pg.locator("#sheet-root .sheet-backdrop").count():
        pg.locator("#sheet-root .sheet-backdrop").click(position={"x": 5, "y": 5})
    settle(pg, 100)


def g_home(pg, url, fx, api):
    pg.goto(url)
    pg.wait_for_selector("text=Create a game")
    settle(pg)
    yield "home"
    fold = pg.locator(".create-fold > summary")
    if fold.count():  # the create form starts folded
        fold.first.click(force=True)
        settle(pg)
        yield "home-create-open"
    det = pg.locator("details.adv summary")
    if det.count():
        det.first.click(force=True)
        settle(pg)
        yield "home-advanced-open"


def g_home_games(pg, url, fx, api):
    open_seat(pg, url, fx["lobby"][0])  # saves the seat, so the home page lists it under "Your games"
    pg.goto(url)
    pg.wait_for_selector(".home-games li")
    settle(pg)
    yield "home-saved-games"
    fold = pg.locator(".games-fold > summary")
    if fold.count():  # the list can be folded away (versions without the fold capture the same screen again)
        fold.first.click(force=True)
        settle(pg)
    yield "home-saved-games-folded"


def g_join(pg, url, fx, api):
    pg.goto(f"{url}/#/join/{fx['lobby'][0]['game_id']}")
    pg.wait_for_selector("input")
    settle(pg)
    yield "join"


def g_lobby(pg, url, fx, api):
    open_seat(pg, url, fx["lobby"][0])
    yield "lobby-host"
    yield from each_tab(pg, "lobby-host")


def g_lobby_guest(pg, url, fx, api):
    open_seat(pg, url, fx["lobby"][1])
    yield "lobby-guest"


def g_rules(pg, url, fx, api):
    open_seat(pg, url, fx["lobby"][0])
    pg.locator("#rules-open").click()
    settle(pg)
    yield "rules"
    pg.locator("#rules-body").evaluate("el => { el.scrollTop = el.scrollHeight; }")
    settle(pg, 100)
    yield "rules-scrolled-to-end"


def g_game(pg, url, fx, api):
    a, b = fx["game"][0], fx["game"][1]
    open_seat(pg, url, a, "svg.board")
    yield "game"
    yield from each_tab(pg, "game")
    first = pg.get_by_role("tab").first
    if first.count():
        first.click()
        settle(pg)
    rejoin = pg.get_by_role("button", name="Your rejoin link")  # on the Me tab
    if rejoin.count():
        rejoin.first.click()
        pg.wait_for_selector(".sheet .rejoin")
        settle(pg)
        yield "game-sheet-rejoin-link"
        close_sheet(pg)
    if tap_tank(pg, api, a, a):
        yield "game-sheet-own-tank"
        close_sheet(pg)
    if tap_tank(pg, api, a, b):
        yield "game-sheet-other-tank"
        close_sheet(pg)


def g_dead(pg, url, fx, api):
    open_seat(pg, url, fx["game"][3], "svg.board")
    yield "dead-player"
    yield from each_tab(pg, "dead-player")


def g_fog(pg, url, fx, api):
    open_seat(pg, url, fx["fog"][2], "svg.board")
    yield "fog"
    yield from each_tab(pg, "fog")


def g_finished(pg, url, fx, api):
    open_seat(pg, url, fx["finished"][0])
    yield "finished-winner"
    open_seat(pg, url, fx["finished"][1])
    yield "finished-loser"


def g_closed(pg, url, fx, api):
    open_seat(pg, url, fx["closed"][1], "#app :is(.card, h2)")
    yield "closed-lobby"


GROUPS: dict[str, Callable] = {
    "home": g_home, "home-games": g_home_games, "join": g_join, "lobby": g_lobby, "lobby-guest": g_lobby_guest, "rules": g_rules,
    "game": g_game, "dead": g_dead, "fog": g_fog, "finished": g_finished, "closed": g_closed,
}


# == capture ===============================================================================

class Version:
    def __init__(self, label: str, root: Path, work: Path):
        self.label, self.root = label, root
        self.srv = Server(work, label, root=root)

    def start(self) -> None:
        self.srv.start()
        self.api = Api(self.srv.url)
        self.fx = make_fixtures(self.api)
        self.ids = game_ids(self.fx)


def capture(browsers, device: str, version: Version, group: str, outdir: Path) -> dict[str, dict]:
    engine, w, hgt, phone = DEVICES[device]
    b, real_engine = browsers[engine]
    kw = {"viewport": {"width": w, "height": hgt}, "has_touch": phone}
    if phone and real_engine != "firefox":
        kw["is_mobile"] = True
    ctx = b.new_context(**kw)
    pg = ctx.new_page()
    pg.set_default_timeout(8000)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(f"JavaScript error: {e}"))
    pg.on("console", lambda m: errors.append(f"console: {m.text[:160]}")
          if m.type == "error" and "Failed to load resource" not in m.text and "WebSocket" not in m.text else None)
    pg.on("dialog", lambda d: d.dismiss())
    shots: dict[str, dict] = {}
    try:
        for name in GROUPS[group](pg, version.srv.url, version.fx, version.api):
            while name in shots:
                name += "-again"
            raw = outdir / "raw" / device / f"{name}.{version.label}"
            raw.parent.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=f"{raw}.png")
            full_h = pg.evaluate("document.documentElement.scrollHeight")
            if full_h > hgt + 4:
                pg.screenshot(path=f"{raw}.full.png", full_page=True)
            findings = pg.evaluate(CHECKS_JS, [phone, version.ids])
            findings += [{"kind": "console-error" if e.startswith("console:") else "js-error", "what": "", "detail": e}
                         for e in errors]
            errors.clear()
            shots[name] = {"png": f"{raw}.png", "full": f"{raw}.full.png" if full_h > hgt + 4 else None, "findings": findings}
    except Exception as e:  # keep going: one broken screen shouldn't hide the rest
        shots[f"{group}-ERROR"] = {"png": None, "full": None,
                                   "findings": [{"kind": "harness-error", "what": "", "detail": f"{type(e).__name__}: {str(e)[:300]}"}]}
    finally:
        ctx.close()
    return shots


def data_uri(path: str) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def compose(page, items: list[tuple[str, str | None]], dest: Path) -> None:
    cols = "".join(
        f'<div><div style="padding:4px 2px;font-weight:700">{label}</div>'
        + (f'<img src="{data_uri(p)}" style="display:block;border:1px solid #000">' if p else
           '<div style="padding:40px;background:#fff">not captured</div>') + "</div>"
        for label, p in items)
    page.set_content(COMPOSE_HTML % cols)
    page.wait_for_load_state()
    dest.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(dest), full_page=True)


def diff_ratio(page, a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    page.set_content("<html><body></body></html>")
    return round(page.evaluate(DIFF_JS, [data_uri(a), data_uri(b)]), 4)


def key(f: dict) -> str:
    return f"{f['kind']}|{f['what']}"


# == main ==================================================================================

def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory (created; existing contents replaced)")
    ap.add_argument("--baseline", help="git ref to compare with, or 'none' (default: where this branch left origin/staging, "
                    "so only this branch's changes count as new)")
    ap.add_argument("--current", help="git ref to inspect instead of the working tree (e.g. to replay an old regression)")
    ap.add_argument("--devices", help=f"comma-separated subset of: {', '.join(DEVICES)}")
    ap.add_argument("--only", help=f"comma-separated subset of: {', '.join(GROUPS)}")
    ap.add_argument("--strict", action="store_true", help="exit 2 instead of falling back to Chromium when WebKit is missing (for CI)")
    args = ap.parse_args()

    devices = [d.strip() for d in args.devices.split(",")] if args.devices else list(DEVICES)
    groups = [g.strip() for g in args.only.split(",")] if args.only else list(GROUPS)
    bad = [d for d in devices if d not in DEVICES] + [g for g in groups if g not in GROUPS]
    if bad:
        print(f"Unknown device or group: {', '.join(bad)}")
        return 2

    out = Path(args.out).resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="tt-inspect-"))
    notes: list[str] = []
    versions: list[Version] = []
    worktrees: list[Path] = []
    try:
        if args.current:
            head, dirty = git("rev-parse", "--short", args.current), False
            current_root = work / "current-src"
            git("worktree", "add", "--detach", str(current_root), args.current)
            worktrees.append(current_root)
            current_desc = f"{args.current} ({head})"
        else:
            head = git("rev-parse", "--short", "HEAD")
            dirty = bool(git("status", "--porcelain", "--", "public", "tank_tactics"))
            current_root = ROOT
            current_desc = f"working tree (HEAD {head}{', with uncommitted changes' if dirty else ''})"
        if args.baseline is None:
            for upstream in ("origin/staging", "origin/main", "main"):
                try:
                    args.baseline = git("merge-base", args.current or "HEAD", upstream)[:9]
                    notes.append(f"Baseline: where this branch left {upstream}.")
                    break
                except subprocess.CalledProcessError:
                    continue
            else:
                print("HARNESS ERROR: no origin/staging, origin/main or main to compare with; pass --baseline <ref> or --baseline none")
                return 2
        if args.baseline != "none":
            try:
                base_sha = git("rev-parse", "--short", args.baseline)
            except subprocess.CalledProcessError:
                print(f"HARNESS ERROR: unknown git ref {args.baseline!r}")
                return 2
            if base_sha == head and not dirty:
                notes.append(f"Baseline {args.baseline} is the same code as the current version, so every finding shows as "
                             "pre-existing. Pass --baseline <older ref> to compare with something else.")
            worktree = work / "baseline-src"
            git("worktree", "add", "--detach", str(worktree), args.baseline)
            worktrees.append(worktree)
            versions.append(Version("baseline", worktree, work))
            base_desc = f"{args.baseline} ({base_sha})"
        versions.append(Version("current", current_root, work))
        for v in versions:
            try:
                v.start()
            except Exception as e:
                print(f"HARNESS ERROR: could not start the {v.label} server or set up its games: {e}")
                return 2

        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browsers: dict[str, tuple] = {}
        for engine in sorted({DEVICES[d][0] for d in devices}):
            try:
                kw = {"channel": "msedge"} if engine == "chromium" else {}
                try:
                    browsers[engine] = (getattr(pw, engine).launch(**kw), engine)
                except Exception:
                    browsers[engine] = (getattr(pw, engine).launch(), engine)
            except Exception as e:
                if engine == "webkit" and not args.strict:
                    notes.append(f"WebKit unavailable ({str(e).splitlines()[0]}); iPhone devices ran in Chromium instead, so "
                                 "iOS-only problems can't show up. Fix with: .venv/Scripts/python -m playwright install webkit")
                    browsers[engine] = None
                else:
                    print(f"HARNESS ERROR: no {engine} browser: {e}")
                    return 2
        if browsers.get("webkit", 1) is None:
            browsers["webkit"] = browsers.get("chromium") or (pw.chromium.launch(), "chromium")

        tool = browsers[next(iter(browsers))][0].new_page()
        screens: list[dict] = []
        t0 = time.monotonic()
        for device in devices:
            for group in groups:
                caps = {v.label: capture(browsers, device, v, group, out) for v in versions}
                names = list(dict.fromkeys(n for c in caps.values() for n in c))
                for name in names:
                    cur, base = caps["current"].get(name), caps.get("baseline", {}).get(name)
                    entry = {"device": device, "screen": name, "group": group}
                    if cur is None:
                        entry.update(only_in="baseline", findings_new=[], findings_pre=[], findings_fixed=[], changed=None)
                    else:
                        base_keys = {key(f) for f in base["findings"]} if base else set()
                        cur_keys = {key(f) for f in cur["findings"]}
                        entry["findings_new"] = [f for f in cur["findings"] if key(f) not in base_keys] if base or len(versions) == 1 else cur["findings"]
                        entry["findings_pre"] = [f for f in cur["findings"] if key(f) in base_keys]
                        entry["findings_fixed"] = [f for f in base["findings"] if key(f) not in cur_keys] if base else []
                        if len(versions) == 1:
                            entry["findings_new"], entry["findings_pre"] = [], cur["findings"]
                        entry["only_in"] = "current" if (len(versions) == 2 and base is None) else None
                        entry["changed"] = diff_ratio(tool, base and base["png"], cur["png"])
                    items = ([("baseline: " + base_desc, base and base["png"])] if len(versions) == 2 else []) + \
                            [("current", cur and cur["png"])]
                    img = out / device / f"{name}.png"
                    compose(tool, items, img)
                    entry["image"] = str(img)
                    fulls = [(lbl, c[name]["full"]) for lbl, c in caps.items() if name in c and c[name]["full"]]
                    if fulls:
                        fimg = out / device / f"{name}.full-page.png"
                        compose(tool, [(f"{lbl} (whole page)", p) for lbl, p in fulls], fimg)
                        entry["full_image"] = str(fimg)
                    screens.append(entry)
                print(f"  {device:<17} {group:<12} {len(names)} screens")
        tool.close()
        for b in {id(x[0]): x[0] for x in browsers.values() if x}.values():
            b.close()
        pw.stop()
    finally:
        for v in versions:
            v.srv.stop()
        for wt in worktrees:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
        time.sleep(0.2)
        shutil.rmtree(work, ignore_errors=True)

    write_report(out, screens, notes, current_desc, base_desc if len(versions) == 2 else None, time.monotonic() - t0)
    new = sum(len(s["findings_new"]) for s in screens)
    print(f"\n{len(screens)} screens on {len(devices)} devices, {new} new findings. Report: {out / 'report.md'}")
    return 1 if new else 0


def fmt(f: dict) -> str:
    return f"**{f['kind']}**" + (f" {f['what']}" if f["what"] else "") + (f": {f['detail']}" if f["detail"] else "")


def write_report(out: Path, screens: list[dict], notes: list[str], current: str, baseline: str | None, secs: float) -> None:
    (out / "report.json").write_text(json.dumps({"current": current, "baseline": baseline, "notes": notes, "screens": screens},
                                                indent=1), encoding="utf-8")
    rel = lambda p: Path(p).relative_to(out).as_posix()  # noqa: E731
    L = ["# UI inspection", "", f"- Current: {current}", f"- Baseline: {baseline or 'none'}",
         f"- {len(screens)} screens, devices: {', '.join(dict.fromkeys(s['device'] for s in screens))}, {secs:.0f}s", ""]
    L += [f"> {n}" for n in notes] + ([""] if notes else [])

    def section(title: str, field: str, blurb: str) -> None:
        rows = [(s, f) for s in screens for f in s[field]]
        L.extend([f"## {title} ({len(rows)})", "", blurb, ""])
        last = None
        for s, f in rows:
            where = f"{s['device']} / {s['screen']}"
            if where != last:
                L.append(f"- {where} ([image]({rel(s['image'])}))")
                last = where
            L.append(f"  - {fmt(f)}")
        L.append("")

    def summary(title: str, field: str, blurb: str) -> None:
        groups: dict[str, list] = {}
        for sc in screens:
            for f in sc[field]:
                groups.setdefault(f["kind"], []).append((sc, f))
        L.extend([f"## {title} ({sum(len(v) for v in groups.values())})", "", blurb, ""])
        for kind, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            where = sorted({f"{sc['device']}/{sc['screen']}" for sc, _ in rows})
            L.append(f"- **{kind}** x{len(rows)} on {len(where)} screens, e.g. {fmt(rows[0][1])} ({where[0]}"
                     + (f", {where[1]}" if len(where) > 1 else "") + (", ..." if len(where) > 2 else "") + ")")
        L.append("")

    if baseline:
        section("New findings", "findings_new", "Present now but not in the baseline. Look at each image before deciding.")
        summary("Fixed since the baseline", "findings_fixed", "Present in the baseline, gone now. Full list in report.json.")
        summary("Pre-existing findings", "findings_pre",
                "The baseline has these too, so they aren't regressions. Full list in report.json.")
    else:
        summary("Findings", "findings_pre", "No baseline, so these can't be split into new and old. Full list in report.json.")
    only = [s for s in screens if s.get("only_in")]
    if only:
        L += ["## Screens in only one version", ""] + [f"- {s['device']} / {s['screen']}: only in {s['only_in']}" for s in only] + [""]
    L += ["## Every screen, most visually changed first", "",
          "Changed = share of pixels that differ from the baseline. Game screens always differ a little "
          "(tank positions are random), so look at the picture, not just the number.", "",
          "| device | screen | changed | images |", "|---|---|---|---|"]
    for s in sorted(screens, key=lambda s: -(s["changed"] if s["changed"] is not None else 2)):
        ch = "n/a" if s["changed"] is None else f"{s['changed'] * 100:.1f}%"
        imgs = f"[view]({rel(s['image'])})" + (f", [whole page]({rel(s['full_image'])})" if s.get("full_image") else "")
        L.append(f"| {s['device']} | {s['screen']} | {ch} | {imgs} |")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
