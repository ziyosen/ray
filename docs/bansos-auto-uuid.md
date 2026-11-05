# Bansos: UUID Otomatis per Pengguna + Web UI

Rencana bansos (gratisan) VM `node`, domain `clay.xinquins.de5.net`.
Status 2026-11-06: path gratisan sudah live di server (VLESS 8082
`/vless/free/kontak-telegram-@Bleszh`, Trojan 8083
`/trojan/free/kontak-telegram-@Bleszh`), tinggal 2 rule tunnel dari Bos.
Dokumen ini menjawab: bagaimana setiap orang yang ambil akun dapat UUID
sendiri secara otomatis, dan seperti apa web UI-nya.

## Temuan riset (dicek 2026-11-06)

- **Xray-core tidak bisa tambah user tanpa restart.** Tidak ada API
  "tambah client" di Xray-core. Semua panel yang ada bekerja dengan pola
  yang sama: ubah daftar client di `config.json`, lalu restart Xray.
  Restart-nya singkat (~1-2 detik), tapi koneksi yang sedang jalan akan
  putus sesaat. Untuk layanan gratisan, itu masih bisa diterima.
- **UUID per user = satu akun satu identitas.** Kalau satu UUID dipakai
  ramai-ramai, tidak bisa dicabut per orang. Kalau tiap orang punya UUID
  sendiri, akun yang dijual-ulang / disalahgunakan bisa dimatikan tanpa
  mengganggu yang lain.
- **Traffic per user butuh Stats API.** Xray bisa melaporkan traffic per
  user (`user>>>email>>>traffic>>>uplink/downlink`) kalau config
  mengaktifkan `stats` + inbound `api`, dan tiap client diberi `email`.
  Itu tahap 2; tahap 1 cukup tampilkan traffic total dulu.

## Opsi A: panel jadi — Marzban (referensi Bos, 2026-11-06) atau 3x-ui

**Marzban** (`github.com/gozargah/marzban`, referensi yang Bos kirim):
panel Xray berbasis Python + React, 7.5k stars, AGPL-3.0, aktif sejak
2022. Fitur yang relevan buat kita: Web UI bawaan, **REST API lengkap**,
multi-user per inbound, VLESS/Trojan/Shadowsocks, batas traffic +
tanggal kedaluwarsa (+ batas periodik harian/mingguan), subscription
link (V2Ray/Clash), share link + QR generator, monitoring sistem +
statistik traffic, config Xray bisa dikustom, **Telegram Bot terintegrasi**,
CLI. Install satu baris (SQLite) atau Docker, dashboard port 8000.

- Kekuatan vs 3x-ui: lebih matang dan lebih besar, punya REST API dan
  Telegram Bot (nyambung sama jalur kontak Telegram `@Bleszh` kita),
  dan multi-node kalau nanti pindah VPS.
- Kekurangan: ini **panel admin**, bukan halaman publik self-service —
  akun dibuat oleh admin, bukan diambil sendiri oleh pengunjung. Dan
  lebih berat (Python + dashboard React, port sendiri).
- **3x-ui** (`github.com/MHSanaei/3x-ui`): alternatif yang lebih ringan
  (satu binary Go, SQLite), fitur intinya sama (UUID otomatis, QR,
  kuota, statistik per-user), tapi UI generik dan README-nya bilang
  "intended for personal use only".

**Jalan tengah yang paling masuk akal:** pakai Marzban sebagai mesin
manajemen user (dia yang urus config Xray + restart + traffic), lalu
halaman publik "Ambil Akun" yang kecil memanggil **REST API Marzban**
untuk membuat user baru. Hermes jadi tidak perlu menulis ulang logika
config/restart — tinggal bikin halaman ambil akun + dashboard bansos
(RAM, info restart VM, kontributor) di atas API yang sudah jadi.

## Opsi B (direkomendasikan): Marzban + halaman publik kecil, dibangun Hermes

