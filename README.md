# CAKRA IoT Gateway

Panel kiosk Python untuk Raspberry Pi 4. Satu panel menaungi beberapa mesin;
**tab mesin dibentuk dari backend** sesuai mesin yang dipilih pada menu
**Master Data & IoT → Gateway / Panel IoT**. Berkas lokal hanya menyimpan hal
yang memang fisik: alamat backend, device token, dan pin sensor counter.

## Alur pemetaan (revisi 16 Okt 2026)

```
Mesin ──(Mesin & Pemeliharaan / Work Center)──► Work Center
  │                                                  ▲
  │                                    Routing PD (Get From ERP, berversi)
  ▼
Gateway / Panel IoT ──(Mapping Operator)──► Operator
```

1. **Mesin & Pemeliharaan / Work Center** — satu work center dapat dikerjakan
   di beberapa mesin, dan satu mesin boleh melayani beberapa work center.
2. **Routing Table** — routing material **berversi**. Saat Get From ERP,
   susunan tahapan PD dibandingkan dengan seluruh versi material; susunan baru
   dicatat sebagai versi 2, 3, dst. Setiap PD tetap memiliki routing sendiri.
3. **Gateway / Panel IoT** — pilih mesin yang dinaungi panel. Tab panel
   mengikuti daftar ini.
4. **Mapping Operator** — beri akses operator ke panel. NPP yang dipindai
   ditolak bila belum berwenang pada panel tersebut.

Saat peti dipindai pada tab mesin, backend memilih **tahapan berikutnya menurut
routing PD peti itu**: tahapan yang work center-nya dilayani mesin dan belum
selesai. Tahapan produksi sebelumnya (selain stasiun QC) wajib sudah
dikonfirmasi untuk peti yang sama. Kolom **PROSES** menampilkan
`0020 REGANG (2/8) · 1115LE01 · routing v2`.

## Menjalankan dengan klik-ganda (Raspberry Pi)

Sekali saja di Pi:

```bash
sudo apt install -y python3-pyqt5 python3-venv
cd ~/cakra-iot-gateway
bash install-shortcut.sh            # ikon "CAKRA Panel IoT" di Desktop + menu
bash install-shortcut.sh --autostart  # opsional: buka otomatis setelah login
```

Setelah itu panel dibuka dengan **klik-ganda ikon CAKRA Panel IoT** di Desktop.
Jalan pertama `run-panel.sh` membuat `.venv` (memakai PyQt5 dari paket OS) dan
memasang panel; jalan berikutnya langsung membuka panel.

Bila `config.json` belum ada, panel membuka **wizard pengaturan awal**: isi
alamat backend, kode gateway, dan device token dari menu Gateway / Panel IoT.
Wizard memvalidasi token ke backend dan mengisi `counters` setiap DAL mesin
panel dengan pin dari `config.example.json`.

### Membuat / melengkapi config.json tanpa wizard

```bash
bash generate-config.sh                      # lengkapi config yang ada (mis. hasil wizard)
bash generate-config.sh --token gw_live_…    # buat config baru
bash generate-config.sh --force              # timpa pin dengan templat
```

Daftar mesin dibaca dari backend; pin diambil dari `config.example.json`
untuk DAL yang dikenal. Pin yang sudah diisi teknisi dipertahankan, dan DAL
tanpa templat ditandai `BELUM ADA PIN`. Tutup panel (tombol ✕), jalankan
generator, lalu buka panel lagi.

### Tampilan

- Seluruh tombol baris atas (tab mesin, ⌨ keyboard, ✕ tutup) setinggi sama.
- Area keyboard di bawah selalu dipesan: membuka keyboard tidak menciutkan
  tabel antrean/operator sehingga posisi input tidak bergeser.
- Jendela kiosk tidak punya tombol minimize/close; tutup aplikasi lewat ✕
  dengan konfirmasi.

### Satu berkas executable (tanpa Python)

```bash
bash build-executable.sh   # menghasilkan dist/cakra-panel (PyInstaller)
bash install-shortcut.sh   # ikon kini menunjuk ke dist/cakra-panel
```

`config.json` dan folder `data/` diletakkan di samping binary. Binary harus
dibangun di Pi itu sendiri karena arsitekturnya mengikuti mesin pembangun.

### Windows (pengujian)

