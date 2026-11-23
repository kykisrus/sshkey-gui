#!/usr/bin/env bash
# Запуск GUI менеджера SSH-ключей
cd "$(dirname "$0")" || exit 1
exec python3 app.py
