#!/usr/bin/env bash
# Проверяет, что версия одинаковая в VERSION, app.py, README и CHANGELOG.
# Запускается локально и в CI перед публикацией релиза.
#
#   bash packaging/check-docs.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

VER="$(tr -d '[:space:]' < VERSION)"
FAILED=0

check() {
  local pattern="$1" file="$2" label="$3"
  if grep -qF -- "$pattern" "$file"; then
    echo "  ok   $label"
  else
    echo "  ОШИБКА  $label — не найдено «$pattern» в $file"
    FAILED=1
  fi
}

echo "Версия в VERSION: $VER"
check "VERSION = \"$VER\"" app.py "app.py"
check "**Версия:** $VER" README.md "README.md"
check "## $VER" CHANGELOG.md "CHANGELOG.md"

# В README не должно остаться примеров с другими версиями .deb
STALE="$(grep -oE 'sshkey-gui_[0-9]+\.[0-9]+\.[0-9]+_all\.deb' README.md | grep -v "_${VER}_" | sort -u || true)"
if [[ -n "$STALE" ]]; then
  echo "  ОШИБКА  в README устаревшие версии в примерах: $STALE"
  FAILED=1
else
  echo "  ok   примеры установки в README"
fi

if [[ $FAILED -eq 0 ]]; then
  echo "Документы соответствуют версии $VER."
else
  echo "Версии расходятся — поправьте файлы перед выпуском."
fi
exit $FAILED