Klik-ganda `run-panel.bat`. Gunakan `"gpio_mode": "mock"`.

## Panel baru, pinout GPIO, dan uji counter

Panduan lengkap: `CONTEXT/06-IoT-APP/01-panduan-pemasangan-panel-dan-gpio.md`.

> ⚠️ GPIO Raspberry Pi **3,3 V, tidak toleran 5 V**. Keluaran optocoupler harus
> menarik pin ke GND (pull-up internal) atau di-pull-up ke 3,3 V — jangan 5 V.

**Pin GPIO per mesin/spur diatur di web** (Gateway / Panel IoT → **Pin GPIO**)
dan berlaku ±10 detik tanpa restart. Pi cukup berisi alamat backend + token:

```bash
sudo apt install -y python3-pyqt5 python3-venv python3-requests python3-rpi-lgpio
bash generate-config.sh --token gw_live_… --gpio real
bash gpio-test.sh            # cek level awal & hitung pulsa per pin (tutup panel dulu)
```

Sumber pin panel: backend → cache SQLite (saat offline) → `counters` di
`config.json` (hanya untuk panel yang pinnya belum diisi di web;
`--pin DAL=PIN`). Pin selalu nomor **BCM**.

## Konfigurasi (`config.json`)

```json
{
  "api_base_url": "http://192.168.196.196:4000",
  "device_token": "gw_live_…",
  "gateway_code": "RPI-INT-01-TEST",
  "gpio_mode": "real",
  "fullscreen": true,
  "counters": [
    {"dal": "I.1", "slots": [{"code": "I.1", "pins": [17, 18]}]},
    {"dal": "I.8-1", "slots": [{"code": "I.8-1-SPUR1", "pins": [5]}, {"code": "I.8-1-SPUR2", "pins": [6]}]}
  ]
}
```

- `counters` dicocokkan ke mesin backend lewat **kode DAL**. Mesin backend
  tanpa entri counter tetap tampil; kartu Spur-nya bernilai `—`.
- Jumlah mesin dan counter **tidak lagi dibatasi** lima mesin/tujuh counter.
- Kunci lama `machines` masih dibaca; `code`/`name` di dalamnya diabaikan —
  kode dan nama mesin selalu dari backend.
- Lokasi config dicari berurutan: `--config`, variabel `CAKRA_PANEL_CONFIG`,
  folder aplikasi, `~/.config/cakra-panel/config.json`, `/etc/cakra/panel.json`.
  Path `data/…` relatif terhadap folder config.
- Token adalah kredensial gateway: simpan dengan permission `0600`, jangan
  dimasukkan Git.

## Fungsi komponen

- **PyQt5 kiosk**: UI native fullscreen yang tetap menghitung saat LAN putus
  dan menerima scanner USB-HID sebagai keyboard wedge.
- **GPIO real/mock**: `real` membaca pin BCM dengan interrupt falling-edge;
  `mock` menjalankan panel di PC tanpa Raspberry Pi.
- **SQLite outbox**: counter dan Finish tertunda tersimpan transaksional.
  Sampel outbox untuk mesin yang tidak lagi dinaungi panel dibuang.
- **CSV recovery**: salinan append-only untuk teknisi.
- **`X-Device-Token`**: identitas gateway tanpa kredensial database.
- **Telemetry 1 Hz idempoten**: UUID per sampel. Backend menyisihkan sampel
  mesin yang tidak dikenal (dilaporkan `unknownMachines`) tanpa menolak
  seluruh kiriman; panel lalu memuat ulang daftar mesin.
- **Satu instans**: kunci OS pada `data/panel.lock` mencegah dua proses
  berebut GPIO; kunci lepas otomatis bila listrik padam.
- **Log**: `data/panel.log` (aplikasi) dan `data/launcher.log` (skrip).

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e .[dev]
.venv/bin/python -m cakra_iot --config config.json --windowed
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
PYTHONPATH=src python tests/render_ui.py .test-tmp/panel.png 0 queue
```

Mode render: `keyboard`, `status-demo`, `partial`, `queue`.

## Layanan systemd (opsional)

`deploy/cakra-iot-panel.service` tetap tersedia bagi Pi yang menjalankan panel
sebagai layanan, alih-alih ikon Desktop. Jangan memakai keduanya bersamaan;
kunci satu-instans akan menolak proses kedua.
