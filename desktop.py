#!/usr/bin/env python3
"""Нативное окно приложения «SSH-ключи» (GTK3 + WebKit2).

Запускает локальный сервер из app.py и показывает интерфейс в своём окне,
без вкладок и адресной строки. Если GTK/WebKit недоступны — открывает в браузере.
"""

import os
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app as server_app  # noqa: E402  (локальный модуль рядом)

URL = f"http://127.0.0.1:{server_app.PORT}/"


def start_server():
    """Поднимает сервер в фоне. Если порт занят — значит уже запущен, это нормально."""
    from http.server import ThreadingHTTPServer

    try:
        server = ThreadingHTTPServer(("127.0.0.1", server_app.PORT), server_app.Handler)
    except OSError:
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def selftest():
    server = start_server()
    import urllib.request

    with urllib.request.urlopen(URL, timeout=5) as resp:
        body = resp.read()
    ok = resp.status == 200 and b"SSH" in body
    print(f"selftest: HTTP {resp.status}, {len(body)} байт, {'ок' if ok else 'ОШИБКА'}")
    if server:
        server.shutdown()
    return 0 if ok else 1


def run_window():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2
    except Exception as exc:  # noqa: BLE001
        # Без GTK (например, Windows или минимальный Linux) работаем через браузер,
        # но процесс не завершаем — иначе вместе с ним остановится сервер.
        print(f"GTK/WebKit недоступны ({exc}) — работаю через браузер")
        server = start_server()
        webbrowser.open(URL)
        print(f"Сервер работает: {URL}")
        print("Закрой это окно терминала или нажми Ctrl+C, чтобы остановить.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        if server:
            server.shutdown()
        return 0

    server = start_server()

    window = Gtk.Window(title="SSH-ключи")
    window.set_default_size(1150, 760)
    window.set_position(Gtk.WindowPosition.CENTER)
    for name in ("icon.png", "icon.svg"):
        icon = os.path.join(HERE, name)
        if os.path.exists(icon):
            try:
                window.set_icon_from_file(icon)
                break
            except Exception:  # noqa: BLE001
                pass

    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_enable_developer_extras(False)
    view.load_uri(URL)
    window.add(view)

    def on_destroy(*_):
        if server:
            server.shutdown()
        Gtk.main_quit()

    window.connect("destroy", on_destroy)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(run_window())
