"""Read-only Baccarat observation helpers.

The functions in this module intentionally work on DOM data only.  They do
not inspect odds, create coupons, or interact with any betting controls.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .suit_calculator import (
    SUIT_SYMBOL_TO_NAME,
    calculate_cards_suit,
    calculate_final_result,
    normalize_suit,
)


@dataclass
class Game:
    game_id: str
    url: str
    time: str
    round_number: str
    status: str
    position: int
    active: bool = False
    player_score: str = ""
    banker_score: str = ""
    status_text: str = ""
    round_number_diagnostic: str = ""
    round_row_fragment: str = ""
    finished: bool = False
    started: bool = False
    is_countdown_to_start: bool = False
    timer_evidence: str = ""
    state_evidence: str = ""


def is_finished_status(value: str) -> bool:
    normalized = value.casefold()
    negated = re.search(r"\bне\s+(?:была\s+)?(?:заверш|оконч)", normalized)
    return not negated and any(token in normalized for token in ("заверш", "оконч"))


def is_finished_flag_text(value: str) -> bool:
    """The in-game terminal flag is an exact, visible-text match, not a CSS class."""
    return " ".join(value.casefold().split()) == "игра завершена"


def classify_game_status(value: str) -> str:
    """Normalize only explicit status text; never infer state from a timer."""
    normalized = " ".join(value.casefold().split())
    if is_finished_status(normalized):
        return "Завершена"
    if any(token in normalized for token in ("идёт", "идет", "в игре", "live", "проводится")):
        return "Идёт"
    if any(token in normalized for token in ("ожида", "предстоит", "скоро", "ставки до начала")):
        return "Ожидается"
    return "Не удалось определить"


_COUNTDOWN_MARKERS = (
    "countdown", "pre-match", "before start", "not started", "ожидает начала",
    "до начала", "предстоит", "обратный отсчет", "обратный отсчёт",
)
_STARTED_MARKERS = (
    "live", "in progress", "started", "в игре", "идёт", "идет", "началась", "начался",
)


def _started_evidence(value: str) -> bool:
    """Ignore explicit negations such as ``not started`` and ``не началась``."""
    normalized = value.casefold()
    normalized = re.sub(r"\bnot[\s_-]+(?:started|in[\s_-]+progress|live)\b", " ", normalized)
    normalized = re.sub(r"\bне\s+(?:началась|начался|идёт|идет|в игре)\b", " ", normalized)
    return any(marker in normalized for marker in _STARTED_MARKERS)


def classify_game(game: Game) -> str:
    """Classify from explicit DOM state; a timer or 0:0 alone is insufficient."""
    status_text = " ".join((game.status_text, game.status)).casefold()
    timer_evidence = game.timer_evidence.casefold()
    state_evidence = game.state_evidence.casefold()
    if (game.finished or game.status.upper() == "FINISHED"
            or is_finished_status(status_text)
            or is_finished_status(state_evidence)
            or bool(re.search(r"\b(?:finished|ended)\b", f"{status_text} {state_evidence}"))):
        return "FINISHED"

    player_score = _score_value(game.player_score)
    banker_score = _score_value(game.banker_score)
    if (game.started or game.status.upper() == "STARTED"
            or _started_evidence(status_text)
            or _started_evidence(timer_evidence)
            or _started_evidence(state_evidence)
            or (player_score is not None and player_score > 0)
            or (banker_score is not None and banker_score > 0)):
        return "STARTED"

    countdown = (game.is_countdown_to_start
                 or any(marker in status_text for marker in _COUNTDOWN_MARKERS)
                 or any(_explicit_timer_state(timer_evidence, marker) for marker in _COUNTDOWN_MARKERS)
                 or any(_explicit_timer_state(state_evidence, marker) for marker in _COUNTDOWN_MARKERS))
    if countdown and bool(game.time.strip()) and player_score == 0 and banker_score == 0:
        return "UPCOMING"
    return "UNKNOWN"


def _score_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def _explicit_timer_state(evidence: str, marker: str) -> bool:
    """Match status metadata, never the timer's numeric display text."""
    normalized = re.sub(r"[\s_-]+", " ", evidence.casefold())
    normalized_marker = re.sub(r"[\s_-]+", " ", marker.casefold())
    if marker in {"live", "started", "in progress"}:
        return bool(re.search(rf"(?<![a-z]){re.escape(normalized_marker)}(?![a-z])", normalized))
    if marker in {"countdown", "pre-match", "before start", "not started"}:
        return bool(re.search(rf"(?<![a-z]){re.escape(normalized_marker)}(?![a-z])", normalized))
    return normalized_marker in normalized


def is_same_upcoming_game(selected: Game, observed: Game) -> bool:
    """Keep the exact selected future game even if it starts during the handoff."""
    same_round = (
        not selected.round_number
        or not observed.round_number
        or selected.round_number == observed.round_number
    )
    return (
        selected.game_id == observed.game_id
        and selected.url == observed.url
        and same_round
        and classify_game(observed) in {"UPCOMING", "STARTED"}
    )


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None = None) -> None:
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def text(self) -> str:
        return " ".join(" ".join(child.text().split()) if isinstance(child, _Node) else child for child in self.children).strip()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root, self.stack = _Node("root", {}), []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1] if self.stack else self.root
        node = _Node(tag, {key: value or "" for key, value in attrs}, parent)
        parent.children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        (self.stack[-1] if self.stack else self.root).children.append(data)


