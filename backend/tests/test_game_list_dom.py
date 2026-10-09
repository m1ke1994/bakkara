import asyncio

from playwright.async_api import async_playwright

import app.browser as browser_module
from app.browser import BrowserService
from app.observation import choose_next_game, classify_game


def test_atomic_game_list_snapshot_keeps_adjacent_rounds_with_their_game_ids():
    async def run():
        markup = """
          <ul class="dashboard-champ-body__games">
            <li class="dashboard-game dashboard-game-block dashboard-game__block">
              <div class="game-link"><a class="dashboard-game-block__link"
                href="https://example.test/ru/live/baccarat/table/759966370-player-banker"></a></div>
              <div class="dashboard-game-block__info">
                <span class="dashboard-game-info dashboard-game__info" data-test="betting-dashboard-game-info">
                  <span class="ui-caption--color-clr-strong-alt ui-caption--no-wrap ui-caption">
                    <span class="dashboard-game-info__item dashboard-game-info__time">00:14</span>
                    <span class="dashboard-game-info__item dashboard-game-info__additional-info has-tooltip">751</span>
                  </span>
                </span>
              </div>
              <span class="dashboard-game-status">Идёт</span>
            </li>
            <li class="dashboard-game dashboard-game-block dashboard-game__block">
              <div class="game-link"><a class="dashboard-game-block__link"
                href="https://example.test/ru/live/baccarat/table/759966371-player-banker"></a></div>
              <div class="dashboard-game-block__info">
                <span class="dashboard-game-info dashboard-game__info" data-test="betting-dashboard-game-info">
                  <span class="ui-caption--color-clr-strong-alt ui-caption--no-wrap ui-caption">
                    <span class="dashboard-game-info__item dashboard-game-info__time">00:15</span>
                    <span class="dashboard-game-info__item dashboard-game-info__additional-info has-tooltip">752</span>
                  </span>
                </span>
              </div>
              <span class="dashboard-game-status">Завершена</span>
            </li>
          </ul>
        """
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(markup)
            service.page = page
            games = await service._read_game_list_snapshot()
            await browser.close()

        assert [(game.game_id, game.round_number, game.time, game.status, game.position)
                for game in games] == [
            ("759966370", "751", "00:14", "Идёт", 0),
            ("759966371", "752", "00:15", "Завершена", 1),
        ]
        assert all(game.round_row_fragment and len(game.round_row_fragment) <= 3500 for game in games)
        assert "751" in games[0].round_row_fragment
        assert games[0].started is True
        assert games[1].finished is True

    asyncio.run(run())


def test_attached_html_reads_round_784_and_href_id_from_one_block_without_period():
    async def run():
        markup = """
          <base href="https://example.test/">
          <div class="dashboard-game-block dashboard-game__block">
            <span class="dashboard-game-block__row">
              <a href="/ru/live/baccarat/2050671-baccara/759973404-player-banker"
                 class="dashboard-game-block__link"></a>
            </span>
            <div class="dashboard-game-block__info">
              <span class="dashboard-game-info dashboard-game__info"><span class="ui-caption">
                <span class="dashboard-game-info__item dashboard-game-info__time">00:03</span>
                <span class="dashboard-game-info__item dashboard-game-info__additional-info has-tooltip">784</span>
              </span></span>
            </div>
          </div>
        """
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(markup)
            service.page = page
            games = await service._read_game_list_snapshot()
            snapshot = await page.evaluate(browser_module.GAME_LIST_BLOCK_SNAPSHOT_SCRIPT)
            await browser.close()

        assert len(games) == 1
        assert (games[0].position + 1, games[0].game_id, games[0].round_number,
                games[0].time, games[0].status_text) == (1, "759973404", "784", "00:03", "")
        assert snapshot[0]["position"] == 1
        assert snapshot[0]["game_id"] == "759973404"
        assert snapshot[0]["round_number"] == 784
        assert snapshot[0]["timer"] == "00:03"
        assert snapshot[0]["status_text"] == ""
        assert snapshot[0]["href"] == "/ru/live/baccarat/2050671-baccara/759973404-player-banker"
        assert games[0].round_row_fragment.startswith('<div class="dashboard-game-block')

    asyncio.run(run())


