#!/usr/bin/env bash
# Собирает описание релиза для GitHub: раздел из CHANGELOG.md + инструкция по установке.
#
#   ./packaging/release-notes.sh v0.6.3 > release-notes.md

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  TAG="v$(tr -d '[:space:]' < "$ROOT/VERSION")"
fi
VER="${TAG#v}"

NOTES="$(awk -v ver="$VER" '
  $0 ~ "^## " ver "[ ]" {found = 1; next}
  found && /^## / {exit}
  found {print}
' "$ROOT/CHANGELOG.md" | sed '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"

if [[ -z "$NOTES" ]]; then
  NOTES="Подробности — в CHANGELOG.md репозитория."
fi

cat <<EOF
## Что нового в ${VER}

${NOTES}

## Установка

**Linux (Debian/Ubuntu)** — скачайте \`.deb\` ниже и выполните:

\`\`\`bash
sudo apt install ./sshkey-gui_${VER}_all.deb
\`\`\`

После установки приложение появится в меню как **SSH-ключи**, в терминале — команда
\`sshkey-gui\`. Зависимости (\`python3-gi\`, \`gir1.2-webkit2-4.1\`, \`openssh-client\`)
\`apt\` поставит сам.

**Windows 10/11** — скачайте \`sshkey-gui.exe\` и запустите, установка не требуется.
Интерфейс откроется в браузере, консольное окно должно оставаться открытым.
Нужен встроенный клиент OpenSSH (\`ssh.exe\`), он есть по умолчанию.

## Что важно знать

- Приложение работает только локально: слушает \`127.0.0.1\` и наружу отправляет
  ровно одну вещь — ваш публичный ключ на указанный сервер.
- Пароли не сохраняются и не пишутся в логи.
- Полное описание — в [README](https://github.com/kykisrus/sshkey-gui#readme),
  политика безопасности — в [SECURITY.md](https://github.com/kykisrus/sshkey-gui/blob/main/SECURITY.md).

**Полный список изменений:** [CHANGELOG.md](https://github.com/kykisrus/sshkey-gui/blob/main/CHANGELOG.md)
EOF
