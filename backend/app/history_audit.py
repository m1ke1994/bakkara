"""Historical recalculation. Never writes original forecasts or LIVE ledgers."""
import json
import sqlite3
from datetime import datetime, timezone


def audit_history(path, session_id, rows, strategy):
    from .cycle18 import (round_number, source_verified, predict_target, captured_cards,
                          strategy_offsets, ROUND_MAX, hot_window_analysis, facecard_53_analysis,
                           STRATEGY_53, STRATEGY_HOT_8,
                          STRATEGY_FIVE)
    from .observation import evaluate_prediction
    counts = {}
    for row in rows:
        number = round_number(row.get("round_number"))
        if number is not None:
            counts[number] = counts.get(number, 0) + 1
    sources = {round_number(row.get("round_number")): row for row in rows
               if counts.get(round_number(row.get("round_number"))) == 1 and source_verified(row, strategy)}
    audited = []
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS cycle18_history_audit (
            session_id INTEGER NOT NULL,target_game_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,updated_at TEXT NOT NULL,
            PRIMARY KEY(session_id,target_game_id))""")
        original = {row["target_game_id"]: dict(row) for row in db.execute(
            "SELECT * FROM cycle18_forecasts WHERE session_id=?", (session_id,))}
        for target in rows:
            number = round_number(target.get("round_number"))
            if number is None:
                continue
            prediction = predict_target(number, sources, strategy) if counts[number] == 1 else None
            missing = [((number-offset-1) % ROUND_MAX)+1 for offset in strategy_offsets(strategy)
                       if ((number-offset-1) % ROUND_MAX)+1 not in sources]
            saved = original.get(target["game_id"])
            # Strategy 5 depends on the chronological LOSE state at publishing.
            # Never pretend a retroactive base vote contains its step-3 filter.
            suit = (saved["predicted_suit"] if saved else None) if strategy == STRATEGY_FIVE else (
                prediction["predicted_suit"] if prediction else None)
            result = (evaluate_prediction(suit, captured_cards(target),
                finished_confirmed=bool(target.get("finished_at")),
                player_hand_final=bool(target.get("player_hand_final")),
                final_snapshot_reverified=target.get("final_snapshot_reverified") == 1)
                if suit else "UNKNOWN")
            hot_reason = (hot_window_analysis(number, sources).get("reason", "")
                          if strategy == STRATEGY_HOT_8 and counts[number] == 1 else
                          facecard_53_analysis(number, sources).get("reason", "")
                          if strategy == STRATEGY_53 and counts[number] == 1 else "")
            reason = ("Неоднозначный номер целевой раздачи" if counts[number] != 1 else
                      "Нет подтверждённых источников: " + ", ".join(map(str, missing)) if missing else
                      hot_reason if hot_reason else
                      "Окончательные карты цели не подтверждены" if not source_verified(target, strategy) else
                      "Стратегия 5: сверка сохранённого прогноза" if saved and strategy == STRATEGY_FIVE else
                      "Стратегия 5: не было своевременного прогноза; задний пересчёт невозможен" if strategy == STRATEGY_FIVE else
                      "Исторический пересчёт; исходный прогноз сохранён" if saved else
                      "Исторический пересчёт; своевременный прогноз не был записан")
            item = {"target_game_id": target["game_id"], "target_round_number": number,
                    "predicted_suit": suit, "result": result,
                    "votes": prediction["votes"] if prediction else [],
                    "missing_sources": missing, "reason": reason,
                    "original_suit": saved["predicted_suit"] if saved else None,
                    "original_result": saved["result"] if saved else None,
                    "changed": bool(saved and (saved["predicted_suit"] != suit or saved["result"] != result))}
            payload = json.dumps(item, ensure_ascii=False)
            db.execute("""INSERT INTO cycle18_history_audit VALUES(?,?,?,?)
                ON CONFLICT(session_id,target_game_id) DO UPDATE SET
                payload_json=excluded.payload_json,updated_at=excluded.updated_at
                WHERE payload_json!=excluded.payload_json""",
                (session_id, target["game_id"], payload, datetime.now(timezone.utc).isoformat()))
            audited.append(item)
    return audited[-300:][::-1]
