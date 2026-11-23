#!/usr/bin/env python3
"""SSH-ключи: просмотр ключей, проверка связи с сервером, передача публичного ключа.

Запуск:  python3 app.py    (откроет http://127.0.0.1:8765 в браузере)

Только локально: сервер слушает 127.0.0.1 и никуда не ходит, кроме выбранного хоста.
"""

import getpass
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("SSHKEY_GUI_PORT", "8765"))
VERSION = "0.6.2"

SSH_DIR = Path(os.environ.get("SSHKEY_GUI_SSH_DIR", str(Path.home() / ".ssh"))).expanduser()
CONFIG_DIR = Path(os.environ.get("SSHKEY_GUI_CONFIG_DIR", str(Path.home() / ".config" / "sshkey-gui"))).expanduser()
CONFIG_PATH = CONFIG_DIR / "settings.json"

SSH = shutil.which("ssh") or "ssh"
SSH_KEYGEN = shutil.which("ssh-keygen") or "ssh-keygen"

# Общие опции: только выбранный ключ, без перебора ключей из агента
BASE_OPTS = [
    "-o", "IdentitiesOnly=yes",
    "-o", "IdentityAgent=none",
    "-o", "StrictHostKeyChecking=accept-new",
]

KEY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_TYPES = {"ed25519", "rsa", "ecdsa"}


# ---------------------------------------------------------------- настройки

def load_settings():
    data = {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    dirs = data.get("keydirs") if isinstance(data, dict) else None
    clean = []
    if isinstance(dirs, list):
        for item in dirs:
            if isinstance(item, str) and item.strip():
                clean.append(str(Path(item).expanduser()))
    return {"keydirs": clean}


def save_settings(settings):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def key_dirs():
    """Основной каталог ключей плюс дополнительные из настроек (без дублей)."""
    dirs = [SSH_DIR]
    for extra in load_settings()["keydirs"]:
        path = Path(extra)
        if path not in dirs:
            dirs.append(path)
    return dirs


# ---------------------------------------------------------------- ключи

def key_info(pub, base_dir):
    priv = pub.with_suffix("")
    info = {
        "name": pub.stem, "pub": str(pub), "private": str(priv) if priv.exists() else "",
        "dir": str(base_dir), "bits": "", "fingerprint": "", "type": "", "comment": "",
        "usable": priv.exists(),
    }
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
    return info


def list_keys():
    keys = []
    for base in key_dirs():
        if not base.is_dir():
            continue
        for pub in sorted(base.glob("*.pub")):
            keys.append(key_info(pub, base))
    return keys


def create_key(name, key_type, comment, passphrase):
    if not KEY_NAME_RE.match(name or ""):
        return {"ok": False, "error": "Имя ключа: латиница, цифры, точка, дефис и подчёркивание, 1–64 символа."}
    if key_type not in KEY_TYPES:
        return {"ok": False, "error": "Тип ключа: ed25519, rsa или ecdsa."}

    target_dir = SSH_DIR
    path = target_dir / name
    if path.exists() or Path(str(path) + ".pub").exists():
        return {"ok": False, "error": f"Файл уже существует: {name}"}

    cmd = [SSH_KEYGEN, "-q", "-t", key_type, "-f", str(path), "-N", passphrase or ""]
    if comment:
        cmd += ["-C", comment]
    if key_type == "rsa":
        cmd += ["-b", "4096"]

    res = run(cmd, timeout=60)
    if res["code"] != 0:
        return {"ok": False, "error": (res["stderr"] or res["stdout"] or "ssh-keygen завершился с ошибкой")[:400]}
    info = key_info(Path(str(path) + ".pub"), target_dir)
    return {"ok": True, "key": info}


def build_export(include_private=False):
    """Архив: публичные ключи всегда, приватные — по флагу."""
    buf = io.BytesIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        note = (
            "Архив ключей sshkey-gui\n"
            f"Создан: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Приватные ключи: {'да' if include_private else 'нет'}\n\n"
            "Публичные ключи безопасно хранить и передавать.\n"
            "Приватные ключи — это секрет: храните их только в защищённом месте.\n"
        ).encode("utf-8")
        info = tarfile.TarInfo("README.txt")
        info.size = len(note)
        tar.addfile(info, io.BytesIO(note))

        for base in key_dirs():
            if not base.is_dir():
                continue
            for pub in sorted(base.glob("*.pub")):
                tar.add(str(pub), arcname=f"keys/{base.name}/{pub.name}")
                if include_private:
                    priv = pub.with_suffix("")
                    if priv.exists():
                        tar.add(str(priv), arcname=f"keys/{base.name}/{priv.name}")
    return buf.getvalue(), f"sshkey-gui-keys-{stamp}.tar.gz"


# ---------------------------------------------------------------- сеть и ssh

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
    """Кладёт публичный ключ в ~/.ssh/authorized_keys на сервере (без ssh-copy-id)."""
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


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли, без паролей
        print("[gui] " + (fmt % args))

    def _send(self, code, payload, ctype="application/json; charset=utf-8", extra=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        """Только свои адреса — защита от DNS-rebinding."""
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"}

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})

        path, _, query = self.path.partition("?")

        if path in ("/", "/index.html"):
            html = (HERE / "static" / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")

        if path == "/api/keys":
            return self._send(200, {"keys": list_keys(), "home": str(SSH_DIR),
                                    "version": VERSION, "dirs": [str(d) for d in key_dirs()]})

        if path == "/api/settings":
            return self._send(200, {"keydirs": load_settings()["keydirs"], "ssh_dir": str(SSH_DIR),
                                    "config": str(CONFIG_PATH)})

        if path == "/api/export":
            include_private = "private=1" in query
            data, filename = build_export(include_private)
            return self._send(200, data, "application/gzip",
                              {"Content-Disposition": f'attachment; filename="{filename}"'})

        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            return self._send(400, {"error": "bad json"})

        if self.path == "/api/settings":
            dirs = data.get("keydirs")
            if not isinstance(dirs, list):
                return self._send(400, {"error": "keydirs должен быть списком"})
            cleaned, errors = [], []
            for item in dirs:
                if not isinstance(item, str) or not item.strip():
                    continue
                path = Path(item).expanduser()
                if not path.is_absolute():
                    errors.append(f"нужен абсолютный путь: {item}")
                    continue
                if not path.is_dir():
                    errors.append(f"каталог не найден: {item}")
                    continue
                if str(path) not in cleaned:
                    cleaned.append(str(path))
            saved = save_settings({"keydirs": cleaned})
            return self._send(200, {"ok": saved, "keydirs": cleaned, "errors": errors,
                                    "config": str(CONFIG_PATH),
                                    "error": None if saved else "не удалось сохранить настройки"})

        if self.path == "/api/keygen":
            result = create_key(
                (data.get("name") or "").strip(),
                (data.get("type") or "ed25519").strip(),
                (data.get("comment") or "").strip(),
                data.get("passphrase") or "",
            )
            return self._send(200, result)

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
    print(f"SSH-ключи v{VERSION}: {SSH_DIR}")
    print(f"Открыто: {url}")
    if not os.environ.get("SSHKEY_GUI_NO_OPEN"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
