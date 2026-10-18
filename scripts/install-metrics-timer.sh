#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo "Execute como root apenas para instalar o timer do Social LeagueCut." >&2
  exit 1
fi
BASE=/home/leaguecut-social-motor/htdocs/social.leaguecut.com.br
install -m 644 "$BASE/systemd/social-leaguecut-metrics.service" /etc/systemd/system/social-leaguecut-metrics.service
install -m 644 "$BASE/systemd/social-leaguecut-metrics.timer" /etc/systemd/system/social-leaguecut-metrics.timer
systemctl daemon-reload
systemctl enable --now social-leaguecut-metrics.timer
systemctl list-timers --all social-leaguecut-metrics.timer --no-pager