def test_nested_game_blocks_do_not_borrow_each_others_round_number():
    async def run():
        markup = """
          <base href="https://example.test/">
          <div class="dashboard-game-block dashboard-game__block">
            <a class="dashboard-game-block__link"
               href="/ru/live/baccarat/table/100-player-banker"></a>
            <div class="dashboard-game-block dashboard-game__block">
              <a class="dashboard-game-block__link"
                 href="/ru/live/baccarat/table/200-player-banker"></a>
              <span class="dashboard-game-info__additional-info">784</span>
              <span class="dashboard-game-info__time">00:03</span>
            </div>
          </div>
        """
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(markup)
            service.page = page
            games = await service._read_game_list_snapshot()
            await browser.close()

        assert [(game.game_id, game.round_number) for game in games] == [
            ("100", ""),
            ("200", "784"),
        ]

    asyncio.run(run())


def test_dom_evidence_selects_first_countdown_zero_zero_row_and_not_timer_only():
    async def run():
        markup = """
          <base href="https://example.test/">
          <ul>
            <li class="dashboard-game-block dashboard-game__block">
              <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/781-player-banker"></a>
              <span class="dashboard-game-info__period">Игра завершена</span>
              <span class="dashboard-game-info__time">00:00</span>
              <span class="dashboard-game-info__additional-info">781</span>
              <div class="dashboard-game-block__teams">
                <span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span>
                <div class="ui-game-scores__item--total"><span class="ui-game-scores__num">6</span><span class="ui-game-scores__num">4</span></div>
              </div>
            </li>
            <li class="dashboard-game-block dashboard-game__block">
              <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/782-player-banker"></a>
              <span class="dashboard-game-info__period">Игра завершена</span>
              <span class="dashboard-game-info__time">00:00</span>
              <span class="dashboard-game-info__additional-info">782</span>
              <div class="dashboard-game-block__teams">
                <span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span>
                <div class="ui-game-scores__item--total"><span class="ui-game-scores__num">8</span><span class="ui-game-scores__num">3</span></div>
              </div>
            </li>
            <li class="dashboard-game-block dashboard-game__block" data-state="countdown">
              <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/783-player-banker"></a>
              <span class="dashboard-game-info__time">00:15</span>
              <span class="dashboard-game-info__additional-info">783</span>
              <div class="dashboard-game-block__teams">
                <span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span>
                <div class="ui-game-scores__item--total"><span class="ui-game-scores__num">0</span><span class="ui-game-scores__num">0</span></div>
              </div>
            </li>
            <li class="dashboard-game-block dashboard-game__block">
              <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/784-player-banker"></a>
              <span class="dashboard-game-info__time">01:15</span>
              <span class="dashboard-game-info__additional-info">784</span>
              <span class="game-state-label">До начала</span>
              <div class="dashboard-game-block__teams">
                <span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span>
                <div class="ui-game-scores__item--total"><span class="ui-game-scores__num">0</span><span class="ui-game-scores__num">0</span></div>
              </div>
            </li>
            <li class="dashboard-game-block dashboard-game__block">
              <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/785-player-banker"></a>
              <span class="dashboard-game-info__time">00:10</span>
              <span class="dashboard-game-info__additional-info">785</span>
              <div class="dashboard-game-block__teams">
                <span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span>
                <div class="ui-game-scores__item--total"><span class="ui-game-scores__num">0</span><span class="ui-game-scores__num">0</span></div>
              </div>
            </li>
          </ul>
        """
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(markup)
            service.page = page
            games = await service._read_game_list_snapshot()
            await browser.close()

        assert [classify_game(game) for game in games] == [
            "FINISHED", "FINISHED", "UPCOMING", "UPCOMING", "UNKNOWN",
        ]
        assert choose_next_game(games, set()).game_id == "783"
        assert [(game.player_score, game.banker_score) for game in games] == [
            ("6", "4"), ("8", "3"), ("0", "0"), ("0", "0"), ("0", "0"),
        ]

    asyncio.run(run())
