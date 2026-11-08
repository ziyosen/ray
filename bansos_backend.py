#!/usr/bin/env python3
"""
Bansos Panel Backend — UUID otomatis per user (sesuai docs/bansos-auto-uuid.md).
Stdlib only. SQLite untuk user. Endpoint:
  GET  /api/stats            -> statistik + paths
  POST /api/accounts         -> buat akun baru (1 akun per IP SELAMANYA; uuid4 per user,
                                trojan password acak) -> token pribadi
  GET  /api/accounts/<token> -> detail akun + links
  GET  /t/<token>            -> halaman pribadi user TANPA login (HTML, escape penuh)
IP pendaftar dibaca dari header CF-Connecting-IP (fallback X-Forwarded-For entri pertama,
terakhir client_address) karena semua trafik lewat Cloudflare Tunnel.
Anti-abuse: 1 AKUN PER IP SELAMANYA (env BANSOS_MAX_PER_IP_LIFETIME, default 1).
Tanpa expiry di DB — akun gratis seumur hidup sampai dihapus manual.
Xray: fungsi write_xray_clients MERGE (tidak menimpa clients lama), hanya menyentuh
inbound free yang dicocokkan tag/port persis; restart hanya kalau config benar ditulis.
"""
import base64 as b64
import datetime
import hashlib
import html
import json
import os
import secrets
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
DB_PATH = str(BASE / "bansos.db")
XRAY_CFG = os.environ.get("BANSOS_XRAY_PATH", "")       # path xray_config.json (opsional)
RESTART_CMD = os.environ.get("BANSOS_RESTART_CMD", "")  # restart xray (opsional)
PORT = int(os.environ.get("BANSOS_PORT", "3001"))
HOST = os.environ.get("BANSOS_HOST", "clay.xinquins.de5.net")
# Anti-abuse: 1 akun per IP selamanya (default 1, env override tetap bisa)
MAX_PER_IP_LIFETIME = int(os.environ.get("BANSOS_MAX_PER_IP_LIFETIME", "1"))
TG = os.environ.get("BANSOS_TG", "kontak-telegram-@Bleszh")
MAX_BODY = 8192  # batas body request

# Tag inbound FREE yang boleh disentuh backend (jangan inbound utama!)
FREE_TAGS = set(filter(None, os.environ.get("BANSOS_FREE_TAGS", "vless-free,trojan-free,vmess-free").split(",")))
FREE_PORTS = set(int(p) for p in os.environ.get("BANSOS_FREE_PORTS", "8082,8083,8085").split(",") if p.strip())

_paths = {
    "vless": f"/vless/{TG}", "vless_free": f"/vless/free/{TG}",
    "trojan": f"/trojan/{TG}", "trojan_free": f"/trojan/free/{TG}",
    "vmess": f"/vmess/{TG}", "vmess_free": f"/vmess/free/{TG}",
}

