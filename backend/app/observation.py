"""Read-only Baccarat observation helpers.

The functions in this module intentionally work on DOM data only.  They do
not inspect odds, create coupons, or interact with any betting controls.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


@dataclass
class Game:
    game_id: str
    url: str
    time: str
    round_number: str
    status: str
    position: int
    active: bool = False


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag, self.attrs, self.children = tag, attrs, []

    def text(self) -> str:
        return " ".join(" ".join(child.text().split()) if isinstance(child, _Node) else child for child in self.children).strip()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root, self.stack = _Node("root", {}), []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        (self.stack[-1] if self.stack else self.root).children.append(node)
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


def parse_game_list_html(html: str, base_url: str) -> list[Game]:
    """Extract Baccarat game metadata from the supplied list-page HTML."""
    tree = _Tree(); tree.feed(html)
    games: list[Game] = []
    for position, node in enumerate(_find(tree.root, "dashboard-game")):
        links = _find(node, "dashboard-game-block__link")
        if not links or not links[0].attrs.get("href"):
            continue
        url = urljoin(base_url, links[0].attrs["href"])
        match = re.search(r"/(\d+)(?:-[^/?#]+)?(?:[?#].*)?$", urlparse(url).path)
        if not match:
            continue
        def field(name: str) -> str:
            values = _find(node, name)
            return values[0].text() if values else ""
        additional, clock = field("dashboard-game-info__additional-info"), field("dashboard-game-info__time")
        games.append(Game(match.group(1), url, clock, field("dashboard-game-info__period"), additional or clock, position))
    return games


def choose_next_game(games: list[Game], handled_ids: set[str]) -> Game | None:
    """Keep DOM order; do not invent semantics for ambiguous timer values."""
    for game in games:
        if game.game_id in handled_ids or not game.active or "заверш" in game.status.lower():
            continue
        return game
    return None


def suit_from_values(*values: str) -> str | None:
    text = " ".join(values).lower()
    for suit in ("hearts", "diamonds", "clubs", "spades"):
        if re.search(rf"(?:suit-|cardsuits\|){suit}\b", text, re.I):
            return suit
    return None


def parse_player_cards_html(html: str) -> list[dict[str, str]]:
    tree = _Tree(); tree.feed(html)
    players = _find(tree.root, "baccarat-player")
    if not players:
        return []
    cards: list[dict[str, str]] = []
    for card in _find(players[0], "baccarat-player__card-box"):
        ranks = _find(card, "baccarat-card__rank")
        rank = ranks[0].text() if ranks else ""
        svg_values = [node.attrs.get("data-v-ico", "") for node in _find_tag(card, "svg")]
        suit = suit_from_values(card.attrs.get("class", ""), *svg_values, card.text())
        if rank and suit:
            cards.append({"rank": rank, "suit": suit})
    return cards


class ObservationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                game_id TEXT PRIMARY KEY, url TEXT NOT NULL, round_number TEXT,
                discovered_at TEXT NOT NULL, finished_at TEXT, cards_json TEXT NOT NULL,
                completeness TEXT NOT NULL, reason TEXT)
            """)

    def upsert(self, game: Game, cards: list[dict[str, str]], completeness: str, reason: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("""INSERT INTO observations(game_id,url,round_number,discovered_at,finished_at,cards_json,completeness,reason)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET
                finished_at=excluded.finished_at,cards_json=excluded.cards_json,completeness=excluded.completeness,reason=excluded.reason""",
                (game.game_id, game.url, game.round_number, now, now if completeness == "CONFIRMED" else None,
                 json.dumps(cards), completeness, reason))

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT game_id,url,round_number,discovered_at,finished_at,cards_json,completeness,reason FROM observations ORDER BY discovered_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"game_id": row[0], "url": row[1], "round_number": row[2], "discovered_at": row[3], "finished_at": row[4], "cards": json.loads(row[5]), "cards_count": len(json.loads(row[5])), "completeness": row[6], "reason": row[7]} for row in rows]
