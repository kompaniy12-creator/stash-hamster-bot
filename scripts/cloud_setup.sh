#!/bin/zsh
# Переселяет хомяка в GitHub Actions. Запускать из папки проекта после того, как код запушен на GitHub.
# Нужны: gh (залогинен), заполненный .env (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID).
set -e
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_ALLOWED_USER_ID" ] || { echo "❌ Заполни .env"; exit 1; }
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
echo "🐹 Переезжаем в $REPO"
uv sync -q

# 1. Ключ шифрования норки
if [ -z "$STASH_DATA_KEY" ]; then
  STASH_DATA_KEY=$(uv run python crypto_store.py genkey)
  printf '\n# Ключ шифрования норки (не терять! без него каталог не прочитать)\nSTASH_DATA_KEY=%s\n' "$STASH_DATA_KEY" >> .env
  echo "🔑 Создал ключ шифрования и сохранил в .env"
fi

# 2. Секреты (значения не выводятся)
gh secret set TELEGRAM_BOT_TOKEN --body "$TELEGRAM_BOT_TOKEN"
gh secret set TELEGRAM_ALLOWED_USER_ID --body "$TELEGRAM_ALLOWED_USER_ID"
gh secret set STASH_DATA_KEY --body "$STASH_DATA_KEY"
gh variable set STASH_TZ --body "$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"

# 3. Доступ к Claude
if ! gh secret list | grep -q CLAUDE_CODE_OAUTH_TOKEN; then
  echo ""
  echo "Теперь токен Claude. В ДРУГОМ окне терминала выполни:  claude setup-token"
  echo "Войди в аккаунт и скопируй токен (sk-ant-oat...)."
  read -rs "CT?Вставь токен сюда и нажми Enter (символы не видны): "
  echo
  gh secret set CLAUDE_CODE_OAUTH_TOKEN --body "$CT"
  unset CT
fi

# 4. Перенести текущую норку в зашифрованную ветку
if ! git ls-remote --exit-code --heads origin norka-data >/dev/null 2>&1; then
  [ -f data/norka.json ] || echo '{"next_id":1,"items":{},"meta":{}}' > data/norka.json
  TMP=$(mktemp -d)
  git worktree add --orphan -b norka-data "$TMP/norka" >/dev/null
  STASH_DATA_KEY="$STASH_DATA_KEY" uv run python crypto_store.py encrypt data/norka.json "$TMP/norka/norka.enc"
  shasum -a 256 data/norka.json | cut -d' ' -f1 > "$TMP/norka/norka.sha"
  (cd "$TMP/norka" && git add -A && git -c user.name=stash -c user.email=stash@localhost commit -qm "🐹 норка переехала" && git push -q origin HEAD:norka-data)
  git worktree remove --force "$TMP/norka"; git branch -D norka-data >/dev/null 2>&1 || true
  echo "📦 Норка зашифрована и отправлена в ветку norka-data"
fi

# 5. На Mac остаётся только грузчик
zsh scripts/install_mac.sh helper

# 6. Первый прогон
gh workflow enable stash.yml >/dev/null 2>&1 || true
gh workflow run stash.yml
echo ""
echo "✅ Готово! Хомяк живёт в GitHub Actions. Он отвечает с задержкой 5–15 минут."
echo "   Прогоны: gh run list --workflow=stash.yml"
