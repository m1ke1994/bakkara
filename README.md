# BAKKARA BOT

Local control panel for a visible Playwright browser session. It opens the configured Baccarat page and lets a person sign in manually. DEMO records virtual suit predictions. LIVE uses the same observer and saved calculator amounts to attempt bets through the visible bookmaker interface.

## Отдельная стратегия «53%» (`facecards_53`)

В разделе **«Стратегия / фильтр»** добавлена самостоятельная стратегия **«53%»**. Это название исторической гипотезы, не гарантия вероятности и не безопасный догон. Прежние режимы и их сохранённые настройки остаются без изменений.

- Используется только предыдущая финально подтверждённая рука Игрока N−1 с **ровно одной** картой A/K/Q/J; её масть назначается по неизменяемой таблице `backend/app/strategy_53.py`.
- При **трёх картах** и нахождении подходящей карты **второй** зеркалится именно назначенная масть: ♥↔♠, ♦↔♣.
- **После любой ничьей (X)** четыре следующие физические раздачи пропускаются. Если исход одной из четырёх предыдущих не подтверждён, прогноз не выдаётся.
- **Прогрев 40 подряд завершённых уникальных раздач**, интервалы между финальными картами не более 185 секунд. При разрыве истории нужен новый непрерывный участок. Неполные карты не подставляются.
- Пропуски не меняют шаг догона; WIN означает появление назначенной масти хотя бы на одной карте Игрока. Ставки и девятишаговые суммы управляются отдельно.
- В архиве 3 982 раздач было 460 WIN из 868 сигналов (53,00%) и максимум пять наблюдаемых LOSE. Это лишь исследовательская статистика и не гарантирует шестой шаг в будущем.

## LIVE status and local update

Choose **LIVE** before starting observation. The mode can change only while scanning is stopped. LIVE tries only a numbered upcoming game with an available prediction. It saves the game ID, suit, step and amount in SQLite before touching the outcome. A submission with an unknown acceptance status blocks further LIVE submissions, including after a backend restart. The stake step changes only when an accepted attempt has a confirmed final WIN or LOSE. The bookmaker's RUB interface is used as configured in the existing browser session; the bot does not switch wallets.

The Canvas text capture and bookmaker acceptance signals depend on the current site layout. If the exact market, four outcome labels, coordinates, coupon amount or acceptance cannot be confirmed, inspect the LIVE status and bookmaker interface before using it again. The automated tests use an imitation and do not verify a live bookmaker session.

To update an existing Windows checkout, stop `start.bat`, run `git pull` in the project directory, then run `start.bat` again. Keep the local `.env`, `browser_profile` and SQLite database. For manual checks run `cd backend; python -m pytest tests -q` and `cd frontend; npm ci; npm test; npm run build` in separate PowerShell sessions.

## Strategy: raw Player suits from the last eight rounds (experimental)

The fourth selection in **Стратегия / фильтр** is **«Частые масти · 8 раздач / разница ≥6»** (`hot_suits_8_spread_6`). It does not use the suit-transformation table:

1. The target Baccarat round N must have eight preceding, consecutive physical rounds N−8 through N−1 with independently confirmed final Player hands. Missing, duplicate, or unconfirmed rounds block the signal. Consecutive final-hand timestamps must be at most 185 seconds apart.
2. Count the actual suits of **all** two or three Player cards in those eight hands. Do not count Banker cards and do not replace any cards by transformed suits.
3. Calculate `max(suit_count) - min(suit_count)`. If it is below **6**, skip the target round. Otherwise predict the most frequent suit; ties are resolved in the fixed order **♥, ♦, ♣, ♠**.
4. A skipped round is never a WIN or LOSE and must not advance the virtual loss step. On a confirmed WIN the streak resets; on a confirmed LOSE it advances. The existing persisted LIVE acceptance/settlement safeguards remain in force.
5. The nine-step stake plan is configured **separately** in the calculator (e.g. 10, 28, 69, 155, 388, 970, 2450, 6078, 11286 RUB at odds 1.90). Selecting a strategy never silently changes the user's stored stake amounts.

