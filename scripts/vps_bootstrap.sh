#!/usr/bin/env bash
# VPS bootstrap for landing-generator.
#
# Run this ONCE on the VPS as a sudoer:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/vps_bootstrap.sh | sudo bash
#
# What it does:
#   1. Installs Docker + Compose plugin + nginx + certbot.
#   2. Clones the repo into /opt/landing-generator.
#   3. Creates /opt/landing-generator/.env with chmod 0600 (you must paste the
#      Yunwu key into it before the first up).
#   4. Installs the nginx site for landing.shopinzo.bond and reloads nginx.
#   5. Brings the docker-compose stack up.
#
# After bootstrap, GitHub Actions handles future deploys via SSH + git pull.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/hamoza33/Landing_Page_Image.git}"
APP_DIR="${APP_DIR:-/opt/landing-generator}"
DOMAIN="${DOMAIN:-landing.shopinzo.bond}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root or with sudo." >&2
  exit 1
fi

echo "==> Updating apt and installing base packages"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl gnupg lsb-release git ufw nginx certbot python3-certbot-nginx

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

echo "==> Configuring firewall"
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
yes | ufw enable || true

echo "==> Cloning repo to $APP_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch --all
  git -C "$APP_DIR" reset --hard origin/main
fi

echo "==> Ensuring .env exists at $APP_DIR/.env (0600)"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 0600 "$APP_DIR/.env"
  echo "    !! Edit $APP_DIR/.env and set YUNWU_API_KEY, then re-run: docker compose up -d --build"
fi

echo "==> Installing nginx site"
install -m 0644 "$APP_DIR/nginx/landing.conf" /etc/nginx/sites-available/landing.conf
ln -sf /etc/nginx/sites-available/landing.conf /etc/nginx/sites-enabled/landing.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Building and starting docker compose"
cd "$APP_DIR"
docker compose pull || true
docker compose up -d --build

echo "==> (Optional) Issuing TLS via certbot — skipped if DNS not pointed yet"
if getent hosts "$DOMAIN" >/dev/null 2>&1; then
  certbot --non-interactive --agree-tos --redirect --nginx \
    -m "$ADMIN_EMAIL" -d "$DOMAIN" || echo "certbot failed; rerun once DNS is correct."
else
  echo "    DNS not resolving for $DOMAIN yet; skip TLS for now."
fi

echo "==> Done. App should be reachable at http://$DOMAIN/  (or https:// once TLS issued)."
