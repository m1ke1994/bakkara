import asyncio
from collections import deque
import json
from urllib.request import urlopen

import httpx
import pytest
from playwright.async_api import async_playwright

import app.browser as browser_module
import app.main as main_module
from app.browser import BrowserService
from app.observation import Game, ObservationStore


def test_two_to_three_card_dom_update_reaches_history_state_api_and_prediction(tmp_path, monkeypatch):
    async def run():
        service = object.__new__(BrowserService)
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "roundtrip.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "759973404"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service.observation_phase = ""
        service.status = "AUTHORIZED"
        service.authorization = "AUTHORIZED"
        service.diagnostic = "authorized fixture"
        service.game_diagnostics = []
        service.state_version = 0
        service.state_epoch = "roundtrip-test"
        service._active_chain_source_id = None
        service._queued_games = []
        service._probe_observation_auth_locked = _resolved_true

        markup_two = """
          <style>.baccarat-player,.baccarat-player__cards,.baccarat-player__card-box { display:block; }</style>
          <div class="baccarat-player baccarat__player">
            <span class="baccarat-player__name">Игрок</span>
            <ul class="baccarat-player__cards">
              <li class="baccarat-player__card-box"><svg data-v-ico="cardSuits|spades"></svg><span class="baccarat-card__rank">K</span></li>
              <li class="baccarat-player__card-box"><svg data-v-ico="cardSuits|hearts"></svg><span class="baccarat-card__rank">2</span></li>
            </ul>
          </div>
        """
        markup_third = """
          <li class="baccarat-player__card-box"><svg data-v-ico="cardSuits|spades"></svg><span class="baccarat-card__rank">9</span></li>
        """

        class PageProxy:
            def __init__(self, page):
                self.real = page
                self.url = "about:blank"

            @property
            def frames(self):
                return self.real.frames

            def is_closed(self):
                return self.real.is_closed()

            async def goto(self, url, **_kwargs):
                assert url.endswith("/759973404-player-banker")
                assert service.current_observation["round_number"] == "784"
                assert service.store.get("759973404") is None
                assert service.store.history() == []
                self.url = url
                await self.real.set_content(markup_two)

        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(headless=True)
            page = await chromium.new_page()
            service.page = PageProxy(page)
            original_inspect = service._inspect_game_dom
            reads = 0

            async def progressive_inspect(player, banker):
                nonlocal reads
                if reads == 1:
                    await page.locator(".baccarat-player__cards").evaluate(
                        "(container, card) => container.insertAdjacentHTML('beforeend', card)", markup_third
                    )
                snapshot = await original_inspect(player, banker)
                reads += 1
                return snapshot

            service._inspect_game_dom = progressive_inspect
            game = Game(
                "759973404",
                "https://1xlite-12669.pro/ru/live/baccarat/2050671-baccara/759973404-player-banker",
                "00:03", "784", "Идёт", 0,
            )
            processed = set()
            await service._scan_selected_game(game, [], processed, {})
            await chromium.close()

        assert processed == {"759973404"}
        row = service.store.get("759973404")
        assert row["game_id"] == "759973404"
        assert row["round_number"] == "784"
        assert row["player_cards"] == [
            {"position": 1, "rank": "K", "suit": "spades"},
            {"position": 2, "rank": "2", "suit": "hearts"},
            {"position": 3, "rank": "9", "suit": "spades"},
        ]
        assert row["calculated_suit"] == "clubs"
        assert row["outgoing_prediction"] == "clubs"
        assert row.get("incoming_prediction") is None
        assert len(service.store.history()) == 1
        assert service.store.history()[0]["game_id"] == "759973404"
        assert service.store.history()[0]["round_number"] == "784"

        incomplete_list_copy = Game(
            row["game_id"], row["url"], row["time"], "", row["site_status"], row["position"]
        )
        await service._remember_game_scan(
            incomplete_list_copy, row["player_cards"], row["completeness"], row["reason"],
            row["scan_result"], finished_confirmed=row["finished_confirmed"],
            player_hand_final=row["player_hand_final"],
            final_snapshot_reverified=row["final_snapshot_reverified"],
        )
        assert incomplete_list_copy.round_number == "784"
        assert service.current_observation["round_number"] == "784"

        monkeypatch.setattr(main_module, "browser_service", service)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app), base_url="http://test"
        ) as client:
            response = await client.get("/api/state")
        assert response.status_code == 200
        payload = response.json()
        current = payload["observation"]["current"]
        assert payload["state_epoch"] == "roundtrip-test"
        assert payload["state_version"] > 0
        assert current["game_id"] == "759973404"
        assert current["round_number"] == "784"
        assert current["cards"] == row["player_cards"]
        assert current["calculated_suit"] == "clubs"
        assert current["outgoing_prediction"] == "clubs"
        assert len(payload["observation"]["history"]) == 1
        assert payload["observation"]["history"][0]["game_id"] == "759973404"
        assert payload["observation"]["history"][0]["round_number"] == "784"
        assert payload["observation"]["history"][0]["player_cards"] == row["player_cards"]

        try:
            with urlopen("http://127.0.0.1:5174/", timeout=1) as frontend_response:
                assert frontend_response.status == 200
        except Exception:
            pytest.skip("Frontend dev server is not running; API-to-Vue rendering is checked when available.")

        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(headless=True)
            page = await chromium.new_page()

            async def serve_api(route):
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"access-control-allow-origin": "*"},
                    body=json.dumps(payload, ensure_ascii=False),
                )

            await page.route("http://127.0.0.1:8010/api/state", serve_api)
            await page.goto("http://127.0.0.1:5174/", wait_until="domcontentloaded")
            row_cells = page.locator("tbody tr").first.locator("td")
            await row_cells.first.wait_for(timeout=5_000)
            assert await row_cells.nth(1).inner_text() == "784"
            player_cards = row_cells.nth(2).locator(".history-card")
            assert await player_cards.count() == 3
            card_lines = [await card.bounding_box() for card in await player_cards.all()]
            assert max(line["y"] for line in card_lines) - min(line["y"] for line in card_lines) < 1
            assert await row_cells.nth(3).inner_text() == "♣"
            assert await row_cells.nth(4).locator(".prediction-none").count() == 1
            assert await page.locator("tbody tr").count() == 1
            await chromium.close()

    async def _resolved_true():
        return True

    asyncio.run(run())