This is a backtested hypothesis, **not a guaranteed or risk-free system**. Historical test on the previously unseen `analiz2.txt` export recorded 173 signals / 91 WIN / 82 LOSE (52.60% accuracy); the profitable nine-step progression in that sample relied on the *order* of wins, not proven positive expected value. The ninth step was not used in the sample. Use DEMO to collect new independent results before considering LIVE.

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
перед таблицей **«История раздач»**. Отдельная история виртуальных денежных
ставок удалена; сохранены просмотр подтверждённых виртуальных WIN/LOSE,
номер шага и источник каждой прогнозируемой масти. Калькулятор доступен
как самостоятельная расчётная форма и не управляет наблюдателем.

## Калькулятор шагов (23.10.2026)
Панель StakePlanner: начальная ставка, редактируемый множитель шага, отдельный коэффициент выплаты БК, число шагов, бюджет всей серии, финансовый результат WIN каждого шага с округлением суммы до копейки. Удалена отдельная таблица виртуальных денежных результатов; история прогнозов и фактических карт сохранена. LIVE-исполнение отсутствует: приложение не выбирает исходы и не отправляет ставки.

### Сохранение расчётной формы на сервере

`GET /api/stake-plan` загружает форму; `PUT /api/stake-plan` сохраняет
поля `initial`, `multiplier`, `steps`, `row`, `odds`, `revision`.
Десятичные значения API возвращает строками, например `"314.29"`.
Суммы хранятся без двоичных ошибок в отдельной таблице
`calculator_settings` существующей SQLite. Таблицы наблюдений и прогнозов
не очищаются. Для отдельного файла можно задать абсолютный путь
`OBSERVATION_DB` в `.env` (он относится ко всей базе приложения).

Ручные суммы сохраняются как введены; сохранение не генерирует ряд заново.
Для расчётной сводки используется целочисленная десятичная арифметика;
итог округляется до копейки, половина копейки — от нуля.
Кнопка «Рассчитать ряд» сохраняет существующий алгоритм генерации.
Предельные значения: 20 шагов, 1 млрд ₽ на сумму, множитель до 10,
коэффициент до 10 000; множитель и коэффициент — до четырёх знаков
после запятой, суммы — до двух.

До загрузки сервера форма заблокирована. Ошибка загрузки или сохранения
показывается явно; черновик при ошибке сохранения остаётся в полях.
Повторное нажатие во время запроса блокируется. `revision` защищает от
перезаписи из другого окна: при устаревшей версии сервер отвечает 409.
«Загрузить сохранённый ряд» заменяет черновик последней серверной версией.
Если серверных настроек ещё нет, прежний ряд из localStorage предлагается
как черновик: для переноса нужно нажать «Применить настройки».
Существующие серверные настройки всегда имеют приоритет.

Настройки переживают перезапуск backend и браузера при сохранении файла
SQLite. Калькулятор не подключён к стратегии или денежному исполнению.
Режим ДЕМО отображается вверху; LIVE явно помечен как не реализованный.

### Проверка и обновление на Windows

Остановите приложение. В каталоге клона проверьте `git status`: собственные
правки и существующую SQLite сохраните отдельно перед обновлением.
Не удаляйте базу и профиль браузера. После объединения изменений с main:

```powershell
git pull --ff-only origin main
cd backend
python -m pip install -r requirements.txt pytest
python -m playwright install chromium
cd ../frontend
npm ci
npm test
npm run build
cd ../backend
python -m pytest tests -q
cd ..
```

Затем запустите `start.bat` как обычно. Для браузерного теста нужны Node.js
и установленный `frontend/node_modules`: тест сам запускает локальные
серверы на свободных портах, использует временную SQLite и перезапускает
backend. Внешние страницы в этом тесте заблокированы. GitHub Actions
устанавливает обе группы зависимостей перед backend-тестами.

### Диагностика Canvas
После ручного открытия страницы конкретной игры и остановки наблюдения кнопка «Проверить рынок» вызывает POST /api/market/probe. Он раскрывает поиск рынков, вводит «Игрок получит карту», собирает подписи и координаты fillText в Canvas. Ни исходы, ни купон, ни отправка ставок не нажимаются. Координаты только диагностические и не считаются готовыми для ставок.

### Тестовый LIVE-фильтр обычного купона

На панели в разделе «Режим работы» появился переключатель
**«Тест LIVE-купона: сначала коэффициент масти, затем сумма»**.
Включать или отключать его нужно при остановленном сканировании.
Режим ДЕМО остаётся наблюдением без действий с купоном.

