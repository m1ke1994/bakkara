"""Browser level coupon regression using the bookmaker's regular DOM shape."""
import asyncio
from collections import deque
from decimal import Decimal
from urllib.parse import quote

import pytest
from playwright.async_api import async_playwright

from app.live_bets import LiveBets
from app.live_coupon_test import place_regular_coupon
from app.live_executor import PlacementError, place_one_click
from app.live_market_probe import INIT_SCRIPT, SNAPSHOT
from app.browser import BrowserService
from app.cycle18 import Cycle18Engine
from app.observation import Game, ObservationStore


HTML = r"""<!doctype html><html lang="ru"><meta charset="utf-8"><body>
<button class="ui-search-default__button" onclick="openSearch()">Поиск</button>
<input class="ui-search-default__input" placeholder="Поиск по рынкам" style="display:none">
<canvas class="market-grid-canvas__canvas" width="908" height="850"></canvas>
<canvas class="market-grid-canvas__offscreen-canvas" width="908" height="850" style="display:none"></canvas>
<div class="coupon-one-click"><input class="selection__input" type="checkbox" checked></div>
<div class="coupon-app"><div class="coupon-bets"></div><div class="coupon-amount"></div>
  <div class="coupon-buttons"></div></div>
<span data-gtm="account-balance-value-desktop">1 936,29 RUB</span>
<script>
window.trace=[];window.submissions=0;window.oddsClicks=0;window.testMode='success';
const suits=['Пики','Трефы','Бубны','Червы'];
const visible=document.querySelector('.market-grid-canvas__canvas');
const offscreen=document.querySelector('.market-grid-canvas__offscreen-canvas');
const input=document.querySelector('input.ui-search-default__input');
document.querySelector('.coupon-one-click input').onchange=e=>{
  if(!e.target.checked)trace.push('toggle_off');
};
function openSearch(){input.style.display='block';trace.push('search_open');}
function draw(){
  const oc=offscreen.getContext('2d'),vc=visible.getContext('2d');
  oc.clearRect(0,0,908,850);vc.clearRect(0,0,908,850);
  if(input.value!=='Игрок получит карту')return;
  oc.font='14px Arial';oc.fillText('Игрок получит карту (масть)',24,35);
  suits.forEach((suit,i)=>{
    const y=70+i*35;
    oc.textAlign='left';oc.fillText(suit,27,y);
    oc.textAlign='right';oc.fillText('1.9',880,y);
  });
  vc.drawImage(offscreen,0,0);
}
input.addEventListener('input',draw);
visible.addEventListener('click',e=>{
  const i=Math.round((e.offsetY-65)/35);
  if(e.offsetX<810||i<0||i>3)return;
  window.oddsClicks++;trace.push('odds:'+suits[i]);
  if(document.querySelector('.coupon-one-click input').checked){
    window.submissions++;trace.push('EARLY_ONE_CLICK_SUBMISSION');return;
  }
  document.querySelector('.coupon-bets').innerHTML='<div class="coupon-bets__bet">'
    +'Игрок получит карту (масть): '+suits[i]+' 1.9</div>';
  document.querySelector('.coupon-amount').innerHTML=
    '<input class="ui-number-input__field" value="99" inputmode="decimal">';
  document.querySelector('.coupon-buttons').innerHTML=
    '<button onclick="submitBet()">Сделать ставку</button>';
  if(window.testMode==='closed')
    document.querySelector('.coupon-bets__bet').insertAdjacentHTML(
      'beforeend','<span class="coupon-bet-lock__text">Рынок закрыт</span>');
});
visible.addEventListener('mousemove',e=>{
  visible.style.cursor=e.offsetX>=810?'pointer':'default';
});
function submitBet(){
  window.submissions++;trace.push('submit');
  if(window.testMode==='unknown')return;
  if(window.testMode==='rejected'){
    document.body.insertAdjacentHTML('beforeend','<div class="modal-container">'
      +'<div class="modal__content" data-test="modal-content">Ставка отклонена'
      +'<button data-test="betting-coupon-modal-control-contune"'
      +' onclick="this.closest(\'.modal-container\').remove();trace.push(\'modal_closed\')">Продолжить</button>'
      +'</div></div>');return;
  }
  document.querySelector('[data-gtm="account-balance-value-desktop"]').textContent='1 926,29 RUB';
  document.body.insertAdjacentHTML('beforeend','<div class="modal-container">'
    +'<div class="modal__content" data-test="modal-content">'
    +'<div data-test="betting-coupon-success-modal-title">Ваша ставка принята!</div>'
    +'<button data-test="betting-coupon-modal-control-contune"'
    +' onclick="this.closest(\'.modal-container\').remove();trace.push(\'modal_closed\')">Продолжить</button>'
    +'</div></div>');
}
</script></body></html>"""


async def _with_page(callback):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            await context.add_init_script(script=INIT_SCRIPT)
            page = await context.new_page()
            await page.goto("data:text/html," + quote(HTML), wait_until="domcontentloaded")
            return await callback(page)
        finally:
            await browser.close()