Marzban jalan di VM `node` sebagai container sendiri (port 8000,
diekspos lewat rule tunnel, TLS tetap di Cloudflare). Halaman publik
"Ambil Akun" + dashboard bansos (Node/Python kecil, mis. port 3000)
memanggil REST API Marzban. Muse riset/desain/review, Hermes yang
bangun (sesuai pembagian kerja tim).

### Alur "Ambil Akun" (otomatis)

1. Orang buka web bansos, isi nama / username Telegram (opsional, buat
   batasan anti-abuse), klik **Ambil Akun**.
2. Halaman publik memanggil REST API Marzban untuk membuat user baru
   (Marzban yang generate UUID + atur client di inbound VLESS 8082
   dan Trojan 8083, termasuk restart Xray ~1-2 detik).
3. Web menampilkan link VLESS + link Trojan + QR code, sekali tampil
   (pengguna disuruh simpan). Path keduanya tetap yang gratisan:
   `/vless/free/kontak-telegram-@Bleszh` dan
   `/trojan/free/kontak-telegram-@Bleszh`.
4. Bos bisa menonaktifkan / menghapus satu akun dari dashboard Marzban.
   Akun yang dijual orang lain tinggal dimatikan, yang lain tetap jalan.

### Dashboard publik (sesuai rencana 2026-11-06)

- Status VM: online/offline, sisa RAM, total bandwidth, info restart
  VM (diambil dari API Instabox: `runStartedAt` + batas 8 jam).
- Jumlah akun aktif, traffic total (per-user menyusul tahap 2).
- Seksi kontributor: **Bos (owner), Muse, Hermes** — Bos tidak mau
  diklaim sebagai kerjaan dia sendiri.

### Anti-abuse (penting sebelum dipasarin)

- Batas 1 akun per Telegram / per IP per hari (angka pastinya diputus Bos).
- Akun gratisan tanpa kuota dulu, atau kuota harian kecil — diputus Bos.
- Jangan pernah taruh UUID/password di kode, chat, atau repo. Hanya di
  database VM.
- Admin web pakai password sendiri, jangan pakai password Trojan.

## Keputusan Bos (2026-11-06)

Web UI **seperti Marzban saja** — pakai UI Marzban sebagai dasar,
modifikasi biar keren/kekinian menyusul nanti. Marzban mendukung
kustomisasi tampilan (`CUSTOM_TEMPLATES_DIRECTORY`,
`SUBSCRIPTION_PAGE_TEMPLATE`, `HOME_PAGE_TEMPLATE`), jadi modifikasi
bisa jalan tanpa ganti mesin.

**Keputusan lanjutan (2026-11-06): Marzban TIDAK diinstall — kita
bikin sendiri, contek logikanya.** Logika yang dicontek dari Marzban:
REST API + database SQLite buat user; config Xray di-generate dari
database; tiap tambah/hapus user, tulis ulang config + restart Xray;
traffic per user lewat Stats API Xray; subscription link + QR code;
bot Telegram (opsional). Kode ditulis sendiri (bukan copas) — Marzban
berlisensi AGPL-3.0, jadi meniru alur/ide aman, menyalin kode mentah
akan membawa kewajiban lisensi. Yang dibangun Hermes (Muse review),
disesuaikan dengan setup kita: path WS + path free, tunnel Cloudflare,
batas VM Instabox, dashboard bansos (RAM, info restart, kontributor).

**Visi Bos buat UI-nya (2026-11-06): SATU UI buat user.** Orang yang
make masuk ke panel, ambil akunnya di sana, terus di UI yang sama dia
bisa lihat trafiknya, sisa RAM VM, dan info lainnya. Beda dari
Marzban: di Marzban dashboard cuma buat admin, user cuma dapat link
subscription — di sini user punya halaman sendiri. Cara kerjanya:
habis ambil akun, user dapat link pribadi (token, seperti token
subscription Marzban) buat buka lagi halaman akunnya — tanpa
login/password. Di halaman itu: link VLESS/Trojan + QR, trafik yang
sudah dipakai, kuota kalau ada, plus info VM (sisa RAM, total
bandwidth, info restart). UI publiknya pakai ZEINTHHUB PROJECT
milik Bos (ziyosen.github.io/ui), panel jadi backend-nya.

