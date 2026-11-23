#!/usr/bin/env python3
"""Менеджер SSH-ключей: список ключей слева, проверка связи и передача ключа справа.

Запуск:  python3 app.py    (откроет http://127.0.0.1:8765 в браузере)

Только локально: сервер слушает 127.0.0.1 и никуда не ходит, кроме выбранного хоста.
"""

import glob
import json
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SSH_DIR = Path.home() / ".ssh"
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("SSHKEY_GUI_PORT", "8765"))
VERSION = "0.5.2"
SSH = shutil.which("ssh") or "/usr/bin/ssh"
SSH_COPY_ID = shutil.which("ssh-copy-id") or "/usr/bin/ssh-copy-id"
SSH_KEYGEN = shutil.which("ssh-keygen") or "/usr/bin/ssh-keygen"

# Общие опции: только выбранный ключ, без перебора ключей из агента
BASE_OPTS = [
    "-o", "IdentitiesOnly=yes",
    "-o", "IdentityAgent=none",
    "-o", "StrictHostKeyChecking=accept-new",
]


def list_keys():
    keys = []
    for pub in sorted(SSH_DIR.glob("*.pub")):
        priv = pub.with_suffix("")
        info = {"name": pub.stem, "pub": str(pub), "private": str(priv) if priv.exists() else "",
                "bits": "", "fingerprint": "", "type": "", "comment": "", "usable": priv.exists()}
        try:
            out = subprocess.run([SSH_KEYGEN, "-lf", str(pub)], capture_output=True, text=True, timeout=10)
            line = out.stdout.strip()
            if line:
                parts = line.split()
                info["bits"] = parts[0] if parts else ""
                info["fingerprint"] = parts[1] if len(parts) > 1 else ""
                info["type"] = line[line.rfind("(") + 1:line.rfind(")")] if "(" in line else ""
                info["comment"] = " ".join(parts[2:-1]) if len(parts) > 3 else ""
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)
        keys.append(info)
    return keys


def tcp_check(host, port, timeout=5.0):
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            ms = round((time.perf_counter() - started) * 1000)
            sock.settimeout(3)
            banner = ""
            try:
                banner = sock.recv(160).decode("utf-8", "replace").strip()
            except OSError:
                pass
            return {"ok": True, "ms": ms, "banner": banner}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "ms": round((time.perf_counter() - started) * 1000)}


def run(cmd, env=None, timeout=25, input_text=None):
    started = time.perf_counter()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                             input=input_text)
        return {"code": out.returncode, "stdout": out.stdout.strip(), "stderr": out.stderr.strip(),
                "ms": round((time.perf_counter() - started) * 1000)}
    except subprocess.TimeoutExpired:
        return {"code": 124, "stdout": "", "stderr": "таймаут", "ms": round((time.perf_counter() - started) * 1000)}


def clean_detail(text):
    """Убирает шум ssh (подключение /dev/null из чужого Host-блока, предупреждения)."""
    lines = []
    for line in (text or "").splitlines():
        if "/dev/null" in line or line.startswith("Warning: Permanently added"):
            continue
        lines.append(line)
    return "\n".join(lines)[:600]


def key_auth(user, host, port, key_path):
    cmd = [SSH, "-i", key_path, *BASE_OPTS,
           "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
           "-o", "PubkeyAuthentication=yes", "-o", "PreferredAuthentications=publickey",
           "-o", "ConnectTimeout=8", "-p", str(port), f"{user}@{host}", "true"]
    res = run(cmd, timeout=20)
    res["ok"] = res["code"] == 0
    return res


