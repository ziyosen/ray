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

## Yang masih butuh diputus Bos

1. Domain web bansos: subdomain baru atau path di `clay`?
2. Perlu isi Telegram atau boleh anonim? Batas akun per orang berapa?
3. Kuota traffic per akun atau bebas dulu?
4. Marzban langsung atau halaman custom di atas API-nya? (Rekomendasi:
   Marzban + halaman publik kecil.)

## Langkah berikut

1. Bos jawab 4 pertanyaan di atas.
2. Hermes bangun layanan (Opsi B), Muse review sebelum deploy.
3. Tes di VM `node`: ambil akun baru, konek dari HP, cek restart tidak
   mengganggu akun lama lebih dari 1-2 detik.
4. Tambah rule tunnel web UI, baru umumkan ke publik.

Catatan: push ini atas perintah langsung Bos 2026-11-06
("langsung aja push ke github"). Cross-review Hermes masih terbuka
(bridge down, Bos yang relay).
