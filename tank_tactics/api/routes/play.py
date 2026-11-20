"""In-game actions and jury votes."""
from __future__ import annotations

from fastapi import APIRouter

from ...game import Action
from ..deps import PlayerDep
from ..schemas import Ok, VoteRequest

router = APIRouter(prefix="/api/games/{game_id}", tags=["play"])


@router.post("/action")
def act(action: Action, player: PlayerDep) -> Ok:
    """Spend AP. The body's ``type`` picks the action: ``move``, ``shoot``,
    ``give_ap``, ``give_hp`` or ``upgrade``."""
    player.service.act(player.game_id, player.player_id, action)
    return Ok()


@router.post("/vote")
def vote(body: VoteRequest, player: PlayerDep) -> Ok:
    """Jurors only (dead players, when the jury is enabled). Re-voting replaces the vote."""
    player.service.vote(player.game_id, player.player_id, body.candidate_id)
    return Ok()