Когда одновременно включены LIVE и тестовый фильтр, бот для каждого
доступного прогноза до начала раздачи выполняет последовательность:

1. Ищет рынок «Игрок получит карту» через кнопку и поле поиска; ждёт
   достоверной карты координат Canvas2D.
2. Если включена «Ставка в 1 клик», сначала отключает её и проверяет
   результат переключения — иначе выбор коэффициента сам отправит ставку.
3. Выбирает **коэффициент** нужной масти в Canvas, а не её надпись.
4. Дожидается `.quick-coupon-main`; заполняет поле суммы текущим шагом из
   сохранённого ряда; считывает и сверяет сумму.
5. Нажимает `button.quick-coupon-put-bet-button` («Сделать ставку») один раз.
6. Дожидается подтверждения или отказа и закрывает окно результата
   («Закрыть» / «Продолжить») при наличии однозначного ответа сайта.

Стадии отображаются в событиях LIVE на русском языке. Если результат
отправки неизвестен, повторная ставка блокируется. Механизм проверен
на тестовом купоне; действие на актуальном букмекерском сайте необходимо
проверить отдельно в авторизованной сессии. Нельзя считать тестирование
успешным только по наличию нажатий в браузере.

### Тест с первой раздачи без прогрева N−18 (20 рук) / голосования (40 рук)

Режим работы → включите **Тест с первой раздачи — без ожидания 18/40 игр**
и выберите тестовую масть (♥, ♦, ♣ или ♠). При активном LIVE бот
открывает ближайшую подходящую предстоящую раздачу, не ожидая
20/40 подтверждённых рук. Для теста запускается цепочка обычного купона:
поиск рынка → коэффициент выбранной масти → сумма сохранённого шага →
«Сделать ставку» → результат/модальное окно.

Это **тест интерфейса**, не прогноз N−18 или N−3/10/24/31/40:
выбранная масть вручную отображается как MANUAL_TEST и не добавляется
в статистику стратегий. Если букмекер принял тестовую ставку, после
финального подтверждения карт Игрока в той же раздаче её WIN/LOSE
учитывается в отдельном журнале LIVE и шаг ряда изменяется. Если
статус принятия или финальные карты недостоверны, шаг не изменяется,
повторная отправка блокируется.

При отключённом фильтре прежняя подготовка 20/40 рук и прогнозы
остаются без изменений. Настройки теста сохраняются в SQLite.
Для настройки остановите сканирование. По умолчанию фильтр отключён.


### Исправление потери третьей карты и проверка истории

Исправления находятся в основной ветке `main`:
- Обнуление таймера больше не подтверждает окончательный набор из двух карт.
  Нужны явное завершение, стабильное чтение и независимая финальная проверка.
- При доступных первых картах Банкира применяется правило добора Punto Banco.
  Игрок с 0–5 очками обязан добрать карту, кроме natural 8/9 у Банкира.
  При неизвестных картах Банкира правило само по себе не подтверждает конец руки.
- Третья карта, появившаяся во время финального перечитывания, сохраняется сразу.
  Изменившийся набор больше не наследует старое подтверждение.
- Ошибочные старые заключения по таймеру и явно недостающие третьи карты
  исключаются из источников. Исходные карты остаются в базе для восстановления.
- Отдельная вкладка только для чтения проверяет сохранённые URL: текущая игра
  продолжает наблюдаться. Повторы ограничены; завершённые неполные записи
  не исключаются из восстановления после перезапуска.
- Раздел «Пересчёт по исправленной истории» автоматически пересчитывает голоса
  и исследовательский результат после обновления карт, включая случаи без
  исходного прогноза. Отдельно показаны исходный сигнал и причина отсутствия
  результата. Это не доказательство доступности сигнала до начала игры.
  Таблица `cycle18_forecasts`, журнал `live_attempts` и шаг исполненных ставок
  задним числом не переписываются. Пересчёт хранится в `cycle18_history_audit`.
- История сайта может уже не отдавать старые карты. В таком случае запись
  остаётся неподтверждённой: программа не восстанавливает её догадкой.

Перед обновлением остановите backend и frontend и сохраните свою SQLite-базу.
В репозитории исторически отслеживается `observations.sqlite3`; не заменяйте
рабочую историю копией из Git и не выполняйте `git reset --hard`.
После обновления запускайте `start.bat`; миграции применяются автоматически.
