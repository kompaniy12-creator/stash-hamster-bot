#!/bin/zsh
# Создаёт новый токен Claude и кладёт его в секрет GitHub CLAUDE_CODE_OAUTH_TOKEN.
cd "$(dirname "$0")/.."
echo "1) Сейчас откроется вход в Claude. После входа скопируй токен sk-ant-oat01-… (⌘C)."
echo
claude setup-token
echo
read -rs "CT?2) Вставь токен сюда и нажми Enter (символов не видно): "
echo
CT="${CT//[[:space:]]/}"
if [[ "$CT" != sk-ant-oat* ]]; then
  echo "❌ Это не похоже на токен (должен начинаться с sk-ant-oat). Запусти скрипт ещё раз."
  exit 1
fi
printf %s "$CT" | gh secret set CLAUDE_CODE_OAUTH_TOKEN && unset CT
clear
echo "✅ Новый токен записан в GitHub. Экран очищен."
echo "   Не забудь отозвать СТАРЫЙ токен в настройках claude.ai."
