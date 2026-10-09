# BAKKARA BOT

Independent local control panel for a visible Playwright browser session. It opens the configured Baccarat page and lets a person sign in manually. It contains no card reading, analysis, betting, stake entry, or clicking of betting controls.

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

Once the interface says `AUTHORIZED`, press **Начать наблюдение**. The service
visits only the configured Baccarat list and selected game pages, reads exposed
Player-card DOM elements, and stores partial observations in
`observations.sqlite3`. It neither reads odds nor places, prepares, or clicks
bets. A round-end selector has not been confirmed on a live page, so entries
are deliberately marked `PARTIAL` until that DOM signal is verified.

The first start downloads Playwright Chromium. Close the two command windows, or use **Остановить браузер**, to stop the app/browser.

## Manual commands

```powershell
cd backend; python -m pip install -r requirements.txt; python -m playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

In another terminal:

```powershell
cd frontend; npm install; npm run dev
```

Ports are `8010` and `5174`, deliberately separate from the source project. Do not copy the browser profile between projects.
