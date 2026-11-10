#!/bin/zsh
# Устанавливает автозапуск на Mac.
#   zsh scripts/install_mac.sh local   — хомяк целиком живёт на Mac (отвечает сразу)
#   zsh scripts/install_mac.sh helper  — хомяк в GitHub Actions, на Mac только грузчик-установщик
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-helper}"
AGENTS=~/Library/LaunchAgents
mkdir -p "$AGENTS" "$DIR/data"
chmod 700 "$DIR/data"; [ -f "$DIR/.env" ] && chmod 600 "$DIR/.env"
cd "$DIR" && uv sync -q --extra voice

render() { # label script log schedule
  sed -e "s|__LABEL__|$1|g" -e "s|__DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" \
      -e "s|__SCRIPT__|$2|g" -e "s|__LOG__|$3|g" -e "s|__SCHEDULE__|$4|g" \
      "$DIR/scripts/launchd.plist.template" > "$AGENTS/$1.plist"
  launchctl unload "$AGENTS/$1.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$1.plist"
}
stop() { [ -f "$AGENTS/$1.plist" ] && launchctl unload "$AGENTS/$1.plist" 2>/dev/null && rm "$AGENTS/$1.plist" || true; }

if [ "$MODE" = local ]; then
  # локальный бот читает Telegram сам — снимаем webhook мгновенного режима, если он был
  ( source "$DIR/.env"; curl -s -K - <<< "url = \"https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook\"" >/dev/null ) || true
  gh variable delete STASH_QUEUE_URL >/dev/null 2>&1 || true
  stop com.stash.helper
  render com.stash.bot bot.py bot.log '<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>'
  echo "🐹 Хомяк живёт на этом Mac. Лог: data/bot.log"
else
  stop com.stash.bot
  stop com.serhii.skill-inbox-bot   # старое имя
  render com.stash.helper mac_helper.py helper.log '<key>RunAtLoad</key><true/><key>StartInterval</key><integer>120</integer>'
  echo "🚚 Грузчик на Mac запущен (раз в 2 минуты). Лог: data/helper.log"
fi