def test_a_b_c_prediction_chain_reaches_api_and_rendered_history(tmp_path, monkeypatch):
    async def run():
        table = "https://example.test/ru/live/baccarat/table"
        store = ObservationStore(tmp_path / "a-b-c.sqlite3")
        game_a = Game("screen-a", f"{table}/screen-a-player-banker", "00:14", "751", "finished", 0)
        cards_a = [
            {"position": 1, "rank": "10", "suit": "spades"},
            {"position": 2, "rank": "5", "suit": "spades"},
            {"position": 3, "rank": "8", "suit": "clubs"},
        ]
        store.upsert(game_a, cards_a, "CONFIRMED", "final A", "CARDS_CONFIRMED",
                     finished_confirmed=True, final_snapshot_reverified=True, player_hand_final=True)
        assert store.get(game_a.game_id)["calculated_suit"] == "spades"

        game_b = Game("screen-b", f"{table}/screen-b-player-banker", "00:15", "752", "finished", 1)
        store.link_active_transition(game_a.game_id, game_b)
        cards_b = [
            {"position": 1, "rank": "8", "suit": "spades"},
            {"position": 2, "rank": "10", "suit": "clubs"},
        ]
        store.upsert(game_b, cards_b, "CONFIRMED", "final B", "CARDS_CONFIRMED",
                     finished_confirmed=True, final_snapshot_reverified=True, player_hand_final=True)
        store.refresh_prediction_target(game_b.game_id)
        row_b = store.get(game_b.game_id)
        assert row_b["incoming_prediction"] == "spades"
        assert row_b["prediction_result"] == "WIN"
        assert row_b["outgoing_prediction"] == "diamonds"

        game_c = Game("screen-c", f"{table}/screen-c-player-banker", "00:16", "753", "active", 2)
        store.link_active_transition(game_b.game_id, game_c)

        service = object.__new__(BrowserService)
        service.store = store
        service.status = "AUTHORIZED"
        service.authorization = "AUTHORIZED"
        service.diagnostic = "fixture"
        service.events = deque(maxlen=100)
        service.game_diagnostics = []
        service.observation_status = "WAITING_FOR_CARDS"
        service.observation_diagnostic = "fixture"
        service.current_observation = {
            "game_id": game_c.game_id, "url": game_c.url, "round_number": game_c.round_number,
            "cards": [], "cards_count": 0, "lifecycle_state": "SELECTED",
        }
        service.state_version = 1
        service.state_epoch = "a-b-c-test"
        monkeypatch.setattr(main_module, "browser_service", service)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app), base_url="http://test"
        ) as client:
            response = await client.get("/api/state")
        assert response.status_code == 200
        payload = response.json()
        current = payload["observation"]["current"]
        assert current["game_id"] == "screen-c"
        assert current["incoming_prediction"] == "diamonds"
        assert current["prediction_result"] == "PENDING"
        history_b = next(item for item in payload["observation"]["history"] if item["game_id"] == "screen-b")
        assert history_b["player_cards"] == cards_b
        assert history_b["outgoing_prediction"] == "diamonds"
        assert history_b["incoming_prediction"] == "spades"
        assert history_b["prediction_result"] == "WIN"
        assert len(payload["observation"]["history"]) == 2

        try:
            with urlopen("http://127.0.0.1:5174/", timeout=1) as frontend_response:
                assert frontend_response.status == 200
        except Exception:
            pytest.skip("Frontend dev server is not running; API chain assertions still passed.")

        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(headless=True)
            page = await chromium.new_page()

            async def serve_api(route):
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"access-control-allow-origin": "*"},
                    body=json.dumps(payload, ensure_ascii=False),
                )

            await page.route("http://127.0.0.1:8010/api/state", serve_api)
            await page.goto("http://127.0.0.1:5174/", wait_until="domcontentloaded")
            b_row = page.locator("tbody tr").filter(has=page.locator('a[href*="screen-b"]'))
            await b_row.wait_for(timeout=5_000)
            cards = b_row.locator(".history-card")
            assert await cards.count() == 2
            assert (await cards.nth(0).inner_text()).startswith("8")
            assert (await cards.nth(1).inner_text()).startswith("10")
            cells = b_row.locator("td")
            assert await cells.nth(3).locator(".calculated-suit").count() == 1
            assert await cells.nth(4).locator(".prediction-win").inner_text() == "WIN"
            assert await page.locator("tbody tr").count() == 2
            await chromium.close()

    asyncio.run(run())
