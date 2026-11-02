# BAKKARA CONTROL — production deployment

**Branch:** Tedeev_Aslan_bakkara (not main)
**Domain:** sistem-loyality.ru
**Server:** 95.31.22.162
**Mode:** login-protected dashboard + public bookmaker hand observer, **DEMO only** (no bookmaker login or real bet execution).

## Requirements
- Linux server with Ubuntu 22.04/24.04, SSH and a user with sudo.
- A record sistem-loyality.ru -> 95.31.22.162. Allow inbound TCP 80 and 443; do not expose 8010.
- Node.js 20+ and npm, Python 3.11+, Nginx, Certbot, Playwright Chromium runtime dependencies.
- The bookmaker may restrict anonymous or datacenter requests; the system must not bypass site restrictions, CAPTCHAs or login requirements.

## Install server dependencies

~~~bash
sudo apt update
sudo apt install -y git python3-venv python3-pip nginx certbot python3-certbot-nginx
node --version  # install Node.js 20+ if missing
npm --version
sudo useradd --system --create-home --home-dir /var/lib/bakkara --shell /usr/sbin/nologin bakkara || true
sudo install -d -o bakkara -g bakkara -m 0700 /var/lib/bakkara
sudo install -d -o bakkara -g bakkara -m 0700 /var/lib/bakkara/chromium
sudo install -d -m 0755 /opt/bakkara
# Clone as an administrator first, then give the read-only service user access:
sudo git clone --branch Tedeev_Aslan_bakkara --single-branch https://github.com/m1ke1994/bakkara.git /opt/bakkara
sudo chown -R bakkara:bakkara /opt/bakkara
sudo -u bakkara python3 -m venv /opt/bakkara/.venv
sudo -u bakkara /opt/bakkara/.venv/bin/pip install -r /opt/bakkara/backend/requirements.txt
sudo /opt/bakkara/.venv/bin/python -m playwright install --with-deps chromium
sudo -u bakkara env HOME=/var/lib/bakkara /opt/bakkara/.venv/bin/python -m playwright install chromium
~~~

**Note:** Chromium is installed in the service account's Playwright cache under /var/lib/bakkara.

## Configure credentials without committing secrets

~~~bash
cd /opt/bakkara
sudo cp .env.production.example .env
sudo chown bakkara:bakkara .env
sudo chmod 600 .env
# Generate a NEW salted hash for each account (enter the password interactively)
cd backend
sudo -u bakkara /opt/bakkara/.venv/bin/python -m app.dashboard_auth hash
sudo -u bakkara /opt/bakkara/.venv/bin/python -m app.dashboard_auth hash
~~~

Set BAKKARA_ASLAN_PASSWORD_HASH and BAKKARA_TEDEEV_PASSWORD_HASH in the server-only /opt/bakkara/.env (one hash from each prompt). Also generate a random session secret:

~~~bash
openssl rand -hex 32
~~~

Set that output as BAKKARA_SESSION_SECRET. Store passwords safely; the requested temporary password should be replaced with a strong unique password for each account as soon as possible. NEVER add secrets to Git.

The two usernames are aslan1994@gmail.com and tedeev1994@gmail.com. Production fails closed without valid hashes and a 32+ character session secret.

## Build frontend and start service

~~~bash
cd /opt/bakkara/frontend
npm ci
npm run build
sudo install -d -m 0755 /var/www/bakkara
sudo cp -a dist/. /var/www/bakkara/
sudo cp /opt/bakkara/ops/bakkara.service /etc/systemd/system/bakkara.service
sudo systemctl daemon-reload
sudo systemctl enable --now bakkara.service
sudo journalctl -u bakkara -n 80 --no-pager
sudo cp /opt/bakkara/ops/nginx-bakkara.conf /etc/nginx/sites-available/bakkara
sudo ln -sfn /etc/nginx/sites-available/bakkara /etc/nginx/sites-enabled/bakkara
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d sistem-loyality.ru --redirect
~~~

Certbot must complete before HTTPS login works: the session cookie is intentionally Secure and the panel requires HTTPS.

## Usage

Open https://sistem-loyality.ru → log into the dashboard → Start browser → Start observation.
A headless browser does not need a monitor/Xvfb. The dashboard's account login is separate from bookmaker login. The first completed Player hand activates the second-card prediction for the next round.
If the public site does not expose cards to anonymous datacenter requests, DEMO cannot collect them; **do not** force authentication bypass.

## Diagnostics

~~~bash
sudo systemctl status bakkara --no-pager
sudo journalctl -u bakkara -f
curl -sS https://sistem-loyality.ru/api/health
curl -i https://sistem-loyality.ru/api/state  # MUST return HTTP/2 401 before dashboard login
sudo nginx -t
~~~

When deploying updates, back up /var/lib/bakkara first, and use git pull --ff-only on the deployment branch. Do not overwrite persistent SQLite, the browser profile, or /opt/bakkara/.env. Keep one Uvicorn worker to avoid duplicate browser observers.
