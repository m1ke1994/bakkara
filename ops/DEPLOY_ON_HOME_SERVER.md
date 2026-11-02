# Bakkara on home-server behind the existing Docker Nginx (Russian guide)

**DO NOT** install the standalone `ops/nginx-bakkara.conf` onto host nginx in this setup.
**DO NOT** run host Certbot nginx installer or expose extra ports 80/443.
This deployment assumes:
- Docker container `ydro-nginx-1` already serves 80/443.
- Docker network `ydro_yadro_network` exists.
- Existing host path `/home/alex/auto/auto/ydro/docker/nginx/templates-https` is mounted at `/etc/nginx/templates` inside `ydro-nginx-1`.
- Valid certificate: `/etc/letsencrypt/live/sistem-loyality.ru/{fullchain.pem,privkey.pem}`.
- `sistem-loyality-acme.conf.template` currently owns port 80 for HTTP-01, and must be kept for renewals.

All Bakkara code stays on `Tedeev_Aslan_bakkara`; the Bakkara `main` is unchanged. Ydro's `default.conf.template` is not edited.

## 1. Get updated deployment files

```bash
set -e
cd /home/alex/projects/Tedeev_Aslan_Bakkara/bakkara
git status --short
git branch --show-current   # MUST be Tedeev_Aslan_bakkara
git pull --ff-only origin Tedeev_Aslan_bakkara
docker compose version
```

If the working tree has local edits, stop before pulling. Backup existing private data before any changes. Do not use `git reset --hard`.

## 2. Create credentials without putting plaintext passwords in Git or shell history

```bash
cd /home/alex/projects/Tedeev_Aslan_Bakkara/bakkara
umask 077
if [ -e .env ]; then echo 'Existing .env found: do not overwrite'; else
python3 - <<'PY'
from pathlib import Path
from getpass import getpass
from secrets import token_hex
import sys

sys.path.insert(0, str(Path.cwd() / "backend"))
from app.dashboard_auth import password_hash

file = Path('.env')
if file.exists():
    raise SystemExit('Refusing to overwrite existing .env')
values = {
    'BAKKARA_ASLAN_PASSWORD_HASH=': "BAKKARA_ASLAN_PASSWORD_HASH='" + password_hash(getpass('Aslan login password: ')) + "'",
    'BAKKARA_TEDEEV_PASSWORD_HASH=': "BAKKARA_TEDEEV_PASSWORD_HASH='" + password_hash(getpass('Tedeev login password: ')) + "'",
    'BAKKARA_SESSION_SECRET=': 'BAKKARA_SESSION_SECRET=' + token_hex(32),
    'BROWSER_PROFILE_DIR=/var/lib/bakkara/chromium': 'BROWSER_PROFILE_DIR=/data/chromium',
    'OBSERVATION_DB=/var/lib/bakkara/observations.sqlite3': 'OBSERVATION_DB=/data/observations.sqlite3',
}
content = Path('.env.production.example').read_text()
for old, new in values.items():
    if content.count(old) != 1:
        raise SystemExit('Missing template setting: ' + old)
    content = content.replace(old, new)
file.write_text(content)
file.chmod(0o600)
print('Created private .env, no passwords shown.')
PY
fi
mkdir -p data/chromium
sudo chown -R 10001:10001 data
sudo chmod 700 data data/chromium
```

Password hashes must stay in *single quotes*: Compose otherwise expands dollar signs and invalidates hashes. Never paste the generated .env into a chat, terminal log, or GitHub commit. The two login emails are `Aslan1994@gmail.com` and `Tedeev1994@gmail.com`. Change temporary shared password 12345678 to strong distinct passwords when possible.

## 3. Build isolated containers without touching Ydro

```bash
cd /home/alex/projects/Tedeev_Aslan_Bakkara/bakkara
docker compose -p bakkara -f ops/docker-compose.existing-nginx.yml config --quiet
docker compose -p bakkara -f ops/docker-compose.existing-nginx.yml up -d --build
docker compose -p bakkara -f ops/docker-compose.existing-nginx.yml ps
docker compose -p bakkara -f ops/docker-compose.existing-nginx.yml logs --tail=60
```

