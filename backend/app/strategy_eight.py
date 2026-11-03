"""Strategy-entry gate: configurable verified virtual LOSE before money entry.

This evaluator is pure. It never places bets or treats an unaccepted LIVE
attempt as a loss. Rows must be in physical round order, from one session.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

STRATEGY_EIGHT = "vote_3_10_24_31_40_after_3_losses"
WAIT_LOSSES = 3
MAX_STEPS = 9
MAX_GAP_SECONDS = 600


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def evaluate_eight(forecasts: list[dict[str, Any]], attempts: dict[str, dict[str, Any]],
                   *, mode: str = "DEMO", wait_losses: int = WAIT_LOSSES,
                   max_steps: int = MAX_STEPS) -> dict[str, Any]:
    """Replay ONLY settled, non-filtered forecasts; return entry state per game.

    DEMO treats a forecast in an active series as a simulated wager.
    LIVE advances a series only on a matched ACCEPTED and settled attempt.
    """
    if mode not in ("DEMO", "LIVE"):
        raise ValueError("Unknown strategy gate mode")
    if isinstance(wait_losses, bool) or not isinstance(wait_losses, int) or not 1 <= wait_losses <= 30:
        raise ValueError("Wait losses must be from 1 to 30")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 20:
        raise ValueError("Max steps must be from 1 to 20")
    phase = "WAITING"
    virtual_losses = 0
    step = 1
    wagers = wins = losses = entries = 0
    wager_history: list[dict[str, Any]] = []
    prior: dict[str, dict[str, Any]] = {}
    previous_virtual_time = None

    def state() -> dict[str, Any]:
        return {"phase": phase, "virtual_losses": virtual_losses, "step": step,
                "bet_allowed": phase == "BETTING" and 1 <= step <= max_steps,
                "wagers": wagers, "wins": wins, "losses": losses,
                "entries": entries, "max_steps": max_steps,
                "required_losses": wait_losses}

    for forecast in forecasts:
        game_id = forecast.get("target_game_id")
        if game_id:
            prior[game_id] = state()
        if (not game_id or forecast.get("mode") in ("FILTER", "MISSED")
                or forecast.get("result") not in ("WIN", "LOSE")):
            continue
        result = forecast["result"]
        final_at = _time(forecast.get("target_finished_at")
                         or forecast.get("target_observed_at"))
        if phase == "WAITING":
            if previous_virtual_time and final_at:
                delta = (final_at - previous_virtual_time).total_seconds()
                if not 0 <= delta <= MAX_GAP_SECONDS:
                    virtual_losses = 0
            elif previous_virtual_time and not final_at:
                virtual_losses = 0
            virtual_losses = virtual_losses + 1 if result == "LOSE" else 0
            previous_virtual_time = final_at
            if virtual_losses >= wait_losses:
                phase = "BETTING"
                step = 1
                entries += 1
            continue
        if phase != "BETTING":
            continue  # A fully exhausted row needs a deliberate reset.
        if mode == "LIVE":
            attempt = attempts.get(game_id)
            if not attempt:
                continue
            # Manual tests, rejected, unknown and unconfirmed bets never advance.
            if attempt.get("signal_source") != "STRATEGY" or attempt.get("single_test_run_id"):
                continue
            if attempt.get("status") != "ACCEPTED" or attempt.get("result") not in ("WIN", "LOSE"):
                continue
            if (attempt["result"] != result or attempt.get("suit") != forecast.get("predicted_suit")
                    or int(attempt.get("step") or 0) != step
                    or (_time(attempt.get("created_at")) and _time(forecast.get("created_at"))
                        and _time(attempt["created_at"]) < _time(forecast["created_at"]))):
                phase = "BLOCKED"
                continue
        wagers += 1
        wager_history.append({"game_id": game_id, "round_number": forecast.get("target_round_number"),
                              "predicted_suit": forecast.get("predicted_suit"),
                              "step": step, "result": result})
        if result == "WIN":
            wins += 1
            phase = "WAITING"
            virtual_losses = 0
            step = 1
            previous_virtual_time = final_at
        else:
            losses += 1
            step += 1
            if step > max_steps:
                phase = "EXHAUSTED"
    return {"before": prior, "current": state(), "mode": mode,
            "wager_history": wager_history}
