#!/usr/bin/env bash
set -euo pipefail
/usr/bin/systemctl restart social-leaguecut.service
/usr/bin/systemctl is-active --quiet social-leaguecut.service
