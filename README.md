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

The planner queues every visible game in captured DOM order and reuses that
queue across direct game URLs. Finished rows without a saved final hand are
opened for recovery rather than silently skipped. Card DOM snapshots are polled
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
next scheduled game. A numeric round gap first triggers a lookup in the visible
queue and stored incomplete games; if the missing round cannot be recovered,
the old chain is marked UNKNOWN before a later round is opened. The next
completed hand then starts a fresh chain. `WIN`/`LOSE` is checked against the
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

## Непрерывный обход при UNKNOWN и отсутствии карт

Наблюдатель **не останавливается** только потому, что в списке для игры не
отобразился таймер, счёт или статус. Сначала перечитываются реальные DOM-строки.
Для каждого доступного URL он пробует считывать карты Игрока независимо от
метки `UPCOMING`, `STARTED`, `FINISHED` или `UNKNOWN`. Если номер пока не
виден, запись сохраняется без выдуманного номера — в подготовку цикла 18 она
не засчитывается до определения настоящей последовательности.

Одна недоступная игровая страница не может бесконечно блокировать переходы.
После `SCAN_INCOMPLETE_TIMEOUT_SECONDS` (по умолчанию 45 секунд, для
завершённой игры — `FINISHED_RECOVERY_TIMEOUT_SECONDS`, 12 секунд) бот
оставляет раздачу неполной, планирует повторное чтение и продолжает обходить
следующие игры. Завершённую игру без доступных карт не перечитывает каждую
секунду, чтобы не задерживать новые. Повторные попытки не добавляют
дубликатов; счётчик прогресса увеличивают только финальные подтверждённые
руки Игрока. Если на сайте карты отсутствуют окончательно, восстановить их
из DOM невозможно — отсутствие будет отражено в диагностике, без ложного
WIN/LOSE.

## Отдельный режим «Цикл 18»: первые 20 раздач — подготовка

Наблюдение остаётся **только чтением**. Бот не открывает купон ставок, не вводит суммы и не нажимает кнопки букмекерской конторы.

1. После кнопки **Начать сканирование** начинается новая сессия: счётчик цикла 18 сбрасывается до **0/20**. Старые сохранённые игры не засчитываются повторно.
2. Подготовка учитывает **20 новых уникальных раздач с окончательными 2–3 картами Игрока** в текущей сессии. Если отдельная игра не удалось прочитать, уже собранные подтверждённые раздачи **не сбрасываются**. Повторное чтение того же `game_id` счётчик не увеличивает.
3. Игры без подтверждённых карт остаются в истории на своих физических номерах. Для прогноза на №N требуется ровно №N−18, а не восемнадцатая сохранённая строка. Если карты физического источника N−18 отсутствуют, прогноз для №N не создаётся; соседняя раздача не подставляется.
4. После 20-й **подтверждённой** игры для ожидаемой №N берётся именно раздача **N−18** (а не 18-я предыдущая строка файлов). Применяется существующая таблица из 16 преобразований; для трёх карт результат первых двух преобразуется вместе с третьей.
5. Прогноз на 21-ю раздачу (с исходной 3-й) появляется **до её результата**. Как только её финальные карты подтверждены, записывается **WIN**, если вычисленная масть присутствует хотя бы в одной карте Игрока, иначе **LOSE**. Неполный снимок остаётся `UNKNOWN`, а не `LOSE`.
6. Все прогнозы режима хранятся в отдельной таблице SQLite и показываются отдельным блоком интерфейса: прогресс 0/20, рассчитанная масть, исходная/целевая раздачи, WIN/LOSE, точность, максимальная серия LOSE и виртуальные шаги. Существующая логика наблюдателя и старая статистика следующей игры не удаляются.
7. При **Остановить сканирование** режим прекращает прогнозировать. Новый **Начать сканирование** всегда заново требует 20 подтверждённых раздач. **Очистить историю** очищает и данные цикла 18; **Очистить логи** историю не удаляет.

Отладочный API: `GET /api/cycle18`. Показатели на прошлых раздачах не являются гарантией прогнозирования или безопасной глубины догона.

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


## Выбор сохранённого фильтра прогнозирования

В панели доступны два фильтра: **N−18** (20 подтверждённых рук
перед сигналами) и **Голосование N−3/10/24/31/40** (40 рук).
Выберите фильтр и нажмите **«Сохранить стратегию»**, затем откройте
браузер и запустите сканирование. Выбор хранится в SQLite
`bot_strategy_settings` и переживает перезапуск. Каждый новый сеанс
сканирования запоминает выбранный фильтр; во время сеанса менять
его нельзя. Остановите сканирование, сохраните другой фильтр и
запустите новый сеанс для сброса виртуальной серии.

Для голосования каждый источник N−k должен быть подтверждённой
физической раздачей. Две или три карты источника преобразуются по
**исходной неизменённой таблице** `suit_calculator.py`.
Побеждает масть с наибольшим числом голосов; при равенстве
фиксирован приоритет ♥ → ♦ → ♣ → ♠. Отсутствие даже одного
источника подавляет сигнал; отсутствующие игры не подменяются
соседними. История карт и WIN/LOSE относится только к выбранному
фильтру текущего сеанса. Приложение наблюдает и считает виртуальные
результаты, **не размещает ставки**.

API: `GET /api/strategy`, `PUT /api/strategy` с JSON
`{"strategy":"n18"}` либо `{"strategy":"vote_3_10_24_31_40"}`.
Во время сканирования изменение возвращает HTTP 409.


## Расположение прогноза и история результатов

В интерфейсе блок **«Текущая и следующая раздачи»** расположен непосредственно
перед таблицей **«История раздач»**. Калькулятор догона и финансовые суммы
удалены из панели; сохранены просмотр подтверждённых виртуальных
WIN/LOSE, номер шага и источник каждой прогнозируемой масти.