Catatan deploy: panel sendiri ini yang mengelola Xray (tulis
`xray_config.json` + restart Xray). Pindah ke panel berarti inbound
yang sekarang (VLESS `/`, Trojan `/trojan`, free
`/vless/free/...`, `/trojan/free/...`, VMess `/vmess`, free
`/vmess/free/...`) harus dibuat ulang di dalam panel, plus rule
tunnel untuk dashboard. Jangan deploy diam-diam — rencanakan dulu,
tes dulu, baru pindah.

## Panel UI baru (2026-11-06, `panel/index.html`)

UI baru buatan sendiri, isinya seperti dashboard Marzban: kartu
**Pengguna Aktif**, **Bandwidth Terpakai**, **Memory (RAM)**, dan
**Restart VM Berikutnya** (sesi VM maksimal 2 jam, hitung mundur
dari `run_started_at` + `max_run_minutes`). Di bawahnya ada tombol
**Ambil Akun Gratis** dan tiga kartu akun — **VLESS, Trojan, VMess**
— masing-masing dengan link, tombol salin, dan QR. Habis ambil akun,
user dapat **link pribadi (token)** buat buka lagi halaman akunnya
tanpa login; di halaman itu ada trafik akunnya. Ada juga seksi
**Kontributor: Bos (Owner), Muse (Riset, Desain & Review), Hermes
(Coding & Deploy)**. Tanpa emoji di UI, ikon SVG, tema gelap.

VMess (2026-11-06, atas izin Bos): inbound baru di
`devil-v2ray.sh` — port 8084 path `/vmess` (UUID sama dengan VLESS)
dan free path port 8085 `/vmess/free/kontak-telegram-@Bleszh`.
Butuh 2 rule tunnel lagi dari Bos (`/vmess` → `:8084`,
`/vmess/free/...` → `:8085`, di atas rule `*`). Deploy live di VM
`node` belum jalan — script + UI baru di repo dulu.

### Kontrak API (buat Hermes, backend panel)

- `GET /api/stats` → `{ users_active, users_total,
  bandwidth_used_bytes, bandwidth_limit_bytes, mem_used_bytes,
  mem_total_bytes, run_started_at (ISO), max_run_minutes, host,
  paths: { vless, trojan, vmess, vless_free, trojan_free,
  vmess_free } }`
- `POST /api/accounts` → `{ token, username, uuid,
  trojan_password, links: { vless, trojan, vmess } }`
- `GET /api/accounts/:token` → `{ username, status,
  used_traffic_bytes, online_at, uuid, trojan_password,
  links: { vless, trojan, vmess } }`

UUID dibuat server-side (uuid4, satu UUID per user dipakai VLESS +
VMess; Trojan pakai password acak). Kalau API belum jalan, halaman
tetap tampil dengan tanda "—" dan tombol Ambil Akun memberi tahu
backend belum online — tidak ada data palsu.

1. Domain web bansos: subdomain baru atau path di `clay`?
2. Perlu isi Telegram atau boleh anonim? Batas akun per orang berapa?
3. Kuota traffic per akun atau bebas dulu?

## Langkah berikut

1. Bos jawab 4 pertanyaan di atas.
2. Hermes bangun layanan (Opsi B), Muse review sebelum deploy.
3. Tes di VM `node`: ambil akun baru, konek dari HP, cek restart tidak
   mengganggu akun lama lebih dari 1-2 detik.
4. Tambah rule tunnel web UI, baru umumkan ke publik.

Catatan: push ini atas perintah langsung Bos 2026-11-06
("langsung aja push ke github"). Cross-review Hermes masih terbuka
(bridge down, Bos yang relay).