def install_key(user, host, port, key_path, password):
    """Кладёт публичный ключ в ~/.ssh/authorized_keys на сервере.

    Делаем это сами, без ssh-copy-id: тот создаёт временный каталог в ~/.ssh,
    а в песочнице эта папка доступна только на чтение. Запись идёт на удалённой стороне.
    """
    pub_path = Path(str(key_path) + ".pub")
    if not pub_path.exists():
        return {"code": 1, "stdout": "", "stderr": f"нет публичного ключа {pub_path}", "ms": 0, "added": False}

    key_line = pub_path.read_text(encoding="utf-8").strip()
    quoted = shlex.quote(key_line)
    script = (
        "umask 077\n"
        "mkdir -p ~/.ssh\n"
        "touch ~/.ssh/authorized_keys\n"
        "chmod 700 ~/.ssh\n"
        "chmod 600 ~/.ssh/authorized_keys\n"
        f"if grep -qxF {quoted} ~/.ssh/authorized_keys 2>/dev/null; then echo PRESENT;\n"
        f"else printf '%s\\n' {quoted} >> ~/.ssh/authorized_keys; echo ADDED; fi\n"
    )

    tmpdir = None
    env = os.environ.copy()
    try:
        if password:
            tmpdir = tempfile.mkdtemp(prefix="sshkey-gui-")
            askpass = Path(tmpdir) / "askpass.sh"
            askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$SSHKEY_GUI_PASSWORD"\n', encoding="utf-8")
            askpass.chmod(0o700)
            env.update({
                "SSH_ASKPASS": str(askpass),
                "SSH_ASKPASS_REQUIRE": "force",
                "SSHKEY_GUI_PASSWORD": password,
                "DISPLAY": os.environ.get("DISPLAY", ":0"),
            })

        cmd = [SSH, *BASE_OPTS,
               "-o", "ConnectTimeout=8",
               "-o", "PubkeyAuthentication=no",
               "-o", "PreferredAuthentications=password,keyboard-interactive",
               "-o", "KbdInteractiveAuthentication=yes",
               "-o", "NumberOfPasswordPrompts=1",
               "-p", str(port), f"{user}@{host}", "bash -s"]
        res = run(cmd, env=env, timeout=40, input_text=script)
        res["added"] = "ADDED" in (res["stdout"] or "")
        return res
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли, без паролей
        print("[gui] " + (fmt % args))

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        """Разрешаем только обращения по своим адресам (защита от DNS-rebinding)."""
        host = (self.headers.get("Host") or "").strip().lower()
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}
        return host in allowed

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        if self.path in ("/", "/index.html"):
            html = (HERE / "static" / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/keys":
            return self._send(200, {"keys": list_keys(), "home": str(SSH_DIR), "version": VERSION})
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            return self._send(400, {"error": "bad json"})

        if self.path == "/api/check":
            host = (data.get("host") or "").strip()
            port = int(data.get("port") or 22)
            user = (data.get("user") or os.environ.get("USER", "root")).strip()
            key = (data.get("key") or "").strip()
            if not host:
                return self._send(400, {"error": "не указан хост"})
            tcp = tcp_check(host, port)
            result = {"tcp": tcp, "host": host, "port": port, "user": user}
            if tcp.get("ok") and key and Path(key).exists():
                auth = key_auth(user, host, port, key)
                result["key_auth"] = {"ok": auth["ok"], "ms": auth["ms"],
                                      "detail": clean_detail(auth["stderr"] or auth["stdout"])}
            return self._send(200, result)

        if self.path == "/api/copy":
            host = (data.get("host") or "").strip()
            port = int(data.get("port") or 22)
            user = (data.get("user") or os.environ.get("USER", "root")).strip()
            key = (data.get("key") or "").strip()
            password = data.get("password") or ""
            if not host or not key or not Path(key).exists():
                return self._send(400, {"error": "нужны хост и существующий приватный ключ"})
            tcp = tcp_check(host, port)
            if not tcp.get("ok"):
                return self._send(200, {"ok": False, "stage": "connect", "tcp": tcp,
                                        "message": "хост недоступен по TCP"})
            before = key_auth(user, host, port, key)
            if before["ok"]:
                return self._send(200, {"ok": True, "stage": "already",
                                        "message": "ключ уже работает — ничего не меняли",
                                        "tcp": tcp, "copy": None, "verify": before})
            copied = install_key(user, host, port, key, password)
            after = key_auth(user, host, port, key)
            ok = after["ok"]
            return self._send(200, {
                "ok": ok,
                "stage": "copied" if ok else "failed",
                "message": "ключ передан и вход по ключу работает" if ok else "передать ключ не удалось",
                "tcp": tcp,
                "copy": {"code": copied["code"], "ms": copied["ms"],
                         "stdout": copied["stdout"][:800], "stderr": copied["stderr"][:800],
                         "added": copied.get("added", False)},
                "verify": {"ok": after["ok"], "ms": after["ms"],
                           "detail": clean_detail(after["stderr"] or after["stdout"])},
            })

        return self._send(404, {"error": "not found"})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"SSH-ключи: {SSH_DIR}")
    print(f"Открыто: {url}")
    if not os.environ.get("SSHKEY_GUI_NO_OPEN"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
