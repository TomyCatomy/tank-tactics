"""Run a full Tank Tactics game with bots at accelerated time and print the log.

Uses the real game-logic module with a virtual clock (debug fast ticks: one AP tick
per second; the jury closes on the same ticks), so a game that would take weeks finishes instantly. Use it to sanity-check
balance:

    python scripts/simulate.py --players 12 --seed 7
    python scripts/simulate.py --players 20 --jury --fog alliance --quiet
    python scripts/simulate.py --runs 200 --quiet        # aggregate stats only
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tank_tactics import game  # noqa: E402
from tank_tactics.config import GameConfig  # noqa: E402

PERSONALITIES = ("aggressive", "hoarder", "diplomat", "coward")


class Bot:
    def __init__(self, pid: str, personality: str, rng: random.Random):
        self.id, self.personality, self.rng = pid, personality, rng
        self.ally: str | None = None

    def act(self, s: game.GameState, now: float) -> None:
        """Spend AP according to personality. Each call may take several actions."""
        me = game.get_player(s, self.id)
        for _ in range(20):  # safety bound
            if s.status != "active" or not me.alive or not self._step(s, me, now):
                return

    def _enemies(self, s: game.GameState, me: game.Player) -> list[game.Player]:
        others = [p for p in game.living_players(s) if p.id != me.id]
        # Diplomats spare their ally until only the two of them are left.
        if self.ally and len(others) > 1:
            others = [p for p in others if p.id != self.ally]
        return others

    def _step(self, s: game.GameState, me: game.Player, now: float) -> bool:
        enemies = self._enemies(s, me)
        if not enemies:
            return False
        in_range = [p for p in enemies if game.in_range(me, p.pos)]
        killable = [p for p in in_range if p.hp <= me.ap]
        nearest = min(enemies, key=lambda p: (game.distance(me.pos, p.pos), p.hp))

        def shoot(t: game.Player) -> bool:
            game.shoot(s, me.id, t.id, now)
            return True

        if self.personality == "aggressive":
            if in_range and me.ap >= 1:
                return shoot(min(in_range, key=lambda p: p.hp))
            if me.ap >= 2:  # keep one AP to shoot after closing in
                return self._approach(s, me, nearest, now)
            return False

        if self.personality == "hoarder":
            if killable:
                return shoot(min(killable, key=lambda p: p.hp))
            cap = s.config.actions.max_range
            if me.ap >= s.config.actions.range_upgrade_cost + 2 and (cap is None or me.action_range < cap):
                game.upgrade_range(s, me.id, now)
                return True
            if in_range and me.ap >= 6:
                return shoot(min(in_range, key=lambda p: p.hp))
            return False

        if self.personality == "diplomat":
            if self.ally is None or not game.get_player(s, self.ally).alive:
                friends = [p for p in game.living_players(s) if p.id != me.id]
                self.ally = min(friends, key=lambda p: game.distance(me.pos, p.pos)).id if friends else None
                enemies = self._enemies(s, me)
            ally = game.get_player(s, self.ally) if self.ally else None
            if ally and ally.alive and game.in_range(me, ally.pos) and me.ap >= 3 and self.rng.random() < 0.3:
                game.give_ap(s, me.id, ally.id, 1, now)
                return True
            if ally and ally.alive and ally.hp == 1 and me.hp >= 3 and game.in_range(me, ally.pos) and s.config.actions.allow_give_hp:
                game.give_hp(s, me.id, ally.id, 1, now)
                return True
            if killable or (in_range and me.ap >= 2):
                return shoot(min(killable or in_range, key=lambda p: p.hp))
            if me.ap >= 3:
                return self._approach(s, me, nearest, now)
            return False

        # coward: runs from threats, shoots only to kill.
        if killable:
            return shoot(killable[0])
        threats = [p for p in enemies if game.in_range(p, me.pos)]
        if threats and me.ap >= 1:
            return self._flee(s, me, threats, now)
        return False

    def _neighbors(self, s: game.GameState, me: game.Player) -> list[tuple[int, int]]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                t = (me.x + dx, me.y + dy)
                if (dx or dy) and game.in_bounds(s, t) and not game.occupant_at(s, t):
                    out.append(t)
        return out

    def _approach(self, s: game.GameState, me: game.Player, target: game.Player, now: float) -> bool:
        options = [t for t in self._neighbors(s, me) if game.distance(t, target.pos) < game.distance(me.pos, target.pos)]
        if not options:
            return False
        game.move(s, me.id, self.rng.choice(options), now)
        return True

    def _flee(self, s: game.GameState, me: game.Player, threats: list[game.Player], now: float) -> bool:
        def danger(t):
            return sum(1 for p in threats if game.distance(t, p.pos) <= p.action_range)

        options = self._neighbors(s, me)
        if not options:
            return False
        best = min(options, key=danger)
        if danger(best) >= danger(me.pos):
            return False
        game.move(s, me.id, best, now)
        return True


def describe(s: game.GameState, e: game.LogEntry, t0: float) -> str:
    def n(pid: str | None) -> str:
        p = game.find_player(s, pid) if pid else None
        return p.name if p else "?"

    t = f"[t+{e.ts - t0:7.2f}s]"
    if e.event_type == "game_start":
        return f"{t} Game started on a {e.details['board_size']}x{e.details['board_size']} board."
    if e.event_type == "move":
        return f"{t} {n(e.actor_id)} moved {e.from_xy} -> {e.to_xy}"
    if e.event_type == "shoot":
        return f"{t} {n(e.actor_id)} shot {n(e.target_id)} (HP now {e.details['target_hp_after']})"
    if e.event_type == "kill":
        return f"{t} *** {n(e.actor_id)} DESTROYED {n(e.target_id)} ***"
    if e.event_type == "loot":
        return f"{t} {n(e.actor_id)} looted {e.amount} AP from {n(e.target_id)}"
    if e.event_type == "give_ap":
        return f"{t} {n(e.actor_id)} gave {e.amount} AP to {n(e.target_id)}"
    if e.event_type == "give_hp":
        return f"{t} {n(e.actor_id)} gave {e.amount} HP to {n(e.target_id)}"
    if e.event_type == "upgrade":
        return f"{t} {n(e.actor_id)} upgraded range to {e.amount}"
    if e.event_type == "ap_tick":
        return f"{t} AP tick (+{e.amount}) for {len(e.details['recipients'])} tanks"
    if e.event_type == "jury_vote":
        return f"{t} juror {n(e.actor_id)} votes for {n(e.target_id)}"
    if e.event_type == "jury_award":
        return f"{t} Jury awards +1 AP to {', '.join(n(w) for w in e.details['winners'])}"
    if e.event_type == "game_over":
        return f"{t} GAME OVER ({e.details['reason']}). Winner: {', '.join(n(w) for w in e.details['winners'])}"
    return f"{t} {e.event_type}"


def simulate(args: argparse.Namespace, seed: int, verbose: bool) -> tuple[game.GameState, dict[str, Bot]]:
    rng = random.Random(seed)
    patch = {
        "debug": {"fast_ticks": True, "tick_seconds": 1},  # one AP tick per virtual second
        "jury": {"enabled": args.jury},  # cycles close on the AP ticks
        "fog_of_war": args.fog,
        "max_duration_seconds": args.max_seconds,
        "players": {"maximum": max(args.players, 2)},
    }
    cfg = GameConfig.parse(patch)
    t0 = 0.0
    names = [f"Bot{i:02d}" for i in range(args.players)]
    s = game.create_game("sim", "p0", names[0], cfg, t0)
    for i in range(1, args.players):
        game.add_player(s, f"p{i}", names[i], t0)
    game.start_game(s, "p0", t0, rng)
    bots = {p.id: Bot(p.id, PERSONALITIES[i % len(PERSONALITIES)], rng) for i, p in enumerate(s.players)}
    for p in s.players:
        p.name = f"{p.name}-{bots[p.id].personality}"

    step = 0.1  # bots get a chance to act ten times per (virtual) second
    now = t0
    while s.status == "active":
        now = round(now + step, 6)
        game.advance_time(s, now, rng)
        order = list(game.living_players(s))
        rng.shuffle(order)
        for p in order:
            if s.status != "active":
                break
            if p.alive and rng.random() < 0.15:  # bots don't check in constantly
                bots[p.id].act(s, now)
        if s.config.jury.enabled and s.status == "active":
            living = game.living_players(s)
            for p in s.players:
                if game.is_juror(s, p) and rng.random() < 0.05:
                    choice = min(living, key=lambda x: (x.hp, rng.random()))  # sympathy vote for the weakest
                    game.cast_jury_vote(s, p.id, choice.id, now)

    if verbose:
        for e in s.log:
            if e.event_type == "ap_tick" and not args.show_ticks:
                continue
            print(describe(s, e, t0))
    return s, bots


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--jury", action="store_true", help="enable the jury of dead players")
    ap.add_argument("--fog", choices=("off", "alliance"), default="off")
    ap.add_argument("--max-seconds", type=int, default=600, help="virtual time limit (default 600 ticks)")
    ap.add_argument("--runs", type=int, default=1, help="run many games and print aggregate stats")
    ap.add_argument("--show-ticks", action="store_true", help="include AP tick lines in the log")
    ap.add_argument("--quiet", action="store_true", help="don't print the full log")
    args = ap.parse_args()
    if args.players < 2:
        ap.error("need at least 2 players")

    base_seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    wins: Counter[str] = Counter()
    durations, reasons = [], Counter()
    for r in range(args.runs):
        s, bots = simulate(args, base_seed + r, verbose=not args.quiet and args.runs == 1)
        for w in s.winner_ids:
            wins[bots[w].personality] += 1 / len(s.winner_ids)
        durations.append((s.ended_at or 0) - (s.started_at or 0))
        reasons[s.end_reason or "?"] += 1

        if args.runs == 1:
            print("\nFinal standings:")
            for p in sorted(s.players, key=lambda p: (not p.alive, -(p.died_at or 1e18))):
                status = "ALIVE" if p.alive else f"died t+{p.died_at:.1f}s"
                print(f"  {p.name:<22} {status:<16} hp={p.hp} range={p.action_range} ap={p.ap}")
            print(f"\nseed={base_seed} ticks={durations[0]:.1f} reason={s.end_reason}")

    if args.runs > 1:
        print(f"{args.runs} games, {args.players} players, seed {base_seed}..{base_seed + args.runs - 1}")
        print(f"avg length: {sum(durations) / len(durations):.1f} AP ticks; end reasons: {dict(reasons)}")
        print("win share by personality:")
        for name in PERSONALITIES:
            print(f"  {name:<11} {wins[name] / args.runs:6.1%}")


if __name__ == "__main__":
    main()