def _has_class(node: _Node, class_name: str) -> bool:
    return class_name in node.attrs.get("class", "").split()


def _find(node: _Node, class_name: str) -> list[_Node]:
    found: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            if _has_class(child, class_name):
                found.append(child)
            found.extend(_find(child, class_name))
    return found


def _find_tag(node: _Node, tag_name: str) -> list[_Node]:
    found: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag_name:
                found.append(child)
            found.extend(_find_tag(child, tag_name))
    return found


def _find_all_nodes(node: _Node) -> list[_Node]:
    found: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            found.append(child)
            found.extend(_find_all_nodes(child))
    return found


def _state_metadata(node: _Node | None) -> str:
    """Read state-bearing attributes from a node and two nearby ancestors."""
    values: list[str] = []
    current = node
    for _ in range(8):
        if current is None:
            break
        for name in ("class", "title", "aria-label", "data-state", "data-status", "data-test"):
            value = current.attrs.get(name, "").strip()
            if value:
                values.append(value)
        current = getattr(current, "parent", None)
    return " ".join(values)[:500]


def _row_state_metadata(node: _Node) -> str:
    values: list[str] = []
    for candidate in [node, *_find_all_nodes(node)]:
        for name in ("class", "title", "aria-label", "data-state", "data-status", "data-test"):
            value = candidate.attrs.get(name, "").strip()
            if value:
                values.append(value)
    return " ".join(values)[:1500]


def parse_game_list_html(
    html: str,
    base_url: str,
    player_name_text: str = "Игрок",
    banker_name_text: str = "Банкир",
) -> list[Game]:
    """Extract games only from the Baccarat championship game lists."""
    tree = _Tree(); tree.feed(html)
    games: list[Game] = []
    position = 0
    for section in _find(tree.root, "dashboard-champ-body"):
        for game_list in _find(section, "dashboard-champ-body__games"):
            rows = _find(game_list, "dashboard-game")
            if not rows:
                rows = [node for node in _find_all_nodes(game_list)
                        if _has_class(node, "dashboard-game-block") and _has_class(node, "dashboard-game__block")]
            for node in rows:
                list_position = position
                position += 1
                nodes = _find_all_nodes(node)
                game_container = next((candidate for candidate in [node, *nodes]
                                       if _has_class(candidate, "dashboard-game-block")
                                       and _has_class(candidate, "dashboard-game__block")), node)
                links = _find(game_container, "dashboard-game-block__link") or _find(node, "dashboard-game-block__link")
                if not links or not links[0].attrs.get("href"):
                    continue
                url = urljoin(base_url, links[0].attrs["href"])
                parsed_url = urlparse(url)
                if not re.search(r"/baccarat/|baccara", parsed_url.path.casefold()):
                    continue
                match = re.search(r"/(\d+)(?:-[^/]+)?/?$", parsed_url.path)
                game_id = match.group(1) if match else "url:" + hashlib.sha256(
                    (parsed_url.path + ("?" + parsed_url.query if parsed_url.query else "")).casefold().encode("utf-8")
                ).hexdigest()[:16]
                def field(name: str) -> str:
                    # The live site has shipped both a nested info container and
                    # a sibling layout. Keep the lookup scoped to this game row.
                    values = _find(game_container, name) or _find(node, name)
                    return values[0].text() if values else ""
                def count_field(name: str) -> int:
                    return len(_find(game_container, name) or _find(node, name))
                period, additional, clock = (field("dashboard-game-info__period"),
                                             field("dashboard-game-info__additional-info"),
                                             field("dashboard-game-info__time"))
                round_fields = ((".dashboard-game-info__period", period),
                                (".dashboard-game-info__additional-info", additional))
                round_entry = ((".dashboard-game-info__additional-info", additional)
                               if additional.strip().isdigit() else None)
                round_number = additional.strip() if round_entry else ""
                status_text = " ".join(value for _, value in round_fields if value and not value.strip().isdigit())
                info_text = field("dashboard-game-block__info")
                market_counts = [item.text() for item in _find(game_container, "dashboard-game-more__count")]
                round_number_diagnostic = (
                    f"Номер раздачи: {round_number or 'не найден'}"
                    f"{f' из {round_entry[0]}' if round_entry else ''}; "
                    f".dashboard-game-info__period={count_field('dashboard-game-info__period')}, "
                    f".dashboard-game-info__additional-info={count_field('dashboard-game-info__additional-info')}, "
                    f".dashboard-game-block__info text={info_text!r}; "
                    f".dashboard-game-more__count игнорируется как число рынков ({', '.join(market_counts) or 'узел отсутствует'}); "
                    "ID игры из URL не используется как номер раздачи."
                )
                status = classify_game_status(status_text)
                timer_nodes = _find(game_container, "dashboard-game-info__time") or _find(node, "dashboard-game-info__time")
                timer_node = timer_nodes[0] if timer_nodes else None
                timer_evidence = _state_metadata(timer_node)
                player_score, banker_score = "", ""
                team_containers = _find(game_container, "dashboard-game-block__teams")
                if team_containers:
                    names = [name.text().strip().casefold() for name in _find(team_containers[0], "ui-team-score-name")]
                    total_rows = _find(team_containers[0], "ui-game-scores__item--total")
                    scores = _find(total_rows[0], "ui-game-scores__num") if total_rows else []
                    if len(scores) == 2 and len(names) >= 2:
                        score_by_team = {name: score.text().strip() for name, score in zip(names, scores)}
                        player_score = score_by_team.get(player_name_text.strip().casefold(), "")
                        banker_score = score_by_team.get(banker_name_text.strip().casefold(), "")
                row_state_evidence = _row_state_metadata(game_container)
                state_evidence = f"{status_text} {timer_evidence} {row_state_evidence}".casefold()
                finished = is_finished_status(status_text) or is_finished_status(row_state_evidence) or bool(
                    re.search(r"\b(?:finished|ended)\b", state_evidence)
                )
                started = (_started_evidence(state_evidence)
                           or (_score_value(player_score) is not None and _score_value(player_score) > 0)
                           or (_score_value(banker_score) is not None and _score_value(banker_score) > 0))
                is_countdown = bool(clock.strip()) and any(
                    _explicit_timer_state(state_evidence, marker) for marker in _COUNTDOWN_MARKERS
                )
                games.append(Game(game_id, url, clock, round_number, status, list_position,
                                  player_score=player_score, banker_score=banker_score,
                                  status_text=status_text,
                                  round_number_diagnostic=round_number_diagnostic,
                                  finished=finished, started=started,
                                  is_countdown_to_start=is_countdown,
                                  timer_evidence=timer_evidence,
                                  state_evidence=row_state_evidence))
    return games