There are no published Bakkara ports: its API and frontend are reached only inside `ydro_yadro_network`. Node 22 and Chromium are installed inside separate Docker image builds, so host Node 18 and existing Ydro services are unchanged.

Verify the dashboard is protected BEFORE connecting the public domain:

```bash
docker exec bakkara-ui wget -qO- http://127.0.0.1/api/health
docker exec bakkara-ui sh -c 'wget -S -O /dev/null http://127.0.0.1/api/state 2>&1 | grep "401 Unauthorized"'
docker exec ydro-nginx-1 sh -c 'wget -qO- http://bakkara-ui/api/health'
```

Expected: `{"ok":true}`, then HTTP 401 for anonymous `/api/state`, then again `{"ok":true}` from the main nginx network. Do not publish if API returns 200 without login.

## 4. Add HTTPS for this domain only

```bash
cd /home/alex/projects/Tedeev_Aslan_Bakkara/bakkara
DEST=/home/alex/auto/auto/ydro/docker/nginx/templates-https
# Must exist: the fullchain + private key already issued by certbot.
sudo test -s /etc/letsencrypt/live/sistem-loyality.ru/fullchain.pem
sudo test -s /etc/letsencrypt/live/sistem-loyality.ru/privkey.pem
cp ops/nginx-existing-ydro-https.conf.template "$DEST/sistem-loyality-https.conf.template"
docker exec ydro-nginx-1 sh -c 'cp /etc/nginx/templates/sistem-loyality-https.conf.template /etc/nginx/conf.d/sistem-loyality-https.conf'
docker exec ydro-nginx-1 nginx -t
```

Only if `nginx -t` succeeds:

```bash
docker exec ydro-nginx-1 nginx -s reload
curl -I --resolve sistem-loyality.ru:443:127.0.0.1 https://sistem-loyality.ru/
curl -i --resolve sistem-loyality.ru:443:127.0.0.1 https://sistem-loyality.ru/api/state
```

Expected: valid TLS, Bakkara HTML, HTTP 401 for anonymous API. TrackNode and other vhosts are unchanged. If a health check fails, remove only `sistem-loyality-https.conf` from the container and its matching template from the host bind mount, test, and reload; don't touch `default.conf.template`.

## 5. Keep ACME renewal and redirect HTTP to HTTPS

Once HTTPS is confirmed working:

```bash
cd /home/alex/projects/Tedeev_Aslan_Bakkara/bakkara
DEST=/home/alex/auto/auto/ydro/docker/nginx/templates-https
cp ops/nginx-existing-ydro-http.conf.template "$DEST/sistem-loyality-acme.conf.template"
docker exec ydro-nginx-1 sh -c 'cp /etc/nginx/templates/sistem-loyality-acme.conf.template /etc/nginx/conf.d/sistem-loyality-acme.conf'
docker exec ydro-nginx-1 nginx -t
# Reload only after the test succeeds.
docker exec ydro-nginx-1 nginx -s reload
curl -I http://sistem-loyality.ru/
curl -fsS http://sistem-loyality.ru/.well-known/acme-challenge/check.txt
```

HTTP should redirect to HTTPS, but `/.well-known/acme-challenge/` must remain accessible for certbot renewals. Check the existing Certbot deploy hooks and set up a safe reload hook for newly renewed certificates if one is not already present.

## Operational checks

```bash
docker compose -p bakkara -f ops/docker-compose.existing-nginx.yml ps
docker logs bakkara-api --tail=80
docker logs bakkara-ui --tail=40
curl -I https://sistem-loyality.ru/
curl -i https://sistem-loyality.ru/api/auth/me
curl -i https://sistem-loyality.ru/api/state
```

Use the two dashboard accounts; click Start Browser and Start Observation. Chromium runs headless. **Public Baccarat cards must actually be accessible without a bookmaker login** for the scan to work; this has not been verified on this server. In this deployment the backend forces `BAKKARA_PUBLIC_READ_ONLY=1` so it does not send live wagers.
