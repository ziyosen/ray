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
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

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


RUN_STARTED_AT = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
MAX_RUN_MINUTES = int(os.environ.get("BANSOS_MAX_RUN_MINUTES", "120"))  # VM max 2 jam per sesi
SESSION_FILE = BASE / "session_state.json"
# Token untuk POST /api/session: dibaca dari env, kalau tidak ada dari file
# session_token (0600, dibuat saat deploy). Token TIDAK pernah dikirim ke
# panel/browser — Muse mendorong sesi lewat exec di VM.
SESSION_TOKEN = os.environ.get("BANSOS_SESSION_TOKEN", "")
if not SESSION_TOKEN:
    try:
        SESSION_TOKEN = (BASE / "session_token").read_text().strip()
    except Exception:
        SESSION_TOKEN = ""

def _load_session_state():
    """Pulihkan run_started_at/max_run_minutes terakhir yang di-push Muse
    (dari Instabox API) supaya restart backend tidak mengembalikan angka basi."""
    global RUN_STARTED_AT, MAX_RUN_MINUTES
    try:
        st = json.load(open(SESSION_FILE))
        if st.get("run_started_at"):
            datetime.datetime.strptime(st["run_started_at"], "%Y-%m-%dT%H:%M:%SZ")
            RUN_STARTED_AT = st["run_started_at"]
        if st.get("max_run_minutes"):
            MAX_RUN_MINUTES = int(st["max_run_minutes"])
    except Exception:
        pass

def _save_session_state():
    try:
        json.dump({"run_started_at": RUN_STARTED_AT, "max_run_minutes": MAX_RUN_MINUTES},
                  open(SESSION_FILE, "w"))
    except Exception:
        pass