@pytest.mark.parametrize("submit", [place_regular_coupon, place_one_click])
def test_regular_coupon_full_browser_flow_and_compatibility(submit):
    async def run(page):
        marked, reports = [], []
        status = await submit(page, "diamonds", Decimal("10"),
                              lambda: marked.append("SUBMITTING")) if submit is place_one_click else (
            await submit(page, "diamonds", Decimal("10"),
                         lambda: marked.append("SUBMITTING"), reports.append))
        trace = await page.evaluate("window.trace")
        assert status == "ACCEPTED"
        assert marked == ["SUBMITTING"]
        assert trace.index("toggle_off") < trace.index("odds:Бубны") < trace.index("submit")
        assert trace[-1] == "modal_closed"
        assert await page.evaluate("window.oddsClicks") == 1
        assert await page.evaluate("window.submissions") == 1
        assert await page.locator(".coupon-amount input").input_value() == "10"
        assert await page.locator(".modal-container").count() == 0
        if reports:
            assert any("координаты" in event for event in reports)
            assert any("слой=offscreen" in event for event in reports)

    asyncio.run(_with_page(run))


def test_one_accepted_test_stake_then_next_coupon_ready_without_submission(tmp_path):
    async def run(page):
        path = tmp_path / "single-run.sqlite3"
        bets = LiveBets(path)
        bets.begin_single_stake_test()
        assert bets.reserve("first", "601", "diamonds", Decimal("10"))
        status = await place_regular_coupon(
            page, "diamonds", Decimal("10"),
            lambda: bets.update("first", "SUBMITTING"))
        bets.update("first", status)
        assert status == "ACCEPTED"
        assert await page.evaluate("window.submissions") == 1
        assert bets.settle("first", "WIN", 2)
        assert not LiveBets(path).reserve("second", "602", "hearts", Decimal("10"))
        await page.goto("data:text/html," + quote(HTML), wait_until="domcontentloaded")
        ready = await place_regular_coupon(
            page, "hearts", Decimal("10"),
            lambda: pytest.fail("Second bet must not be submitted"), submit=False)
        assert ready == "READY"
        assert await page.evaluate("window.submissions") == 0
        assert await page.locator(".coupon-amount input").input_value() == "10"
    asyncio.run(_with_page(run))


def test_market_closes_after_selection_before_confirmation():
    async def run(page):
        await page.evaluate("window.testMode='closed'")
        with pytest.raises(PlacementError, match="заблокировал"):
            await place_regular_coupon(page, "diamonds", Decimal("10"),
                                       lambda: pytest.fail("Closed market submitted"))
        assert await page.evaluate("window.submissions") == 0
    asyncio.run(_with_page(run))


@pytest.mark.parametrize("outcome", ["WIN", "LOSE"])
def test_observer_prediction_coupon_result_and_next_ready_on_local_bookmaker(tmp_path, outcome):
    async def run(page):
        async def bookmaker_page(route):
            await route.fulfill(body=HTML, content_type="text/html")
        await page.route("https://example.test/**", bookmaker_page)
        path = tmp_path / "cycle.sqlite3"
        store, engine, bets = ObservationStore(path), Cycle18Engine(path), LiveBets(path)
        engine.start_session()
        bets.mode("LIVE")
        bets.begin_single_stake_test()
        source_cards = [{"position": 1, "rank": "A", "suit": "hearts"},
                        {"position": 2, "rank": "7", "suit": "hearts"}]
        for number in range(601, 620):
            source = Game(f"r{number}", f"https://example.test/{number}", "", str(number), "Завершена", 0)
            store.upsert(source, source_cards, "CONFIRMED", "стенд", "CARDS_CONFIRMED",
                         player_hand_final=True, final_snapshot_reverified=True)
        rounds = [Game(f"r{number}", f"https://example.test/{number}", "00:15",
                       str(number), "Ожидается", number-620,
                       player_score="0", banker_score="0", is_countdown_to_start=True)
                  for number in (620, 621, 622)]
        store.register_discovered_games(rounds)
        service = object.__new__(BrowserService)
        service.page, service.page_lock = page, asyncio.Lock()
        service.store, service.cycle_engine, service.live_bets = store, engine, bets
        service.live_plan_store = type("Plan", (), {"load": lambda _self: type("Row", (), {"row": [Decimal("10")]})()})()
        service.events = deque(maxlen=200)
        service.current_observation = None
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards_for_round = {620: source_cards}
        async def inspect(*_args):
            number = int(page.url.rsplit("/", 1)[-1])
            cards = cards_for_round[number]
            return {"cards": cards, "banker_initial_ranks": [],
                    "counts": {"player_fields": 1, "player_cards_containers": 1,
                               "player_card_boxes": len(cards)},
                    "pending_card_slots": 0, "finished": False,
                    "status_text": "", "cause": "локальный стенд"}
        service._inspect_game_dom = inspect
        processed = set()
        await service._scan_selected_game(rounds[0], [], processed, {})
        assert processed == {"r620"}
        forecast = engine.refresh({"game_id": "r621", "round_number": "621"})
        preview = next(p for p in forecast["live_preview"] if p["phase"] == "CURRENT")
        assert preview["available"]
        suit = preview["predicted_suit"]
        actual_suit = suit if outcome == "WIN" else next(
            item for item in ("hearts", "diamonds", "clubs", "spades") if item != suit)
        cards_for_round[621] = [{"position": 1, "rank": "A", "suit": actual_suit},
                                {"position": 2, "rank": "8", "suit": actual_suit}]
        service.current_observation = {"game_id": "r621", "round_number": "621"}
        await service._scan_selected_game(rounds[1], [], processed, {})
        await service._pending_live_settlement
        assert bets.get_attempt("r621")["status"] == "ACCEPTED"
        assert bets.get_attempt("r621")["result"] == outcome
        assert await page.evaluate("window.submissions") == 1
        assert not LiveBets(path).reserve("r622", "622", suit, Decimal("10"))
        next_preview = engine.refresh({"game_id": "r622", "round_number": "622"})
        next_suit = next(p["predicted_suit"] for p in next_preview["live_preview"]
                         if p["phase"] == "CURRENT")
        await page.goto("https://example.test/622")
        ready = await place_regular_coupon(page, next_suit, Decimal("10"),
                                           lambda: pytest.fail("Second submission"), submit=False)
        assert ready == "READY"
        assert await page.evaluate("window.submissions") == 0
    asyncio.run(_with_page(run))


