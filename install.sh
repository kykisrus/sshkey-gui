#!/usr/bin/env bash
# Установка/удаление приложения «SSH-ключи» для Linux (локально, для текущего пользователя).
#
#   ./install.sh              — установить (меню приложений + ярлык на рабочем столе)
#   ./install.sh --uninstall  — удалить

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/sshkey-gui"
BIN="$HOME/.local/bin/sshkey-gui"
ENTRY="$HOME/.local/share/applications/sshkey-gui.desktop"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

DESKTOP_DIR=""
for d in "$HOME/Рабочий стол" "$HOME/Desktop" "$HOME/Загрузки"; do
  [[ -d "$d" ]] && { DESKTOP_DIR="$d"; break; }
done

uninstall() {
  rm -rf "$DEST"
  rm -f "$BIN" "$ENTRY" "$ICON_DIR/sshkey-gui.png" "$HOME/.local/share/icons/hicolor/scalable/apps/sshkey-gui.svg"
  [[ -n "$DESKTOP_DIR" ]] && rm -f "$DESKTOP_DIR/sshkey-gui.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  echo "Удалено."
  exit 0
}

[[ "${1:-}" == "--uninstall" ]] && uninstall

command -v python3 >/dev/null || { echo "Нужен python3" >&2; exit 1; }
python3 - "$SRC" <<'PY' || true
import sys, importlib.util, pathlib
src = pathlib.Path(sys.argv[1])
# базовая проверка, что модули приложения импортируются
for name in ("app.py", "desktop.py"):
    import ast
    ast.parse((src / name).read_text(encoding="utf-8"))
print("файлы приложения корректны")
PY

mkdir -p "$DEST/static" "$HOME/.local/bin" "$HOME/.local/share/applications" "$ICON_DIR"
install -m 644 "$SRC/app.py" "$SRC/desktop.py" "$SRC/icon.png" "$DEST/"
install -m 644 "$SRC/static/index.html" "$DEST/static/index.html"
install -m 644 "$SRC/icon.png" "$ICON_DIR/sshkey-gui.png"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$DEST/desktop.py" "\$@"
EOF
chmod +x "$BIN"

cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=SSH-ключи
GenericName=Передача SSH-ключей
Comment=Проверить связь с сервером и передать SSH-ключ
Exec=$BIN
Icon=sshkey-gui
Terminal=false
Categories=Network;
Keywords=ssh;key;keys;server;
StartupNotify=true
EOF
chmod +x "$ENTRY"

if [[ -n "$DESKTOP_DIR" ]]; then
  cp "$ENTRY" "$DESKTOP_DIR/sshkey-gui.desktop"
  chmod +x "$DESKTOP_DIR/sshkey-gui.desktop"
  command -v gio >/dev/null && gio set "$DESKTOP_DIR/sshkey-gui.desktop" metadata::trusted true 2>/dev/null || true
fi

command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Установлено:"
echo "  приложение: $DEST"
echo "  запуск:     $BIN"
echo "  меню:       SSH-ключи"
[[ -n "$DESKTOP_DIR" ]] && echo "  ярлык:      $DESKTOP_DIR/sshkey-gui.desktop"
echo
echo "Проверка: $BIN --selftest"
