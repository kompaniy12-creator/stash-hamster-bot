#!/bin/zsh
# ⚡ Мгновенный режим: Cloudflare Worker принимает сообщения Telegram, сразу отвечает «🐹 Хап!»
# и будит GitHub Actions. Запускать после cloud_setup.sh. Нужен бесплатный аккаунт Cloudflare.
set -e
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .env
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_ALLOWED_USER_ID" ] || { echo "❌ Заполни .env"; exit 1; }
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
OWNER=${REPO%%/*}
W() { npx -y wrangler@4 "$@"; }
cd worker

echo "☁️  1/6 Вход в Cloudflare"
W whoami 2>/dev/null | grep -q "You are logged in" || W login

echo "🗄  2/6 Очередь (D1)"
DB_ID=$(W d1 list --json 2>/dev/null | uv run --no-project python -c "import json,sys; print(next((d['uuid'] for d in json.load(sys.stdin) if d['name']=='stash-queue'), ''))")
if [ -z "$DB_ID" ]; then
  W d1 create stash-queue >/dev/null
  DB_ID=$(W d1 list --json | uv run --no-project python -c "import json,sys; print(next(d['uuid'] for d in json.load(sys.stdin) if d['name']=='stash-queue'))")
fi
sed "s/__DB_ID__/$DB_ID/" wrangler.toml.template > wrangler.toml
W d1 execute stash-queue --remote --file schema.sql -y >/dev/null

echo "🚀 3/6 Выкладываю Worker"
URL=$(W deploy 2>&1 | grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' | head -1)
[ -n "$URL" ] || { echo "❌ Не удалось получить адрес Worker"; exit 1; }
echo "   $URL"

echo "🔐 4/6 Секреты Worker"
WEBHOOK_SECRET=$(openssl rand -hex 32)
QUEUE_SECRET=$(openssl rand -hex 32)
put() { printf %s "$2" | W secret put "$1" >/dev/null; }
put TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
put OWNER_ID "$TELEGRAM_ALLOWED_USER_ID"
put WEBHOOK_SECRET "$WEBHOOK_SECRET"
put QUEUE_SECRET "$QUEUE_SECRET"
put GH_REPO "$REPO"

echo
echo "🔑 5/6 Токен GitHub, чтобы Worker мог будить хомяка"
echo "   Откроется страница создания токена. Там:"
echo "   • Repository access → Only select repositories → $REPO"
echo "   • Permissions уже выставлены: Actions — Read and write (больше ничего не нужно)"
echo "   • Нажми Generate token и скопируй его"
open "https://github.com/settings/personal-access-tokens/new?name=stash-hamster-worker&description=Cloudflare+Worker+wakes+Stash+workflow&target_name=$OWNER&expires_in=366&actions=write"
while true; do
  read -rs "GT?   Вставь токен и нажми Enter (символов не видно): "; echo
  GT="${GT//[[:space:]]/}"
  code=$(curl -s -o /dev/null -w '%{http_code}' -K - <<< "header = \"Authorization: Bearer $GT\"
url = \"https://api.github.com/repos/$REPO/actions/workflows/stash.yml\"")
  [ "$code" = 200 ] && break
  echo "   ❌ Токен не подошёл (код $code). Проверь, что выбран репозиторий $REPO, и вставь ещё раз."
done
put GH_TOKEN "$GT"; unset GT

echo "🔗 6/6 Подключаю Telegram и GitHub"
printf %s "$QUEUE_SECRET" | gh secret set STASH_QUEUE_SECRET
gh variable set STASH_QUEUE_URL --body "$URL"
RES=$(curl -s -K - <<< "url = \"https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook\"
data-urlencode = \"url=$URL/tg\"
data-urlencode = \"secret_token=$WEBHOOK_SECRET\"
data-urlencode = \"allowed_updates=[\\\"message\\\",\\\"callback_query\\\"]\"")
echo "$RES" | grep -q '"ok":true' || { echo "❌ Telegram не принял webhook"; exit 1; }
unset WEBHOOK_SECRET QUEUE_SECRET

echo
echo "⚡ Готово! Хомяк отвечает «Хап!» мгновенно, карточка — примерно через минуту."
echo "   Вернуть как было: zsh scripts/install_mac.sh local  (или удалить webhook в Telegram)"