def test_existing_selection_blocks_canvas_click_and_submission():
    async def run(page):
        await page.evaluate("""() => {
          document.querySelector('.coupon-bets').innerHTML=
            '<div class="coupon-bets__bet">Другой исход 2.1</div>';
        }""")
        marked = []
        with pytest.raises(PlacementError, match="уже есть исход"):
            await place_regular_coupon(page, "clubs", Decimal("10"), lambda: marked.append(True))
        assert await page.evaluate("window.oddsClicks") == 0
        assert await page.evaluate("window.submissions") == 0
        assert not marked

    asyncio.run(_with_page(run))


def test_locked_previous_outcome_is_removed_before_next_selection():
    async def run(page):
        await page.evaluate("""() => {
          const card=document.createElement('div');card.className='coupon-bets__bet';
          const lock=document.createElement('div');lock.className='coupon-bet-lock__text';
          lock.textContent='Заблокированное событие';card.append(lock);
          const remove=document.createElement('button');
          remove.className='coupon-bet-lock-remove';remove.setAttribute('aria-label','Удалить');
          remove.textContent='Удалить';remove.onclick=()=>{card.remove();trace.push('stale_removed')};
          card.append(remove);document.querySelector('.coupon-bets').append(card);
        }""")
        marked = []
        status = await place_regular_coupon(page, "diamonds", Decimal("10"),
                                            lambda: marked.append(True))
        trace = await page.evaluate("window.trace")
        assert status == "ACCEPTED"
        assert trace.index("stale_removed") < trace.index("odds:Бубны")
        assert marked == [True]
        assert await page.evaluate("window.submissions") == 1

    asyncio.run(_with_page(run))


def test_cleared_canvas_does_not_reuse_previous_odds():
    async def run(page):
        await page.locator("button.ui-search-default__button").click()
        await page.locator('input[placeholder="Поиск по рынкам"]').fill("Игрок получит карту")
        assert (await page.evaluate(SNAPSHOT))["status"] == "ODDS_MAPPED"
        await page.evaluate("""() => {
          for (const canvas of document.querySelectorAll('canvas'))
            canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
        }""")
        assert (await page.evaluate(SNAPSHOT))["oddsBySuit"] == {}

    asyncio.run(_with_page(run))


def test_unknown_result_never_repeats_confirm_click():
    async def run(page):
        await page.evaluate("window.testMode='unknown'")
        marked = []
        status = await place_regular_coupon(page, "hearts", Decimal("10"),
                                            lambda: marked.append(True))
        assert status == "UNKNOWN"
        assert marked == [True]
        assert await page.evaluate("window.submissions") == 1

    asyncio.run(_with_page(run))


def test_rejected_modal_closes_without_advancing_ladder(tmp_path):
    async def run(page):
        await page.evaluate("window.testMode='rejected'")
        status = await place_regular_coupon(page, "spades", Decimal("10"), lambda: None)
        assert status == "REJECTED"
        assert await page.evaluate("window.submissions") == 1
        assert await page.locator(".modal-container").count() == 0

    asyncio.run(_with_page(run))
    bets = LiveBets(tmp_path / "bets.sqlite3")
    assert bets.reserve("game", "1", "spades", Decimal("10"))
    bets.update("game", "REJECTED")
    assert not bets.settle("game", "LOSE", 3)
    assert bets.snapshot()["step"] == 1


def test_coupon_test_filter_migrates_existing_database(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE live_control (id INTEGER PRIMARY KEY, mode TEXT, step INTEGER)")
        db.execute("INSERT INTO live_control VALUES (1,'LIVE',3)")
    store = LiveBets(path)
    assert store.snapshot()["coupon_test_filter"] == 0
    store.set_coupon_test_filter(True)
    assert LiveBets(path).snapshot()["coupon_test_filter"] == 1
    assert store.snapshot()["step"] == 3