_lock = threading.Lock()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS accounts(
            token TEXT PRIMARY KEY, username TEXT, uuid TEXT,
            trojan_password TEXT, created_at TEXT, status TEXT DEFAULT 'active',
            ip TEXT, used_traffic_bytes INTEGER DEFAULT 0, online_at TEXT)""")


def client_ip(headers, remote_addr):
    """IP asli pendaftar: CF-Connecting-IP > X-Forwarded-For pertama > remote."""
    cf = (headers.get("CF-Connecting-IP") or "").strip()
    if cf:
        return cf
    xff = (headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return remote_addr


def gen_links(u: str, tp: str, sni: str = None):
    """Link akun: port 443 (tunnel cuma expose 443) + path FREE, sesuai panel & docs."""
    sni = sni or HOST
    tgq = TG.replace("@", "%40")
    vless = (f"vless://{u}@{HOST}:443?encryption=none&security=tls&sni={sni}"
             f"&type=ws&host={HOST}&path=%2Fvless%2Ffree%2F{tgq}#TimAI-VLESS")
    trojan = (f"trojan://{tp}@{HOST}:443?security=tls&sni={sni}"
              f"&type=ws&host={HOST}&path=%2Ftrojan%2Ffree%2F{tgq}#TimAI-Trojan")
    vmess_payload = {"v": "2", "ps": "TimAI-VMess", "add": HOST, "port": "443",
                     "id": u, "aid": "0", "net": "ws", "host": HOST,
                     "path": f"/vmess/free/{TG}", "tls": "tls", "sni": sni}
    vmess = "vmess://" + b64.urlsafe_b64encode(json.dumps(vmess_payload).encode()).decode()
    return {"vless": vless, "trojan": trojan, "vmess": vmess}


def write_xray_clients(rows):
    """MERGE: pertahankan clients yang sudah ada (gratisan/utama), tambah/update dari DB.
    Hanya menyentuh inbound FREE (cocok tag ATAU port persis di FREE_TAGS/FREE_PORTS)."""
    if not XRAY_CFG or not os.path.exists(XRAY_CFG):
        return False
    cfg = json.load(open(XRAY_CFG))
    touched = False
    for inbound in cfg.get("inbounds", []):
        tag = inbound.get("tag", "")
        try:
            port = int(inbound.get("port", 0))
        except (TypeError, ValueError):
            port = 0
        if tag not in FREE_TAGS and port not in FREE_PORTS:
            continue  # JANGAN sentuh inbound lain
        settings = inbound.setdefault("settings", {})
        existing = settings.get("clients", []) or []
        db_ids = {r["uuid"] for r in rows}
        db_pws = {r["trojan_password"] for r in rows}
        is_trojan = inbound.get("protocol") == "trojan"
        key = "password" if is_trojan else "id"
        db_keys = db_pws if is_trojan else db_ids
        kept = [c for c in existing if c.get(key) not in db_keys]
        new = ([{"password": r["trojan_password"], "email": "acc-" + r["token"][:8]} for r in rows]
               if is_trojan else
               [{"id": r["uuid"], "email": "acc-" + r["token"][:8]} for r in rows])
        settings["clients"] = kept + new
        touched = True
    if not touched:
        return False
    tmp = XRAY_CFG + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, XRAY_CFG)
        return True
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def restart_xray():
    if RESTART_CMD:
        os.system(RESTART_CMD)
        return True
    return False


init_db()


class H(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature BaseHTTPRequestHandler
        print("[bansos]", format % args)

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n or n > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/stats":
            with db() as c:
                n = c.execute("SELECT COUNT(*) n FROM accounts WHERE status='active'").fetchone()["n"]
                mem = os.popen("free -b | awk 'NR==2{print $3\" \"$2}'").read().split()
                mem_used = int(mem[0]) if len(mem) == 2 else 0
                mem_total = int(mem[1]) if len(mem) == 2 else 0
            return self._json(200, {
                "users_active": n, "users_total": n,
                "bandwidth_used_bytes": 0, "bandwidth_limit_bytes": None,
                "mem_used_bytes": mem_used, "mem_total_bytes": mem_total,
                "run_started_at": None, "max_run_minutes": None,
                "host": HOST, "paths": _paths,
            })
        if p.startswith("/api/accounts/"):
            token = p.split("/")[-1]
            with db() as c:
                r = c.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            if not r or r["status"] != "active":
                return self._json(404, {"ok": False, "error": "akun tidak ditemukan / tidak aktif"})
            links = gen_links(r["uuid"], r["trojan_password"])
            return self._json(200, {"username": r["username"], "status": r["status"],
                "used_traffic_bytes": r["used_traffic_bytes"], "online_at": r["online_at"],
                "uuid": r["uuid"], "trojan_password": r["trojan_password"], "links": links})
        if p.startswith("/t/"):
            return self._html_token(p.split("/")[-1])
        return self._json(404, {"ok": False})

    def _html_token(self, token):
        with db() as c:
            r = c.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        if not r:
            body = b"<html><body><h2>Akun tidak ditemukan</h2></body></html>"
            code = 404
        else:
            links = gen_links(r["uuid"], r["trojan_password"])
            name = html.escape(r["username"] or "pengguna")
            esc = lambda s: html.escape(s, quote=True)
            body = f"""<html><head><meta charset=utf-8><title>Akun TimAI</title>
            <style>body{{font-family:system-ui;background:#0b141a;color:#e9edef;padding:24px;max-width:640px;margin:auto}}
            .c{{background:#202c33;padding:16px;border-radius:12px;margin:12px 0}}code{{word-break:break-all;color:#67e8f9}}</style></head>
            <body><h1>Akun TimAI kamu</h1><p>Halo {name}! Simpan halaman ini (link pribadi, tanpa login).</p>
            <div class=c><b>VLESS</b><br><code>{esc(links['vless'])}</code></div>
            <div class=c><b>Trojan</b><br><code>{esc(links['trojan'])}</code></div>
            <div class=c><b>VMess</b><br><code>{esc(links['vmess'])}</code></div>
            <p style="color:#8696a0;font-size:13px">Traffik: {int(r['used_traffic_bytes'])} bytes &bull; Status: {html.escape(r['status'])}</p></body></html>""".encode()
            code = 200
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/accounts":
            return self._json(404, {"ok": False})
        d = self._body()
        ip = client_ip(self.headers, self.client_address[0])
        with _lock:  # cek batas + insert dalam satu lock (anti race)
            with db() as c:
                n = c.execute("SELECT COUNT(*) n FROM accounts WHERE ip=? AND status='active'",
                              (ip,)).fetchone()["n"]
            if n >= MAX_PER_IP_LIFETIME:
                return self._json(429, {"ok": False,
                    "error": "IP ini sudah pernah membuat akun. 1 akun per orang, gratis seumur hidup."})
            username = (str(d.get("username") or "")[:32]).strip()
            u = str(uuid.uuid4())
            tp = secrets.token_urlsafe(24)
            token = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with db() as c:
                c.execute("INSERT INTO accounts(token,username,uuid,trojan_password,created_at,status,ip,used_traffic_bytes,online_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (token, username, u, tp, now, "active", ip, 0, None))
                rows = [dict(r) for r in c.execute("SELECT * FROM accounts WHERE status='active'").fetchall()]
        try:
            ok = write_xray_clients(rows)
            restarted = restart_xray() if ok else False
        except Exception:
            ok, restarted = False, False
        links = gen_links(u, tp)
        return self._json(200, {"ok": True, "token": token, "username": username,
                                "uuid": u, "trojan_password": tp, "links": links,
                                "xray_updated": ok, "private_page": f"/t/{token}"})


if __name__ == "__main__":
    print(f"Bansos backend on :{PORT} (db: {DB_PATH})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
