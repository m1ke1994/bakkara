# BAKKARA BOT

Independent local control panel for a visible Playwright browser session. It opens the configured Baccarat page and lets a person sign in manually. It reads exposed Player-card DOM data and records virtual suit predictions only; it has no real betting, stake entry, or interaction with betting controls.

## Layout

```text
bakkara/
  backend/app/       FastAPI and Playwright lifecycle
  frontend/          Vue 3 + Vite control panel
  browser_profile/   created locally; separate persistent Chromium profile
  .env               local configuration (not committed)
```

## Start on Windows

1. Copy `.env.example` to `.env` (the supplied `start.bat` does this on first run).
2. Run `start.bat`.
3. Open `http://127.0.0.1:5174` and press **Запустить браузер**.
4. Sign in manually in the visible Chromium window. The backend uses the same balance-currency (`RUB`) selector and fallback selectors as `new_bot` to set `AUTHORIZED`; it does not fill or log credentials.

## Read-only observation

Once the interface says `AUTHORIZED` or `TEMPORARILY_UNCONFIRMED`, press
**Начать наблюдение**. A missing balance marker alone is ambiguous, so the
observer may continue read-only navigation; an explicit login form or failed
auth check stops it. It visits only the configured Baccarat list and selected
game pages and never reads odds or interacts with betting controls. Existing
history is preserved; schema changes are additive.

Games are opened in the list's current DOM order. The game ID comes from the
game URL, separately from a round number. The round number, timer, status, and
game URL are captured in one synchronous `page.evaluate()` pass from each
`.dashboard-game-block.dashboard-game__block`. The technical game ID is parsed
from that block's link URL; the round number is read only from its own
`.dashboard-game-info__additional-info`. Nested blocks cannot borrow another
game's link or number. A missing value is stored as null, never inferred from
the game ID or list position. `.dashboard-game-more__count` is the market-option
count and is never used as a round number. If a number is absent, the backend
logs a structured row and a short fragment of that block only; it never dumps
the full page or authentication data.

Card parsing is scoped to a visible `.baccarat-player` whose
`.baccarat-player__name` is `Игрок`, then reads its `.baccarat-player__cards`
and `.baccarat-player__card-box` nodes. Rank comes from
`.baccarat-card__rank`; suit comes from a `suit-*` class or
`svg[data-v-ico^="cardSuits|"]`. The observer checks the main document and all
frames. It distinguishes a missing Player field, missing card container, an
empty container, and incomplete rank/suit slots. Only exposed card values are
saved. An accessible completed game page sampled during this fix showed the
visible `.market-grid__game-over-panel` but no Player wrapper or card data, so
that page did not yield cards. That panel is not used as the terminal signal:
completion requires exact visible text in
`.ui-caption--size-xl.ui-caption--weight-700` (or the explicitly configured
`ROUND_FINISHED_SELECTOR`).

Game status is read only from visible `.scoreboard-header__match-info`,
`.ui-game-timer__label`, `.scoreboard-status`, or explicit list status fields;
timers and market counts are not status. Incomplete observations are retried,
and later real cards can be merged without erasing previously recorded cards.
The UI's DOM details show the game URL, frame count, selector counts, cause,
and a short relevant DOM fragment for diagnosis.

The planner selects the first explicitly UPCOMING 0:0 game in captured DOM
order and reserves the following UPCOMING rows before leaving the list. After a
Player hand is finalized it opens the exact next reserved game directly, without
reloading the list; a reserved game remains the target if its countdown reaches
STARTED during the handoff. A numeric round gap blocks opening the later row and
returns the observer to the list to wait for the missing round, so the chain
cannot jump from round 1 to round 3. Finished rows without a saved final hand are
never used as a substitute target. Card DOM snapshots are polled
every 100–250 ms (`CARD_POLL_SECONDS`); the old fixed `GAME_PAGE_WAIT_SECONDS`
is no longer applied. Three complete cards in one atomic Player snapshot are
final immediately, without waiting for the rest of the game. Two cards are final
only with a trusted visible finish marker or explicit finished status from the
list, followed by an immediate stable reread. The 90-second diagnostic interval
(`PLAYER_HAND_TIMEOUT_SECONDS`) never finalizes the hand or navigates to another
game. A finish signal without a trusted final Player snapshot also keeps the
observer on that game; it cannot silently advance on missing or partial cards.
The current page remains under observation until the hand is verified, an
explicit stop, or an actual technical/access error. Game-ID-keyed lifecycle
events record discovery, selection, opening, observation, finalization, and the
next-game transition. In-progress games are shown in “Текущее наблюдение” and
do not create empty history rows unless their list round number is known; known
numbers are saved before navigation and remain visible while cards are observed. A
confirmed hand is persisted separately from the overall game-finished timestamp
so it remains available after restart.

Completed, finally reverified two- or three-card hands are transformed by the
ordered 16-entry map in `backend/app/suit_calculator.py`. The rank is stored for
display but does not participate in the calculation. After a hand is completed,
the observer keeps an in-memory chain source and links its forecast to the exact
next scheduled game. A numeric round gap is a hard sequencing guard: a later
round is not opened until the expected round is found, so a forecast is never
silently transferred across a missing game. `WIN`/`LOSE` is checked against the
confirmed final Player hand, not a timer or an arbitrary later row. A target
with a persisted pending forecast is reopened after a process restart so the
exact target can be confirmed.

The history table shows the incoming forecast and its result on the target hand;
the source hand's outgoing calculated suit remains in the persisted observation
model. `WIN` requires the predicted suit on at least one of the final two or three
confirmed Player cards. An incomplete final scan becomes `UNKNOWN`, never
`LOSE`. Results, source/target IDs and round numbers, actual cards, skip reasons,
and summary statistics persist in an additive `predictions` table inside the
existing SQLite database. Repeated list reads do not duplicate transitions, and
confirmed `WIN`/`LOSE` results are not overwritten. The UI shows result colors,
win rate, current win/loss streaks, and maximum loss streak; pending, unknown,
and skipped predictions do not affect the win-rate denominator.

The compact history has five columns: time, round number, Player cards, incoming
forecast, and `WIN`/`LOSE`. “Очистить историю” stops and awaits the observer,
then deletes only observation/prediction rows and resets in-memory scan state;
the browser profile, cookies, settings, suit map, and authorization are kept.
“Очистить логи” clears only the in-memory UI event feed and does not stop the
observer or change history. These actions use `DELETE /api/observations` and
`DELETE /api/logs`, respectively.

The first start downloads Playwright Chromium. Close the two command windows, or use **Остановить браузер**, to stop the app/browser.

## Manual commands

```powershell
cd backend; python -m pip install -r requirements.txt; python -m playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Run backend tests from `backend` with `python -m pytest tests -q`.

In another terminal:

```powershell
cd frontend; npm install; npm run dev
```

Ports are `8010` and `5174`, deliberately separate from the source project. Do not copy the browser profile between projects.
