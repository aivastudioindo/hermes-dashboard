# Hermes Profile Skill & Memory Dashboard

Dashboard web ringan (no-build, no-npm) untuk mengelola **skill** dan **memory**
setiap profil Hermes secara realtime dari browser.

- Edit / hapus / nonaktifkan skill & memory per profil
- Pisahkan skill **bawaan** (bundled) vs **buatan** (custom)
- Buat skill baru & memory baru (field standar: `MEMORY.md`, `USER.md`, `NOTES.md`)
- Tampilkan "terakhir diedit X menit lalu" secara live (auto-refresh 20 dtk)
- UI mobile-friendly dengan hamburger navigation + FAB
- 100% Python stdlib (server) + 1 file HTML — **tanpa npm/build**, jalan di Termux/ARM/VPS/Windows/macOS/Linux (platform apa pun yang mendukung Hermes)

---

## Persyaratan

- Python 3.10+ (pakai venv Hermes: `~/hermes-venv/bin/python`, atau python3 biasa)
- Hermes sudah terinstall dan punya struktur `~/.hermes/`
- Tidak butuh pip install apa pun (semua pakai stdlib)

Struktur yang dibaca:
```
~/.hermes/                                    # profil "default"
~/.hermes/skills/<kategori>/<skill>/SKILL.md
~/.hermes/memories/{MEMORY.md,USER.md,NOTES.md,...}
~/.hermes/profiles/<nama>/                    # profil tambahan (dev, brainstorm, dst)
~/.hermes/profiles/<nama>/skills/...
~/.hermes/profiles/<nama>/memories/...
~/.hermes/profiles/<nama>/config.yaml         # berisi skills.disabled
```

## Cara menjalankan

```bash
# dari repo ini
~/hermes-venv/bin/python hermes_dash_server.py
# atau python3 hermes_dash_server.py
```

Buka di browser:
```
http://127.0.0.1:8765/
```

### Akses dari device lain (VPS / remote)
Server bind ke `127.0.0.1` secara default (aman, tidak terbuka ke publik).
Untuk akses remote, salah satu:

1. **SSH tunnel** (paling aman):
   ```bash
   ssh -L 8765:127.0.0.1:8765 user@vps-ip
   ```
   lalu buka `http://127.0.0.1:8765/` di laptop lo.

2. **Bind ke 0.0.0.0** (hanya kalau VPS punya firewall):
   edit baris `ThreadingHTTPServer(("127.0.0.1", PORT), ...)` di `hermes_dash_server.py`
   jadi `("0.0.0.0", PORT)`, lalu pastikan port 8765 di-firewall.

### Port & env
- Port default `8765`, bisa diubah via env: `HERMES_DASHBOARD_PORT=9000 ~/hermes-venv/bin/python hermes_dash_server.py`
- `HERMES_HOME` otomatis diambil dari `~/.hermes` (tidak perlu di-set).

### Jalankan di background (Termux / server)
```bash
nohup ~/hermes-venv/bin/python hermes_dash_server.py > hermes-dashboard.log 2>&1 &
# atau via tmux/systemd di VPS
```

---

## Cara kerja (untuk AI yang diminta menginstall)

Repo ini **self-contained**. Untuk menginstall di environment Hermes baru:

1. **Clone / copy** 2 file ini ke mana pun (mis. `~/hermes-dashboard/`):
   - `hermes_dash_server.py`
   - `hermes-dashboard.html`
2. **Jalankan** server dengan python yang punya akses ke `~/.hermes`:
   ```bash
   python3 hermes_dash_server.py
   ```
3. Buka `http://127.0.0.1:8765/`.

Tidak ada dependency eksternal, tidak ada build step. File HTML di-serve langsung
oleh server (tidak perlu web server terpisah).

### API internal (referensi)
| Endpoint | Fungsi |
|---|---|
| `GET /api/profiles` | daftar profil |
| `GET /api/data?profile=X` | skill + memory profil X |
| `GET /api/raw?profile=X&kind=skill\|memory&name=...` | baca isi file |
| `POST /api/save` | tulis skill/memory (bisa buat baru) |
| `POST /api/delete` | hapus skill/memory (permanen) |
| `POST /api/toggle` | enable/disable skill (ubah `skills.disabled` di config.yaml) |

---

## Catatan keamanan & perilaku

- **Nonaktifkan skill** menulis ke `skills.disabled` di `config.yaml` profil tersebut.
  Agar efek langsung terlihat di Hermes, restart gateway (`hermes gateway restart`).
- **Hapus** = penghapusan permanen (tidak ada trash).
- Field memory standar (`MEMORY.md/USER.md/NOTES.md`) selalu ditampilkan walau file
  belum ada — klik "Buat/Edit" untuk membuatnya.
- Path di-sanitize (tolak `..`, `.lock`, dsb) agar tidak bisa kabur dari folder profil.
- Server hanya bind localhost secara default — tidak mengekspos ke jaringan kecuali
  diubah secara sadar.

## Lisensi
Bebas digunakan & dimodifikasi.