def normalize_token(tok):
    """Token akun = 32 hex. Normalisasi input tempelan chat: potong di
    karakter non-hex pertama (mis. ':' dari jam '05:33'), lalu buang
    kelebihan digit di akhir (mis. '05' dari '05:33' yang menempel saat
    Bos menyalin link) -> 32 char pertama."""
    if not tok:
        return ""
    tok = str(tok).strip().lower()
    cut = len(tok)
    for i, ch in enumerate(tok):
        if ch not in "0123456789abcdef":
            cut = i
            break
    tok = tok[:cut]
    if len(tok) > 32:
        tok = tok[:32]
    return tok

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS accounts(
            token TEXT PRIMARY KEY, username TEXT, uuid TEXT,
            trojan_password TEXT, created_at TEXT, status TEXT DEFAULT 'active',
            ip TEXT, used_traffic_bytes INTEGER DEFAULT 0, online_at TEXT)""")
        # Migrasi trafik kumulatif (2026-11-10): simpan baseline counter Xray
        # per akun supaya total di DB tidak pernah turun saat Xray restart.
        # NULL = belum pernah tercatat (migrasi / akun baru).
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        for col in ("last_xray_up", "last_xray_down"):
            if col not in cols:
                c.execute("ALTER TABLE accounts ADD COLUMN %s INTEGER" % col)


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
        # Prune: client 'acc-*' (buatan backend) yang kuncinya sudah tidak
        # ada di DB ikut terhapus (akun yang dihapus tidak lagi bisa konek);
        # client utama (tanpa prefix acc-) selalu dipertahankan.
        kept = [c for c in existing
                if c.get(key) not in db_keys
                and not str(c.get("email") or "").startswith("acc-")]
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


# OPSI B (594cfaa, diadaptasi): Xray jalan di container Docker ray-node-dns,
# bukan binary host — jadi query lewat `docker exec`. Upstream memakai
# `xray api statsquery` langsung (binary host tidak ada di node ini).
XRAY_API = os.environ.get("BANSOS_XRAY_API", "127.0.0.1:10085")

def query_xray_traffic():
    """Query Xray stats API via docker exec. Return {email: {'up': int, 'down': int}}."""
    try:
        r = subprocess.run(["docker", "exec", "ray-node-dns", "xray", "api", "statsquery",
                            f"--server={XRAY_API}", "-pattern", "user>>>"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {}
        out = json.loads(r.stdout)
        result = {}
        for st in out.get("stat", out.get("stats", [])):
            name = st.get("name", "")
            val = int(st.get("value", 0))
            # format: user>>>EMAIL>>>traffic>>>uplink|downlink
            parts = name.split(">>>")
            if len(parts) >= 4 and parts[0] == "user":
                email = parts[1]
                kind = parts[3]
                result.setdefault(email, {"up": 0, "down": 0})
                if kind == "uplink":
                    result[email]["up"] += val
                elif kind == "downlink":
                    result[email]["down"] += val
        return result
    except Exception as e:
        print("[bansos] traffic query failed:", e)
        return {}


def apply_traffic_delta(con, traffic):
    """Terapkan counter Xray ke DB secara KUMULATIF.

    DB adalah sumber kebenaran; counter Xray hanya delta per periode.
    Counter Xray hidup di memori container dan reset ke 0 setiap Xray
    restart — tanpa baseline, total di DB ikut balik ke 0 (user bingung:
    trafik yang sudah dipakai hilang dari tampilan).

    Per akun aktif:
    - baseline NULL + used > 0  -> migrasi: baseline = counter sekarang,
      delta 0 (nilai DB yang sudah ada sudah sama dengan counter).
    - baseline NULL + used == 0 -> akun baru: baseline 0, delta = counter.
    - counter >= baseline       -> delta = counter - baseline (normal).
    - counter < baseline        -> Xray restart di antara refresh:
      delta = counter (trafik sejak restart), baseline direset.
    Total di DB hanya bisa naik, tidak pernah turun.
    """
    for row in con.execute(
            "SELECT token, used_traffic_bytes, last_xray_up, last_xray_down"
            " FROM accounts WHERE status='active'").fetchall():
        email = "acc-" + row["token"][:8]
        t = traffic.get(email)
        if not t:
            continue
        up, down = t["up"], t["down"]
        base_up, base_down = row["last_xray_up"], row["last_xray_down"]
        if base_up is None or base_down is None:
            if (row["used_traffic_bytes"] or 0) > 0:
                con.execute("UPDATE accounts SET last_xray_up=?, last_xray_down=? WHERE token=?",
                            (up, down, row["token"]))
            else:
                con.execute("UPDATE accounts SET used_traffic_bytes=?, last_xray_up=?, last_xray_down=? WHERE token=?",
                            (up + down, up, down, row["token"]))
            continue
        d_up = up - base_up if up >= base_up else up
        d_down = down - base_down if down >= base_down else down
        new_used = (row["used_traffic_bytes"] or 0) + d_up + d_down
        con.execute("UPDATE accounts SET used_traffic_bytes=?, last_xray_up=?, last_xray_down=? WHERE token=?",
                    (new_used, up, down, row["token"]))


def restart_xray():
    if RESTART_CMD:
        os.system(RESTART_CMD)
        return True
    return False


init_db()
_load_session_state()


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
            # refresh traffic dari Xray ke DB (OPSI B, kumulatif — lihat
            # apply_traffic_delta: total tidak pernah turun saat Xray restart)
            traffic = query_xray_traffic()
            if traffic:
                with db() as c:
                    apply_traffic_delta(c, traffic)
            with db() as c:
                n = c.execute("SELECT COUNT(*) n FROM accounts WHERE status='active'").fetchone()["n"]
                mem = os.popen("free -b | awk 'NR==2{print $3\" \"$2}'").read().split()
                mem_used = int(mem[0]) if len(mem) == 2 else 0
                mem_total = int(mem[1]) if len(mem) == 2 else 0
            return self._json(200, {
                "users_active": n, "users_total": n,
                "bandwidth_used_bytes": (lambda c: c.execute("SELECT COALESCE(SUM(used_traffic_bytes),0) s FROM accounts").fetchone()["s"])(c), "bandwidth_limit_bytes": None,
                "mem_used_bytes": mem_used, "mem_total_bytes": mem_total,
                "run_started_at": RUN_STARTED_AT, "max_run_minutes": MAX_RUN_MINUTES,
                "host": HOST, "paths": _paths,
            })
        if p.startswith("/api/accounts/"):
            token = normalize_token(unquote(p.split("/")[-1]))
            with db() as c:
                r = c.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
            if not r or r["status"] != "active":
                return self._json(404, {"ok": False, "error": "akun tidak ditemukan / tidak aktif"})
            links = gen_links(r["uuid"], r["trojan_password"])
            return self._json(200, {"username": r["username"], "status": r["status"],
                "used_traffic_bytes": r["used_traffic_bytes"], "online_at": r["online_at"],
                "uuid": r["uuid"], "trojan_password": r["trojan_password"], "links": links})
        if p.startswith("/t/"):
            return self._html_token(normalize_token(unquote(p.split("/")[-1])))
        if p in ("/", "/index.html"):
            f = BASE / "panel" / "index.html"
            if not f.exists():
                return self._json(404, {"ok": False})
            b = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
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
        if p == "/api/session":
            # Sesi mesin dari Instabox API (didorong Muse via exec di VM).
            # BUKAN localhost-only: cloudflared juga tersambung dari
            # 127.0.0.1, jadi wajib Bearer token dari file session_token.
            if not SESSION_TOKEN:
                return self._json(403, {"ok": False, "error": "session push belum dikonfigurasi"})
            auth = (self.headers.get("Authorization") or "").strip()
            if auth != "Bearer " + SESSION_TOKEN:
                return self._json(403, {"ok": False})
            d = self._body()
            global RUN_STARTED_AT, MAX_RUN_MINUTES
            try:
                rsa = str(d.get("run_started_at") or "")
                datetime.datetime.strptime(rsa, "%Y-%m-%dT%H:%M:%SZ")
                mrm = int(d.get("max_run_minutes"))
                assert 1 <= mrm <= 1440
            except Exception:
                return self._json(400, {"ok": False, "error": "run_started_at (ISO Z) / max_run_minutes (1-1440) tidak valid"})
            RUN_STARTED_AT = rsa
            MAX_RUN_MINUTES = mrm
            _save_session_state()
            return self._json(200, {"ok": True, "run_started_at": RUN_STARTED_AT,
                                    "max_run_minutes": MAX_RUN_MINUTES})
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
