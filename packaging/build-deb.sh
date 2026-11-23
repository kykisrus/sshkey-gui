#!/usr/bin/env bash
# Сборка .deb для SSH-ключей (sshkey-gui).
# Результат: dist/sshkey-gui_<версия>_all.deb
#
#   ./packaging/build-deb.sh
#
# Зависимостей при сборке не нужно, кроме dpkg-deb (пакет dpkg-dev не обязателен).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="sshkey-gui"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="$ROOT/.deb-build"
DIST="$ROOT/dist"
APP_DIR="$BUILD/usr/share/$PKG"

rm -rf "$BUILD"
mkdir -p "$DIST" \
         "$BUILD/DEBIAN" \
         "$BUILD/usr/bin" \
         "$APP_DIR/static" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/256x256/apps" \
         "$BUILD/usr/share/doc/$PKG"

install -m 644 "$ROOT/app.py" "$ROOT/desktop.py" "$APP_DIR/"
install -m 644 "$ROOT/static/index.html" "$APP_DIR/static/"
install -m 644 "$ROOT/icon.png" "$BUILD/usr/share/icons/hicolor/256x256/apps/$PKG.png"
install -m 644 "$ROOT/README.md" "$ROOT/LICENSE" "$BUILD/usr/share/doc/$PKG/"
gzip -9 -n -c "$ROOT/CHANGELOG.md" > "$BUILD/usr/share/doc/$PKG/changelog.gz"

cat > "$BUILD/usr/bin/$PKG" <<EOF
#!/usr/bin/env bash
exec python3 /usr/share/$PKG/desktop.py "\$@"
EOF
chmod 755 "$BUILD/usr/bin/$PKG"

cat > "$BUILD/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SSH-ключи
GenericName=Управление SSH-ключами
Comment=Проверить связь с сервером и передать SSH-ключ
Exec=$PKG
Icon=$PKG
Terminal=false
Categories=Network;
Keywords=ssh;key;keys;server;
StartupNotify=true
EOF
chmod 644 "$BUILD/usr/share/applications/$PKG.desktop"

cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: kykis_rus <kykis_rus@users.noreply.github.com>
Homepage: https://github.com/kykisrus/sshkey-gui
Depends: python3 (>= 3.8), python3-gi, gir1.2-webkit2-4.1, openssh-client
Recommends: desktop-file-utils, hicolor-icon-theme
Description: Управление SSH-ключами для Linux
 Простое приложение для просмотра SSH-ключей, создания новых ключей,
 проверки связи с сервером и передачи публичного ключа в authorized_keys.
 .
 Работает локально: слушает только 127.0.0.1, наружу ничего не отправляет,
 кроме публичного ключа на выбранный сервер.
EOF

cat > "$BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 755 "$BUILD/DEBIAN/postinst"

cat > "$BUILD/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 755 "$BUILD/DEBIAN/prerm"

find "$BUILD" -type d -exec chmod 755 {} +
find "$BUILD/usr/share/doc/$PKG" -type f -exec chmod 644 {} +

OUT="$DIST/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$BUILD" "$OUT" >/dev/null
rm -rf "$BUILD"
echo "$OUT"