def choose_next_game(
    games: list[Game], handled_ids: set[str], inspect_ids: set[str] | None = None,
) -> Game | None:
    """Return the first unhandled, explicitly upcoming row in DOM order."""
    del inspect_ids  # Pending forecasts do not authorize opening a started/unknown row.
    return next((game for game in sorted(games, key=lambda item: item.position)
                 if game.game_id not in handled_ids and classify_game(game) == "UPCOMING"), None)


def suit_from_values(*values: str) -> str | None:
    text = " ".join(values).lower()
    for suit in ("hearts", "diamonds", "clubs", "spades"):
        if re.search(rf"(?:suit-|cardsuits\|){suit}\b", text, re.I):
            return suit
    return None


def valid_card_rank(value: str) -> bool:
    return value.strip().upper() in {"A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"}


def parse_player_cards_snapshot_html(html: str, player_name_text: str = "Игрок") -> dict[str, Any]:
    """Return only revealed Player cards and count empty/unconfirmed slots."""
    tree = _Tree(); tree.feed(html)
    sides = _find(tree.root, "baccarat-player")
    player_name = player_name_text.strip().casefold()
    player = next((side for side in sides
                   if any(name.text().casefold() == player_name
                          for name in _find(side, "baccarat-player__name"))), None)
    if player is None:
        return {"cards": [], "cards_count": 0, "pending_card_slots": 0}
    containers = _find(player, "baccarat-player__cards")
    if not containers:
        return {"cards": [], "cards_count": 0, "pending_card_slots": 0}
    cards: list[dict[str, str]] = []
    pending = 0
    for position, card in enumerate(_find(containers[0], "baccarat-player__card-box"), start=1):
        ranks = _find(card, "baccarat-card__rank")
        rank = ranks[0].text() if ranks else ""
        svg_values = [node.attrs.get("data-v-ico", "") for node in _find_tag(card, "svg")]
        class_values = [node.attrs.get("class", "") for node in _find_all_nodes(card)]
        suit = suit_from_values(card.attrs.get("class", ""), *class_values, *svg_values)
        if valid_card_rank(rank) and suit:
            cards.append({"position": position, "rank": rank.strip().upper(), "suit": suit})
        else:
            pending += 1
    return {"cards": cards, "cards_count": len(cards), "pending_card_slots": pending}


def parse_player_cards_html(html: str, player_name_text: str = "Игрок") -> list[dict[str, str]]:
    """Compatibility wrapper for callers interested in cards only."""
    return parse_player_cards_snapshot_html(html, player_name_text)["cards"]


def classify_player_scan(
    *,
    player_field_present: bool,
    cards_container_present: bool,
    card_boxes_count: int,
    cards_count: int,
    pending_card_slots: int,
    finished: bool,
) -> tuple[str, str]:
    """Classify only observed Player markup; no absent DOM is treated as empty cards."""
    if not player_field_present:
        return "PLAYER_FIELD_NOT_FOUND", "PARTIAL"
    if not cards_container_present:
        return "PLAYER_CARDS_CONTAINER_NOT_FOUND", "PARTIAL"
    if card_boxes_count and pending_card_slots:
        return "CARDS_UNCONFIRMED", "PARTIAL"
    if finished and cards_count:
        return "CARDS_CONFIRMED", "CONFIRMED"
    if cards_count:
        return "CARDS_FOUND", "PARTIAL"
    return "CARDS_NOT_FOUND", "PARTIAL"


def evaluate_prediction(
    predicted_suit: Any,
    actual_cards: list[dict[str, Any]],
    *,
    finished_confirmed: bool,
    final_snapshot_reverified: bool,
    pending_card_slots: int = 0,
    player_hand_final: bool = False,
) -> str:
    """Compare one incoming forecast with a confirmed final Player snapshot."""
    expected = normalize_suit(predicted_suit)
    if not expected:
        return "UNKNOWN"
    if not (finished_confirmed or player_hand_final):
        return "PENDING"
    if (not expected or not final_snapshot_reverified or pending_card_slots
            or len(actual_cards) not in (2, 3)
            or any(not valid_card_rank(str(card.get("rank", ""))) for card in actual_cards)):
        return "UNKNOWN"
    actual = [normalize_suit(card.get("suit")) for card in actual_cards]
    if any(suit is None for suit in actual):
        return "UNKNOWN"
    return "WIN" if expected in actual else "LOSE"


class ObservationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                game_id TEXT PRIMARY KEY, url TEXT NOT NULL, round_number TEXT,
                discovered_at TEXT NOT NULL, finished_at TEXT, cards_json TEXT NOT NULL,
                completeness TEXT NOT NULL, reason TEXT,
                list_position INTEGER NOT NULL DEFAULT 0,
                site_status TEXT NOT NULL DEFAULT 'Не удалось определить',
                player_score TEXT NOT NULL DEFAULT '', banker_score TEXT NOT NULL DEFAULT '',
                scan_result TEXT NOT NULL DEFAULT '', last_scanned_at TEXT NOT NULL DEFAULT '',
                game_time TEXT NOT NULL DEFAULT '', final_snapshot_reverified INTEGER,
                observed_at TEXT NOT NULL DEFAULT '', calculated_suit TEXT,
                calculation_status TEXT NOT NULL DEFAULT 'not_calculated',
                prediction_for_next_round TEXT, prediction_checked INTEGER, prediction_hit INTEGER,
                player_hand_final INTEGER NOT NULL DEFAULT 0)
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
            migrations = {
                "list_position": "INTEGER NOT NULL DEFAULT 0",
                "site_status": "TEXT NOT NULL DEFAULT 'Не удалось определить'",
                "player_score": "TEXT NOT NULL DEFAULT ''",
                "banker_score": "TEXT NOT NULL DEFAULT ''",
                "scan_result": "TEXT NOT NULL DEFAULT ''",
                "last_scanned_at": "TEXT NOT NULL DEFAULT ''",
                "game_time": "TEXT NOT NULL DEFAULT ''",
                "final_snapshot_reverified": "INTEGER",
                "observed_at": "TEXT NOT NULL DEFAULT ''",
                "calculated_suit": "TEXT",
                "calculation_status": "TEXT NOT NULL DEFAULT 'not_calculated'",
                "prediction_for_next_round": "TEXT",
                "prediction_checked": "INTEGER",
                "prediction_hit": "INTEGER",
                "player_hand_final": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE observations ADD COLUMN {name} {declaration}")
            db.execute("UPDATE observations SET round_number=NULL WHERE round_number=''")
            db.execute("""CREATE TABLE IF NOT EXISTS predictions (
                source_game_id TEXT PRIMARY KEY,
                source_round_number TEXT,
                target_game_id TEXT UNIQUE,
                target_round_number TEXT,
                predicted_suit TEXT,
                actual_player_cards TEXT,
                prediction_result TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                checked_at TEXT,
                FOREIGN KEY(source_game_id) REFERENCES observations(game_id),
                FOREIGN KEY(target_game_id) REFERENCES observations(game_id))""")
            target_column = next(row for row in db.execute("PRAGMA table_info(predictions)")
                                 if row[1] == "target_game_id")
            if target_column[3]:
                db.execute("ALTER TABLE predictions RENAME TO predictions_v1")
                db.execute("""CREATE TABLE predictions (
                    source_game_id TEXT PRIMARY KEY,
                    source_round_number TEXT,
                    target_game_id TEXT UNIQUE,
                    target_round_number TEXT,
                    predicted_suit TEXT,
                    actual_player_cards TEXT,
                    prediction_result TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    checked_at TEXT,
                    FOREIGN KEY(source_game_id) REFERENCES observations(game_id),
                    FOREIGN KEY(target_game_id) REFERENCES observations(game_id))""")
                db.execute("""INSERT INTO predictions(source_game_id,source_round_number,target_game_id,
                    target_round_number,predicted_suit,actual_player_cards,prediction_result,reason,created_at,checked_at)
                    SELECT source_game_id,source_round_number,target_game_id,target_round_number,predicted_suit,
                    actual_player_cards,prediction_result,reason,created_at,checked_at FROM predictions_v1""")
                db.execute("DROP TABLE predictions_v1")
                db.execute("DROP INDEX IF EXISTS idx_predictions_target")
            db.execute("CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_game_id)")
            self._backfill_legacy_calculations(db)
            db.execute("CREATE INDEX IF NOT EXISTS idx_observations_observed_at ON observations(observed_at)")

    def clear_all(self) -> None:
        """Delete observation state without touching settings, auth, or other tables."""
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM predictions")
            db.execute("DELETE FROM observations")

    def record_unresolved_transition(self, source_game_id: str, reason: str) -> dict[str, Any] | None:
        """End an active forecast chain without assigning it to a guessed game."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            source = db.execute("""SELECT round_number,calculated_suit,calculation_status,finished_at,player_hand_final
                FROM observations WHERE game_id=?""", (source_game_id,)).fetchone()
            if not source or not (source["finished_at"] or source["player_hand_final"]):
                return None
            existing = db.execute("SELECT prediction_result FROM predictions WHERE source_game_id=?",
                                  (source_game_id,)).fetchone()
            if existing and existing["prediction_result"] in {"WIN", "LOSE"}:
                return None
            db.execute("""INSERT INTO predictions(source_game_id,source_round_number,target_game_id,
                target_round_number,predicted_suit,actual_player_cards,prediction_result,reason,created_at,checked_at)
                VALUES(?,?,NULL,NULL,?,NULL,'UNKNOWN',?,?,?)
                ON CONFLICT(source_game_id) DO UPDATE SET target_game_id=NULL,target_round_number=NULL,
                actual_player_cards=NULL,prediction_result='UNKNOWN',reason=excluded.reason,
                checked_at=excluded.checked_at""",
                (source_game_id, source["round_number"],
                 source["calculated_suit"] if source["calculation_status"] == "complete" else None,
                 reason, now, now))
        return {"source_game_id": source_game_id, "target_game_id": None,
                "source_round_number": source["round_number"], "target_round_number": None,
                "predicted_suit": source["calculated_suit"], "prediction_result": "UNKNOWN",
                "reason": reason}

    def link_active_transition(self, source_game_id: str, target_game: Game) -> dict[str, Any] | None:
        """Link a forecast to the exact next game selected by the running scanner.

        Numeric round adjacency is deliberately irrelevant here. The in-memory
        observer chain, not historical timestamps or list neighbors, establishes
        that this target follows the source.
        """
        now = datetime.now(timezone.utc).isoformat()
        source_round = ""
        predicted = None
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            source = db.execute("""SELECT round_number,calculated_suit,calculation_status,finished_at,player_hand_final
                FROM observations WHERE game_id=?""", (source_game_id,)).fetchone()
            if not source or not (source["finished_at"] or source["player_hand_final"]):
                return None
            source_round = source["round_number"] or ""
            if source["calculation_status"] == "complete":
                predicted = source["calculated_suit"]
            result = "PENDING" if predicted else "UNKNOWN"
            reason = ("Связано по фактической последовательности сканера; номера раздач сверены планировщиком."
                      if predicted else "Игра завершена, но расчётная масть не подтверждена.")
            old_for_source = db.execute("SELECT * FROM predictions WHERE source_game_id=?",
                                        (source_game_id,)).fetchone()
            if old_for_source and old_for_source["prediction_result"] in {"WIN", "LOSE"}:
                return {"source_game_id": source_game_id, "target_game_id": old_for_source["target_game_id"],
                        "source_round_number": old_for_source["source_round_number"],
                        "target_round_number": old_for_source["target_round_number"],
                        "predicted_suit": old_for_source["predicted_suit"],
                        "prediction_result": old_for_source["prediction_result"],
                        "reason": old_for_source["reason"]}
            if (old_for_source and old_for_source["prediction_result"] in {"UNKNOWN", "SKIPPED"}
                    and old_for_source["target_game_id"] is None):
                # A previously recorded chain break is terminal; only a fresh
                # source game may create a new active transition.
                return None

            target_link = db.execute("SELECT * FROM predictions WHERE target_game_id=?",
                                     (target_game.game_id,)).fetchone()
            if target_link and target_link["source_game_id"] != source_game_id:
                if target_link["prediction_result"] in {"WIN", "LOSE"}:
                    return None
                # Old unconfirmed associations came from DOM/round heuristics.
                # Detach them before associating the target selected now.
                db.execute("""UPDATE predictions SET target_game_id=NULL,target_round_number=NULL,
                    actual_player_cards=NULL,prediction_result='UNKNOWN',
                    reason='Старая неподтверждённая связь снята: игра выбрана новой цепочкой сканера.',
                    checked_at=? WHERE source_game_id=?""", (now, target_link["source_game_id"]))

            db.execute("""UPDATE observations SET round_number=COALESCE(NULLIF(?,''),round_number),
                list_position=? WHERE game_id=?""",
                (target_game.round_number or "", target_game.position, target_game.game_id))
            if old_for_source:
                db.execute("""UPDATE predictions SET source_round_number=?,target_game_id=?,
                    target_round_number=?,predicted_suit=?,actual_player_cards=NULL,
                    prediction_result=?,reason=?,created_at=?,checked_at=NULL WHERE source_game_id=?""",
                    (source_round or None, target_game.game_id, target_game.round_number or None,
                     predicted, result, reason, now, source_game_id))
            else:
                db.execute("""INSERT INTO predictions(source_game_id,source_round_number,target_game_id,
                    target_round_number,predicted_suit,actual_player_cards,prediction_result,reason,created_at,checked_at)
                    VALUES(?,?,?,?,?,NULL,?,?,?,NULL)""",
                    (source_game_id, source_round or None, target_game.game_id,
                     target_game.round_number or None, predicted, result, reason, now))

        self.refresh_prediction_target(target_game.game_id)
        settled = self.get(target_game.game_id) or {}
        return {"source_game_id": source_game_id, "target_game_id": target_game.game_id,
                "source_round_number": source_round or None,
                "target_round_number": target_game.round_number or None,
                "predicted_suit": predicted,
                "prediction_result": settled.get("prediction_result", result),
                "reason": settled.get("prediction_reason", reason)}

    def break_incoming_transition(self, target_game_id: str, reason: str) -> bool:
        """Mark an unsettled incoming forecast unknown when its scan chain is lost."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT source_game_id,prediction_result FROM predictions WHERE target_game_id=?",
                             (target_game_id,)).fetchone()
            if not row or row[1] in {"WIN", "LOSE"}:
                return False
            db.execute("""UPDATE predictions SET actual_player_cards=NULL,prediction_result='UNKNOWN',
                reason=?,checked_at=? WHERE source_game_id=?""", (reason, now, row[0]))
        return True

    @staticmethod
    def _backfill_legacy_calculations(db: sqlite3.Connection) -> None:
        """Backfill only timestamps and final suit results supported by existing records."""
        rows = db.execute("""SELECT game_id,finished_at,cards_json,completeness,scan_result,
            observed_at,last_scanned_at,discovered_at,calculated_suit,calculation_status
            FROM observations""").fetchall()
        for row in rows:
            game_id, finished_at, cards_json, completeness, scan_result = row[:5]
            observed_at, last_scanned_at, discovered_at, saved_suit, saved_status = row[5:]
            timestamp = observed_at or last_scanned_at or finished_at or discovered_at
            if not finished_at:
                if not observed_at:
                    db.execute("UPDATE observations SET observed_at=? WHERE game_id=?", (timestamp, game_id))
                continue
            if saved_status == "complete" and saved_suit:
                if not observed_at:
                    db.execute("UPDATE observations SET observed_at=? WHERE game_id=?", (timestamp, game_id))
                continue
            try:
                cards = json.loads(cards_json)
            except (TypeError, json.JSONDecodeError):
                cards = []
            calculated = None
            if completeness == "CONFIRMED" and len(cards) in (2, 3):
                symbol = calculate_cards_suit(cards)
                calculated = SUIT_SYMBOL_TO_NAME.get(symbol) if symbol else None
            status = "complete" if calculated else "incomplete"
            db.execute("""UPDATE observations SET observed_at=?,calculated_suit=?,
                calculation_status=?,prediction_for_next_round=? WHERE game_id=?""",
                (timestamp, calculated, status, calculated, game_id))

    def update_game_list_metadata(self, games: list[Game]) -> None:
        """Backfill newly visible round numbers without touching observed results."""
        updates = [(game.round_number.strip(), game.game_id)
                   for game in games if game.round_number and game.round_number.strip()]
        if not updates:
            return
        with sqlite3.connect(self.path) as db:
            db.executemany(
                "UPDATE observations SET round_number=? WHERE game_id=?",
                updates,
            )

    def upsert(
        self,
        game: Game,
        cards: list[dict[str, Any]],
        completeness: str,
        reason: str = "",
        scan_result: str = "CARDS_FOUND",
        finished_confirmed: bool = False,
        final_snapshot_reverified: bool | None = None,
        pending_card_slots: int = 0,
        player_hand_final: bool = False,
    ) -> bool:
        """Insert/update one stable game record; return False for unchanged data."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT url,round_number,finished_at,cards_json,completeness,reason,
                list_position,site_status,player_score,banker_score,scan_result,game_time,final_snapshot_reverified,
                observed_at,calculated_suit,calculation_status,prediction_for_next_round,prediction_checked,prediction_hit,
                player_hand_final
                FROM observations WHERE game_id=?""", (game.game_id,)).fetchone()
            previous_cards = json.loads(row["cards_json"]) if row else []
            merged_cards = merge_card_snapshots(previous_cards, cards)
            stored_finished = bool(finished_confirmed or (row and row["finished_at"]))
            stored_hand_final = bool(player_hand_final or (row and row["player_hand_final"]))
            saved_reverified = row["final_snapshot_reverified"] if row else None
            reverified = final_snapshot_reverified if final_snapshot_reverified is not None else saved_reverified
            calculated, calculation_status = calculate_final_result(
                merged_cards,
                finished=stored_finished,
                final_snapshot_reverified=reverified is True or reverified == 1,
                pending_card_slots=pending_card_slots,
                player_hand_final=stored_hand_final,
            )
            old_calculation_status = row["calculation_status"] if row else None
            old_calculated = row["calculated_suit"] if row else None
            if old_calculation_status == "complete" and old_calculated and calculation_status != "complete":
                calculated = old_calculated
                calculation_status = "complete"
            prediction = calculated if calculation_status == "complete" else None
            prediction_checked = row["prediction_checked"] if row else None
            prediction_hit = row["prediction_hit"] if row else None
            cards_json = json.dumps(merged_cards, ensure_ascii=False)
            values = (game.url, game.round_number or "", completeness, reason, game.position,
                      game.status, game.player_score, game.banker_score, scan_result,
                      cards_json, game.time, stored_finished, reverified,
                      calculated, calculation_status, prediction, prediction_checked, prediction_hit,
                      stored_hand_final)
            existing_values = ((row["url"], row["round_number"] or "", row["completeness"], row["reason"] or "",
                                row["list_position"], row["site_status"], row["player_score"], row["banker_score"],
                                row["scan_result"], json.dumps(previous_cards, ensure_ascii=False), row["game_time"],
                                bool(row["finished_at"]), saved_reverified, old_calculated, old_calculation_status,
                                row["prediction_for_next_round"], prediction_checked, prediction_hit,
                                bool(row["player_hand_final"])) if row else None)
            if existing_values == values:
                return False
            finished_at = (row["finished_at"] if row else None) or (now if finished_confirmed else None)
            db.execute("""INSERT INTO observations(
                game_id,url,round_number,discovered_at,finished_at,cards_json,completeness,reason,
                list_position,site_status,player_score,banker_score,scan_result,last_scanned_at,game_time,final_snapshot_reverified,
                observed_at,calculated_suit,calculation_status,prediction_for_next_round,prediction_checked,prediction_hit,
                player_hand_final)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET
                url=excluded.url,round_number=COALESCE(NULLIF(excluded.round_number,''),observations.round_number),
                finished_at=COALESCE(observations.finished_at,excluded.finished_at),
                cards_json=excluded.cards_json,completeness=excluded.completeness,reason=excluded.reason,
                list_position=excluded.list_position,site_status=excluded.site_status,
                player_score=excluded.player_score,banker_score=excluded.banker_score,
                scan_result=excluded.scan_result,last_scanned_at=excluded.last_scanned_at,
                game_time=COALESCE(NULLIF(excluded.game_time,''),observations.game_time),
                final_snapshot_reverified=COALESCE(excluded.final_snapshot_reverified,observations.final_snapshot_reverified),
                observed_at=excluded.observed_at,calculated_suit=excluded.calculated_suit,
                calculation_status=excluded.calculation_status,prediction_for_next_round=excluded.prediction_for_next_round,
                prediction_checked=COALESCE(excluded.prediction_checked,observations.prediction_checked),
                prediction_hit=COALESCE(excluded.prediction_hit,observations.prediction_hit),
                player_hand_final=MAX(observations.player_hand_final,excluded.player_hand_final)""",
                (game.game_id, game.url, game.round_number or None, now, finished_at,
                 cards_json, completeness, reason,
                 game.position, game.status, game.player_score, game.banker_score, scan_result, now,
                 game.time, None if reverified is None else int(bool(reverified)), now,
                 calculated, calculation_status, prediction, prediction_checked, prediction_hit,
                 int(stored_hand_final)))
            return True

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        cards = json.loads(row["cards_json"])
        finished = bool(row["finished_at"])
        return {"game_id": row["game_id"], "url": row["url"], "game_url": row["url"],
                "round_number": row["round_number"], "discovered_at": row["discovered_at"],
                "observed_at": row["observed_at"], "finished_at": row["finished_at"],
                "cards": cards, "player_cards": cards, "cards_count": len(cards),
                "completeness": row["completeness"], "reason": row["reason"],
                "finished_confirmed": finished, "game_status": "finished" if finished else "in_progress",
                "player_hand_final": bool(row["player_hand_final"]),
                "position": row["list_position"], "site_status": row["site_status"],
                "player_score": row["player_score"], "banker_score": row["banker_score"],
                "scan_result": row["scan_result"], "last_scanned_at": row["last_scanned_at"],
                "time": row["game_time"],
                "final_snapshot_reverified": None if row["final_snapshot_reverified"] is None else bool(row["final_snapshot_reverified"]),
                "calculated_suit": row["calculated_suit"], "calculation_status": row["calculation_status"],
                "prediction_for_next_round": row["prediction_for_next_round"],
                "outgoing_prediction": row["prediction_for_next_round"],
                "incoming_prediction": None,
                "prediction_checked": None if row["prediction_checked"] is None else bool(row["prediction_checked"]),
                "prediction_hit": None if row["prediction_hit"] is None else bool(row["prediction_hit"])}

    def get(self, game_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT game_id,url,round_number,discovered_at,finished_at,cards_json,
                completeness,reason,list_position,site_status,player_score,banker_score,scan_result,last_scanned_at,game_time,final_snapshot_reverified,
                observed_at,calculated_suit,calculation_status,prediction_for_next_round,prediction_checked,prediction_hit,
                player_hand_final
                FROM observations WHERE game_id=?""", (game_id,)).fetchone()
        if not row:
            return None
        return dict(self._record(row), **self._incoming_predictions().get(game_id, {}),
                    **self._outgoing_predictions().get(game_id, {}))

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT game_id,url,round_number,discovered_at,finished_at,cards_json,
                completeness,reason,list_position,site_status,player_score,banker_score,scan_result,last_scanned_at,game_time,final_snapshot_reverified,
                observed_at,calculated_suit,calculation_status,prediction_for_next_round,prediction_checked,prediction_hit,
                player_hand_final
                FROM observations WHERE cards_json<>'[]'
                    AND (player_hand_final=1 OR finished_at IS NOT NULL)
                ORDER BY COALESCE(NULLIF(observed_at,''),last_scanned_at,discovered_at) DESC,discovered_at DESC LIMIT ?""", (limit,)).fetchall()
        incoming = self._incoming_predictions()
        outgoing = self._outgoing_predictions()
        return [dict(self._record(row), **incoming.get(row["game_id"], {}),
                     **outgoing.get(row["game_id"], {})) for row in rows]

    def finished_game_ids(self) -> set[str]:
        """IDs with a confirmed final Player hand; incomplete finished rows are recoverable."""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                """SELECT game_id FROM observations WHERE player_hand_final=1
                OR (finished_at IS NOT NULL AND final_snapshot_reverified=1
                    AND completeness='CONFIRMED' AND cards_json<>'[]')"""
            ).fetchall()
        return {row[0] for row in rows}

    def recoverable_game_for_round(self, round_number: str, excluded_ids: set[str] | None = None) -> Game | None:
        """Return a previously discovered, still-unconfirmed game with the requested round number."""
        excluded_ids = excluded_ids or set()
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT game_id,url,game_time,round_number,site_status,list_position,
                player_score,banker_score FROM observations
                WHERE round_number=? AND finished_at IS NULL AND player_hand_final=0
                ORDER BY list_position""", (round_number,)).fetchall()
        row = next((item for item in rows if item["game_id"] not in excluded_ids and item["url"]), None)
        if not row:
            return None
        return Game(row["game_id"], row["url"], row["game_time"], row["round_number"],
                    row["site_status"], row["list_position"], player_score=row["player_score"],
                    banker_score=row["banker_score"],
                    round_number_diagnostic="Повторно открыта ранее сохранённая незавершённая игра.")

    def pending_prediction_targets(self) -> set[str]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT target_game_id FROM predictions WHERE prediction_result='PENDING'").fetchall()
        return {row[0] for row in rows}

    def refresh_prediction_target(self, target_game_id: str) -> bool:
        """Update actual cards and settle an incoming prediction exactly once."""
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            prediction = db.execute("SELECT * FROM predictions WHERE target_game_id=?", (target_game_id,)).fetchone()
            target = db.execute("""SELECT cards_json,finished_at,final_snapshot_reverified,player_hand_final
                FROM observations WHERE game_id=?""", (target_game_id,)).fetchone()
            if not prediction or not target:
                return False
            cards = json.loads(target["cards_json"])
            current_result = prediction["prediction_result"]
            # A confirmed result is immutable. Linkage uncertainty is terminal too:
            # never turn it into WIN/LOSE just because more rows later appear.
            if current_result in {"WIN", "LOSE"}:
                return False
            cards_json = json.dumps(cards, ensure_ascii=False)
            if current_result in {"SKIPPED", "UNKNOWN"}:
                db.execute("UPDATE predictions SET actual_player_cards=? WHERE source_game_id=?",
                           (cards_json, prediction["source_game_id"]))
                return True
            finished = bool(target["finished_at"])
            hand_final = bool(target["player_hand_final"])
            outcome = evaluate_prediction(
                prediction["predicted_suit"], cards,
                finished_confirmed=finished,
                final_snapshot_reverified=target["final_snapshot_reverified"] is True
                or target["final_snapshot_reverified"] == 1,
                player_hand_final=hand_final,
            )
            reason = prediction["reason"]
            checked_at = prediction["checked_at"]
            if outcome in {"WIN", "LOSE"}:
                reason = ("Прогноз подтверждён: искомая масть присутствует среди финальных карт Игрока."
                          if outcome == "WIN" else
                          "Прогноз проверен по финальным картам Игрока; искомой масти нет.")
                checked_at = datetime.now(timezone.utc).isoformat()
                db.execute("UPDATE observations SET prediction_checked=1,prediction_hit=? WHERE game_id=?",
                           (int(outcome == "WIN"), prediction["source_game_id"]))
            elif outcome == "UNKNOWN" and (finished or hand_final):
                reason = ("Целевая раздача завершена, но достоверных финальных двух или трёх карт Игрока нет; "
                          "результат не считается LOSE.")
                checked_at = datetime.now(timezone.utc).isoformat()
            db.execute("""UPDATE predictions SET actual_player_cards=?,prediction_result=?,reason=?,checked_at=?
                WHERE source_game_id=? AND prediction_result NOT IN ('WIN','LOSE','SKIPPED')""",
                (cards_json, outcome, reason, checked_at, prediction["source_game_id"]))
            return True

    def _incoming_predictions(self) -> dict[str, dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM predictions").fetchall()
        return {row["target_game_id"]: {
            "source_game_id": row["source_game_id"],
            "source_round_number": row["source_round_number"],
            "target_game_id": row["target_game_id"],
            "target_round_number": row["target_round_number"],
            "predicted_suit": row["predicted_suit"],
            "incoming_prediction": row["predicted_suit"],
            "actual_player_cards": json.loads(row["actual_player_cards"] or "null"),
            "prediction_result": row["prediction_result"],
            "prediction_reason": row["reason"],
        } for row in rows if row["target_game_id"] is not None}

    def incoming_prediction_for(self, target_game_id: str) -> dict[str, Any]:
        """Expose a linked forecast in the Current Observation panel before cards exist."""
        return self._incoming_predictions().get(target_game_id, {})

    def _outgoing_predictions(self) -> dict[str, dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM predictions").fetchall()
        return {row["source_game_id"]: {
            "outgoing_prediction": row["predicted_suit"],
            "outgoing_target_game_id": row["target_game_id"],
            "outgoing_target_round_number": row["target_round_number"],
            "outgoing_prediction_result": row["prediction_result"],
            "outgoing_prediction_reason": row["reason"],
        } for row in rows}

    def statistics(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("""SELECT prediction_result,COALESCE(checked_at,created_at)
                FROM predictions ORDER BY COALESCE(checked_at,created_at),created_at""").fetchall()
        wins = sum(result == "WIN" for result, _ in rows)
        losses = sum(result == "LOSE" for result, _ in rows)
        current_win = current_lose = max_lose = losing = 0
        for result, _ in rows:
            if result == "PENDING":
                continue
            if result == "WIN":
                current_win += 1
                current_lose = 0
                losing = 0
            elif result == "LOSE":
                current_lose += 1
                current_win = 0
                losing += 1
                max_lose = max(max_lose, losing)
            else:
                current_win = current_lose = losing = 0
        checked = wins + losses
        return {
            "checked_count": checked,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins * 100 / checked, 2) if checked else None,
            "current_win_streak": current_win,
            "current_lose_streak": current_lose,
            "max_lose_streak": max_lose,
        }


def merge_card_snapshots(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep known positions through transient DOM gaps and append newly revealed cards."""
    if not current:
        return previous
    by_position: dict[int, dict[str, Any]] = {}
    for index, card in enumerate(previous, start=1):
        by_position[int(card.get("position", index))] = card
    for index, card in enumerate(current, start=1):
        by_position[int(card.get("position", index))] = card
    return [by_position[position] for position in sorted(by_position)]
