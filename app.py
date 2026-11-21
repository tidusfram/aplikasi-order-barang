import os
import re
import sys
import json
import socket
import shutil
import sqlite3
import webbrowser
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Pastikan saat dijadikan .EXE (PyInstaller), lokasi file tetap mengacu ke folder di samping .EXE
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_ITEMS_PATH = os.path.join(BASE_DIR, "inventori.db")
DB_CATEGORIES_PATH = os.path.join(BASE_DIR, "kategori.db")
DB_SETTINGS_PATH = os.path.join(BASE_DIR, "pengaturan.db")
DB_PURCHASE_PATH = os.path.join(BASE_DIR, "purchase.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
STATIC_DIR = os.path.join(BASE_DIR, "static")


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def record_audit(aksi, modul, detail, keterangan=""):
    try:
        conn = sqlite3.connect(DB_SETTINGS_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO audit_logs (waktu, aksi, modul, detail, keterangan)
            VALUES (?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(aksi),
            str(modul),
            str(detail),
            str(keterangan)
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


def verifikasi_pin(pin_input):
    try:
        conn = sqlite3.connect(DB_SETTINGS_PATH)
        c = conn.cursor()
        row = c.execute("SELECT value FROM settings WHERE key = 'pin_supervisor'").fetchone()
        conn.close()
        expected = row[0] if row else "1234"
        return str(pin_input or "").strip() == str(expected).strip()
    except Exception:
        return False


def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)

    # 1. Database Barang (inventori.db)
    conn_items = sqlite3.connect(DB_ITEMS_PATH)
    c_items = conn_items.cursor()
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal_lengkap TEXT NOT NULL DEFAULT '',
            kode_barang TEXT NOT NULL DEFAULT '',
            tanggal INTEGER NOT NULL DEFAULT 1,
            bulan INTEGER NOT NULL DEFAULT 1,
            tahun INTEGER NOT NULL DEFAULT 2026,
            kategori TEXT NOT NULL DEFAULT 'Umum',
            nama_barang TEXT NOT NULL,
            satuan TEXT NOT NULL DEFAULT 'Pcs',
            jenis_transaksi TEXT NOT NULL DEFAULT 'masuk',
            jumlah_sebelumnya INTEGER NOT NULL DEFAULT 0,
            jumlah_order INTEGER NOT NULL DEFAULT 0,
            total_akhir INTEGER NOT NULL DEFAULT 0,
            pemakaian_dimana TEXT NOT NULL DEFAULT '',
            keterangan TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    c_items.execute("PRAGMA table_info(items)")
    existing_cols = {row[1] for row in c_items.fetchall()}
    if "tanggal_lengkap" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN tanggal_lengkap TEXT NOT NULL DEFAULT ''")
        c_items.execute("""
            UPDATE items
            SET tanggal_lengkap = printf('%04d-%02d-%02d', tahun, bulan, tanggal)
            WHERE tanggal_lengkap = ''
        """)
    if "kategori" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN kategori TEXT NOT NULL DEFAULT 'Umum'")
    if "jenis_transaksi" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN jenis_transaksi TEXT NOT NULL DEFAULT 'masuk'")
    if "kode_barang" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN kode_barang TEXT NOT NULL DEFAULT ''")
    if "gudang" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN gudang TEXT NOT NULL DEFAULT 'Gudang ME'")
    if "sub_lokasi" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN sub_lokasi TEXT NOT NULL DEFAULT ''")
    if "tanggal_expired" not in existing_cols:
        c_items.execute("ALTER TABLE items ADD COLUMN tanggal_expired TEXT NOT NULL DEFAULT ''")

    # Master Barang: daftar resmi barang (kode, nama, satuan, kategori, gudang, sub_lokasi, ada_expired, stok_minimum, harga_beli, harga_jual)
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS master_barang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_barang TEXT NOT NULL UNIQUE,
            nama_barang TEXT NOT NULL UNIQUE COLLATE NOCASE,
            gudang TEXT NOT NULL DEFAULT 'Gudang ME',
            sub_lokasi TEXT NOT NULL DEFAULT '',
            ada_expired INTEGER NOT NULL DEFAULT 0,
            kategori TEXT NOT NULL DEFAULT 'Umum',
            satuan TEXT NOT NULL DEFAULT 'Pcs',
            stok_minimum REAL NOT NULL DEFAULT 0,
            harga_beli INTEGER NOT NULL DEFAULT 0,
            harga_jual INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    c_items.execute("PRAGMA table_info(master_barang)")
    mb_cols = {row[1] for row in c_items.fetchall()}
    if "harga_beli" not in mb_cols:
        c_items.execute("ALTER TABLE master_barang ADD COLUMN harga_beli INTEGER NOT NULL DEFAULT 0")
    if "harga_jual" not in mb_cols:
        c_items.execute("ALTER TABLE master_barang ADD COLUMN harga_jual INTEGER NOT NULL DEFAULT 0")
    if "gudang" not in mb_cols:
        c_items.execute("ALTER TABLE master_barang ADD COLUMN gudang TEXT NOT NULL DEFAULT 'Gudang ME'")
    if "sub_lokasi" not in mb_cols:
        c_items.execute("ALTER TABLE master_barang ADD COLUMN sub_lokasi TEXT NOT NULL DEFAULT ''")
    if "ada_expired" not in mb_cols:
        c_items.execute("ALTER TABLE master_barang ADD COLUMN ada_expired INTEGER NOT NULL DEFAULT 0")

    # Otomatis sesuaikan kolom gudang berdasarkan kategori bila masih default
    for tbl in ("items", "master_barang"):
        c_items.execute(f"""
            UPDATE {tbl} SET gudang = 'Gudang IT'
            WHERE (gudang IS NULL OR gudang = '' OR gudang = 'Gudang ME')
              AND (LOWER(kategori) LIKE '%(it)%' OR LOWER(kategori) LIKE '%perangkat it%' OR LOWER(kategori) LIKE '%jaringan%' OR LOWER(kategori) = 'it')
        """)
        c_items.execute(f"""
            UPDATE {tbl} SET gudang = 'Gudang Minuman'
            WHERE (gudang IS NULL OR gudang = '' OR gudang = 'Gudang ME')
              AND (LOWER(kategori) LIKE '%minuman%' OR LOWER(kategori) LIKE '%alkohol%' OR LOWER(kategori) LIKE '%bar%')
        """)
        c_items.execute(f"""
            UPDATE {tbl} SET gudang = 'Gudang Makanan'
            WHERE (gudang IS NULL OR gudang = '' OR gudang = 'Gudang ME')
              AND (LOWER(kategori) LIKE '%makanan%' OR LOWER(kategori) LIKE '%mentah%' OR LOWER(kategori) LIKE '%daging%' OR LOWER(kategori) LIKE '%sayur%' OR LOWER(kategori) LIKE '%bumbu%' OR LOWER(kategori) LIKE '%dry store%')
        """)

    # Tabel F&B: Resep Menu (BOM)
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_menu TEXT NOT NULL UNIQUE,
            nama_menu TEXT NOT NULL,
            kategori TEXT NOT NULL DEFAULT 'Makanan',
            harga_jual REAL NOT NULL DEFAULT 0,
            target_fc REAL NOT NULL DEFAULT 0.3,
            porsi_standar REAL NOT NULL DEFAULT 1,
            keterangan TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    # Tabel F&B: Item Bahan Resep (BOM Items)
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            kode_bahan TEXT NOT NULL DEFAULT '',
            nama_bahan TEXT NOT NULL,
            qty_per_porsi REAL NOT NULL DEFAULT 0,
            satuan TEXT NOT NULL DEFAULT 'gram',
            catatan TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (recipe_id) REFERENCES recipes (id) ON DELETE CASCADE
        )
    """)
    # Tabel F&B: Waste & Susut Bahan
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS waste_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            no_waste TEXT NOT NULL UNIQUE,
            tanggal TEXT NOT NULL,
            gudang TEXT NOT NULL DEFAULT 'Kitchen',
            kode_bahan TEXT NOT NULL DEFAULT '',
            nama_bahan TEXT NOT NULL,
            kategori_waste TEXT NOT NULL DEFAULT 'Rusak/busuk',
            qty REAL NOT NULL DEFAULT 0,
            satuan TEXT NOT NULL DEFAULT 'gram',
            biaya_per_satuan REAL NOT NULL DEFAULT 0,
            kerugian_total REAL NOT NULL DEFAULT 0,
            alasan TEXT NOT NULL DEFAULT '',
            disetujui_oleh TEXT NOT NULL DEFAULT 'Supervisor',
            created_at TEXT NOT NULL
        )
    """)
    # Tabel F&B: Mutasi Antar-Gudang (Inter-Warehouse Transfers)
    c_items.execute("""
        CREATE TABLE IF NOT EXISTS warehouse_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            no_mutasi TEXT NOT NULL UNIQUE,
            tanggal TEXT NOT NULL,
            gudang_asal TEXT NOT NULL,
            gudang_tujuan TEXT NOT NULL,
            kode_bahan TEXT NOT NULL DEFAULT '',
            nama_bahan TEXT NOT NULL,
            qty REAL NOT NULL DEFAULT 0,
            satuan TEXT NOT NULL DEFAULT 'kg',
            status TEXT NOT NULL DEFAULT 'Draft',
            catatan TEXT NOT NULL DEFAULT '',
            posted_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    conn_items.commit()
    conn_items.close()

    # Pre-seed bahan simulasi & resep NUSAERP jika belum ada
    seed_nusaerp_simulasi()

    # Daftarkan otomatis barang lama (dari riwayat transaksi) ke Master Barang
    sinkron_master_barang()

    # 2. Database Kategori Terpisah (kategori.db)
    conn_cat = sqlite3.connect(DB_CATEGORIES_PATH)
    c_cat = conn_cat.cursor()
    c_cat.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama_kategori TEXT NOT NULL UNIQUE,
            deskripsi TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    default_cats = [
        ("Umum", "Barang kebutuhan umum"),
        ("Mekanikal Elektrikal (ME)", "Sparepart, alat teknik, dan kebutuhan ME"),
        ("Perangkat IT & Jaringan", "Komputer, router, CCTV, kabel LAN, perangkat IT"),
        ("Minuman Alkohol & Bar", "Wine, bir, spirit, minuman beralkohol"),
        ("Minuman Non-Alkohol", "Sirup, jus, susu, kopi, teh, soft drink"),
        ("Bahan Makanan - Daging & Seafood", "Daging sapi, ayam, ikan, frozen food (Freezer)"),
        ("Bahan Makanan - Sayur & Segar", "Sayuran, buah, telur, bahan segar (Chiller)"),
        ("Bumbu & Dry Store", "Bumbu dapur, beras, tepung, minyak, bahan kering"),
        ("ATK & Kantor", "Alat tulis dan perlengkapan kantor"),
        ("Kebersihan", "Perlengkapan kebersihan"),
    ]
    c_cat.executemany(
        "INSERT OR IGNORE INTO categories (nama_kategori, deskripsi, created_at) VALUES (?, ?, ?)",
        [(name, desc, now_str) for name, desc in default_cats],
    )
    conn_cat.commit()
    conn_cat.close()

    # 3. Database Pengaturan (pengaturan.db)
    conn_set = sqlite3.connect(DB_SETTINGS_PATH)
    c_set = conn_set.cursor()
    c_set.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Tabel Log Audit untuk mencatat perubahan/penghapusan/opname
    c_set.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            waktu TEXT NOT NULL,
            aksi TEXT NOT NULL,
            modul TEXT NOT NULL,
            detail TEXT NOT NULL,
            keterangan TEXT NOT NULL DEFAULT ''
        )
    """)
    # Tabel Riwayat Stok Opname
    c_set.execute("""
        CREATE TABLE IF NOT EXISTS opname_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT NOT NULL,
            gudang TEXT NOT NULL,
            kode_barang TEXT NOT NULL,
            nama_barang TEXT NOT NULL,
            satuan TEXT NOT NULL,
            stok_sistem REAL NOT NULL,
            stok_fisik REAL NOT NULL,
            selisih REAL NOT NULL,
            alasan TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    # Tabel Master Gudang / Divisi (bisa ditambah, diedit, disesuaikan untuk segala industri)
    c_set.execute("""
        CREATE TABLE IF NOT EXISTS master_gudang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            prefix TEXT NOT NULL,
            sub_lokasi TEXT NOT NULL DEFAULT '',
            wajib_expired INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    default_gudang_awal = [
        ("Gudang ME", "🔧 Gudang ME (Mekanikal Elektrikal)", "ME", "Workshop ME, Rak Sparepart, Gudang Alat, Ruang Mesin / Genset", 0),
        ("Gudang IT", "💻 Gudang IT (Teknologi Informasi)", "IT", "Ruang Server, Lemari Perangkat IT, Rak Kabel & Jaringan, Gudang Sparepart IT", 0),
        ("Gudang Minuman", "🍷 Gudang Minuman (Bar / Beverage)", "BEV", "Gudang Alkohol / Wine, Chiller Bar, Rak Botol / Soft Drink, Gudang Bar Utama", 0),
        ("Gudang Makanan", "🥩 Gudang Makanan (Kitchen / Bahan Mentah)", "FOOD", "Freezer (Daging & Beku), Chiller (Sayur & Segar), Dry Store (Bumbu & Kering), Dapur Utama", 1),
    ]
    now_gud = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for g_nama, g_label, g_pfx, g_sub, g_exp in default_gudang_awal:
        c_set.execute("""
            INSERT OR IGNORE INTO master_gudang (nama, label, prefix, sub_lokasi, wajib_expired, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (g_nama, g_label, g_pfx, g_sub, g_exp, now_gud))
    default_settings = {
        "nama_instansi": "Sistem Input & Order Barang",
        "sub_judul": "Manajemen Stok, Order Masuk (+), Pemakaian Keluar (-) & Lokasi Pemakaian",
        "logo_base64": "",
        "tampilkan_jam": "1",
        "zona_waktu": "WIB",
        "format_jam_24": "1",
        "format_tanggal_export": "DD/MM/YYYY",
        "pin_supervisor": "1234",
    }
    for k, v in default_settings.items():
        c_set.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn_set.commit()
    conn_set.close()

    # 4. Database Purchase / Pembelian Terpisah (purchase.db)
    conn_po = sqlite3.connect(DB_PURCHASE_PATH)
    c_po = conn_po.cursor()
    c_po.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            no_po TEXT NOT NULL DEFAULT '',
            tanggal_lengkap TEXT NOT NULL DEFAULT '',
            tanggal INTEGER NOT NULL DEFAULT 1,
            bulan INTEGER NOT NULL DEFAULT 1,
            tahun INTEGER NOT NULL DEFAULT 2026,
            supplier TEXT NOT NULL DEFAULT '',
            kode_barang TEXT NOT NULL DEFAULT '',
            nama_barang TEXT NOT NULL,
            kategori TEXT NOT NULL DEFAULT 'Umum',
            satuan TEXT NOT NULL DEFAULT 'Pcs',
            jumlah INTEGER NOT NULL DEFAULT 0,
            harga_satuan INTEGER NOT NULL DEFAULT 0,
            total_harga INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'dipesan',
            lokasi_tujuan TEXT NOT NULL DEFAULT 'Gudang Utama',
            keterangan TEXT NOT NULL DEFAULT '',
            stok_masuk_id INTEGER DEFAULT NULL,
            received_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    c_po.execute("PRAGMA table_info(purchases)")
    po_cols = {row[1] for row in c_po.fetchall()}
    if "gudang" not in po_cols:
        c_po.execute("ALTER TABLE purchases ADD COLUMN gudang TEXT NOT NULL DEFAULT 'Gudang ME'")
    if "sub_lokasi" not in po_cols:
        c_po.execute("ALTER TABLE purchases ADD COLUMN sub_lokasi TEXT NOT NULL DEFAULT ''")
    if "tanggal_expired" not in po_cols:
        c_po.execute("ALTER TABLE purchases ADD COLUMN tanggal_expired TEXT NOT NULL DEFAULT ''")
    conn_po.commit()
    conn_po.close()

    create_auto_backup(tag="harian")


def get_daftar_gudang():
    """Ambil daftar gudang aktif dari pengaturan.db (bisa dikelola fleksibel)."""
    try:
        conn = sqlite3.connect(DB_SETTINGS_PATH)
        conn.row_factory = dict_factory
        c = conn.cursor()
        rows = c.execute("SELECT * FROM master_gudang ORDER BY id ASC").fetchall()
        conn.close()
        res = []
        for r in rows:
            subs = [s.strip() for s in str(r.get("sub_lokasi", "")).split(",") if s.strip()]
            res.append({
                "id": r.get("id"),
                "nama": r.get("nama"),
                "label": r.get("label") or r.get("nama"),
                "prefix": r.get("prefix") or "BRG",
                "sub_lokasi": subs,
                "wajib_expired": bool(r.get("wajib_expired")),
            })
        if res:
            return res
    except Exception:
        pass
    return DAFTAR_GUDANG_FALLBACK


DAFTAR_GUDANG_FALLBACK = [
    {
        "nama": "Gudang ME",
        "label": "🔧 Gudang ME (Mekanikal Elektrikal)",
        "prefix": "ME",
        "sub_lokasi": ["Workshop ME", "Rak Sparepart", "Gudang Alat", "Ruang Mesin / Genset"],
        "wajib_expired": False,
    },
    {
        "nama": "Gudang IT",
        "label": "💻 Gudang IT (Teknologi Informasi)",
        "prefix": "IT",
        "sub_lokasi": ["Ruang Server", "Lemari Perangkat IT", "Rak Kabel & Jaringan", "Gudang Sparepart IT"],
        "wajib_expired": False,
    },
    {
        "nama": "Gudang Minuman",
        "label": "🍷 Gudang Minuman (Bar / Beverage)",
        "prefix": "BEV",
        "sub_lokasi": ["Gudang Alkohol / Wine", "Chiller Bar", "Rak Botol / Soft Drink", "Gudang Bar Utama"],
        "wajib_expired": False,
    },
    {
        "nama": "Gudang Makanan",
        "label": "🥩 Gudang Makanan (Kitchen / Bahan Mentah)",
        "prefix": "FOOD",
        "sub_lokasi": ["Freezer (Daging & Beku)", "Chiller (Sayur & Segar)", "Dry Store (Bumbu & Kering)", "Dapur Utama"],
        "wajib_expired": True,
    },
]


def parse_qty(val):
    """Dukung angka bulat maupun desimal (misal 2.5 Kg). Jika bulat (5.0), kembalikan int (5)."""
    try:
        if val is None or val == "":
            return 0
        f = round(float(str(val).replace(",", ".")), 3)
        return int(f) if f.is_integer() else f
    except Exception:
        return 0


def get_lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            ips.add(ip)
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def hitung_total_akhir(jumlah_sebelumnya, jumlah_order, jenis_transaksi):
    seb = parse_qty(jumlah_sebelumnya)
    ord_q = parse_qty(jumlah_order)
    if str(jenis_transaksi).lower() == "keluar":
        return parse_qty(seb - ord_q)
    return parse_qty(seb + ord_q)


def generate_kode_barang(cursor, gudang="Gudang ME"):
    daftar = get_daftar_gudang()
    prefix_map = {g["nama"]: g["prefix"] for g in daftar}
    pfx = prefix_map.get(gudang, "BRG")
    nums = [0]
    for row in cursor.execute("SELECT kode_barang FROM master_barang").fetchall():
        k = row["kode_barang"] if isinstance(row, dict) else row[0]
        m = re.match(rf"^(?:{pfx}|BRG)-(\d+)$", str(k))
        if m:
            nums.append(int(m.group(1)))
    return f"{pfx}-{max(nums) + 1:04d}"


def generate_no_po(cursor, tanggal_str=""):
    try:
        dt = datetime.strptime(tanggal_str, "%Y-%m-%d")
    except Exception:
        dt = datetime.now()
    prefix = f"PO-{dt.strftime('%Y%m')}-"
    nums = [0]
    for row in cursor.execute("SELECT no_po FROM purchases WHERE no_po LIKE ?", (prefix + "%",)).fetchall():
        val = row["no_po"] if isinstance(row, dict) else row[0]
        m = re.match(r"^PO-\d{6}-(\d+)$", str(val))
        if m:
            nums.append(int(m.group(1)))
    return f"{prefix}{max(nums) + 1:03d}"


def sinkron_master_barang():
    """Daftarkan barang dari riwayat transaksi yang belum ada di Master Barang,
    lalu isi kode_barang pada transaksi lama."""
    conn = sqlite3.connect(DB_ITEMS_PATH)
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = c.execute(
        "SELECT nama_barang, kategori, satuan FROM items ORDER BY tanggal_lengkap DESC, id DESC"
    ).fetchall()
    existing = {str(r[0]).strip().lower() for r in c.execute("SELECT nama_barang FROM master_barang")}
    for nama, kat, sat in rows:
        key = str(nama).strip().lower()
        if not key or key in existing:
            continue
        existing.add(key)
        c.execute(
            "INSERT INTO master_barang (kode_barang, nama_barang, kategori, satuan, stok_minimum, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (generate_kode_barang(c), str(nama).strip(), kat or "Umum", sat or "Pcs", now_str),
        )
    c.execute("""
        UPDATE items SET
            kode_barang = COALESCE(
                (SELECT m.kode_barang FROM master_barang m
                 WHERE lower(trim(m.nama_barang)) = lower(trim(items.nama_barang))), kode_barang),
            gudang = COALESCE(
                (SELECT m.gudang FROM master_barang m
                 WHERE lower(trim(m.nama_barang)) = lower(trim(items.nama_barang))), 'Gudang ME')
        WHERE kode_barang = '' OR kode_barang IS NULL
    """)
    conn.commit()
    conn.close()


def hitung_stok_saat_ini(cursor, nama_barang, gudang=None):
    """Ambil stok paling akhir barang. Jika gudang diberikan, filter berdasarkan gudang."""
    if gudang:
        row = cursor.execute(
            "SELECT total_akhir FROM items WHERE lower(trim(nama_barang)) = lower(trim(?)) AND gudang = ? ORDER BY tanggal_lengkap DESC, id DESC LIMIT 1",
            (nama_barang, gudang),
        ).fetchone()
    else:
        row = cursor.execute(
            "SELECT total_akhir FROM items WHERE lower(trim(nama_barang)) = lower(trim(?)) ORDER BY tanggal_lengkap DESC, id DESC LIMIT 1",
            (nama_barang,),
        ).fetchone()
    if row:
        val = row["total_akhir"] if isinstance(row, dict) else row[0]
        return parse_qty(val)
    return 0


def seed_nusaerp_simulasi():
    """Isi simulasi data F&B NUSAERP jika belum ada."""
    conn = None
    try:
        conn = sqlite3.connect(DB_ITEMS_PATH)
        c = conn.cursor()

        # 1. Master Bahan Dasar (Sesuai Panduan NUSAERP)
        bahan_nusaerp = [
            ("BHN001", "Beras", "gram", 15, "Karbohidrat", "Gudang Makanan", "Dry Store", 1000),
            ("BHN002", "Ayam fillet", "gram", 40, "Bahan Makanan - Daging & Seafood", "Gudang Makanan", "Chiller", 500),
            ("BHN003", "Telur", "butir", 2500, "Bahan Makanan - Sayur & Segar", "Gudang Makanan", "Chiller", 30),
            ("BHN004", "Bawang merah", "gram", 30, "Bumbu & Dry Store", "Gudang Makanan", "Dry Store", 200),
            ("BHN005", "Kecap manis", "ml", 20, "Bumbu & Dry Store", "Gudang Makanan", "Dry Store", 500),
            ("BHN006", "Minyak goreng", "ml", 18, "Bumbu & Dry Store", "Gudang Makanan", "Dry Store", 1000),
            ("BHN007", "Sayuran campur", "gram", 20, "Bahan Makanan - Sayur & Segar", "Gudang Makanan", "Chiller", 300),
            ("BHN008", "Bumbu lain", "paket", 500, "Bumbu & Dry Store", "Gudang Makanan", "Dry Store", 50),
        ]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for kode, nama, sat, hrg, kat, gud, sub, s_min in bahan_nusaerp:
            exist = c.execute("SELECT id FROM master_barang WHERE kode_barang = ? OR lower(trim(nama_barang)) = lower(trim(?))", (kode, nama)).fetchone()
            if not exist:
                c.execute("""
                    INSERT INTO master_barang (kode_barang, nama_barang, gudang, sub_lokasi, ada_expired, kategori, satuan, stok_minimum, harga_beli, harga_jual, created_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 0, ?)
                """, (kode, nama, gud, sub, kat, sat, s_min, hrg, now_str))

        # 2. Resep Menu: MNU001 - Nasi Goreng Spesial
        exist_rcp = c.execute("SELECT id FROM recipes WHERE kode_menu = 'MNU001'").fetchone()
        if not exist_rcp:
            c.execute("""
                INSERT INTO recipes (kode_menu, nama_menu, kategori, harga_jual, target_fc, porsi_standar, keterangan, created_at)
                VALUES ('MNU001', 'Nasi Goreng Spesial', 'Makanan', 35000, 0.30, 1, 'Resep standar simulasi NUSAERP F&B', ?)
            """, (now_str,))
            rcp_id = c.lastrowid
            resep_items = [
                ("BHN001", "Beras", 100, "gram", "Beras pulen"),
                ("BHN002", "Ayam fillet", 50, "gram", "Potong dadu"),
                ("BHN003", "Telur", 1, "butir", "Telur ayam"),
                ("BHN004", "Bawang merah", 10, "gram", "Iris halus"),
                ("BHN005", "Kecap manis", 15, "ml", "Kecap manis bango/sedap"),
                ("BHN006", "Minyak goreng", 10, "ml", "Minyak sawit"),
                ("BHN007", "Sayuran campur", 30, "gram", "Wortel, buncis, jagung"),
                ("BHN008", "Bumbu lain", 1, "paket", "Garam, lada, bumbu racik"),
            ]
            for kb, nb, qty, sat, cat in resep_items:
                c.execute("""
                    INSERT INTO recipe_items (recipe_id, kode_bahan, nama_bahan, qty_per_porsi, satuan, catatan)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (rcp_id, kb, nb, qty, sat, cat))

        # 3. Contoh Data Waste Awal jika masih kosong
        cnt_w = c.execute("SELECT COUNT(*) FROM waste_records").fetchone()[0]
        if cnt_w == 0:
            sample_waste = [
                ("WST-0001", "2026-11-10", "Chiller", "BHN002", "Ayam fillet", "Rusak/busuk", 2000, "gram", 40, 80000, "Kemasan bocor", "Supervisor"),
                ("WST-0002", "2026-11-11", "Kitchen", "BHN007", "Sayuran campur", "Preparation waste", 500, "gram", 20, 10000, "Trim sayuran layu", "Chef"),
                ("WST-0003", "2026-11-12", "Freezer", "BHN002", "Ayam fillet", "Expired", 1000, "gram", 40, 40000, "Lewat tanggal kadaluwarsa", "Supervisor"),
            ]
            for nw, tgl, gd, kb, nb, kat, qty, sat, hrg, rugi, als, acc in sample_waste:
                c.execute("""
                    INSERT INTO waste_records (no_waste, tanggal, gudang, kode_bahan, nama_bahan, kategori_waste, qty, satuan, biaya_per_satuan, kerugian_total, alasan, disetujui_oleh, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nw, tgl, gd, kb, nb, kat, qty, sat, hrg, rugi, als, acc, now_str))

        # 4. Contoh Mutasi Awal jika masih kosong
        cnt_m = c.execute("SELECT COUNT(*) FROM warehouse_transfers").fetchone()[0]
        if cnt_m == 0:
            sample_mutasi = [
                ("MUT-0001", "2026-11-13", "Gudang Makanan", "Gudang Makanan (Kitchen)", "BHN002", "Ayam fillet", 10000, "gram", "Posted", "Transfer untuk produksi hari ini", now_str),
                ("MUT-0002", "2026-11-14", "Gudang Makanan", "Gudang Makanan (Chiller)", "BHN002", "Ayam fillet", 5000, "gram", "Draft", "Draft transfer persiapan akhir pekan", ""),
            ]
            for nm, tgl, ga, gt, kb, nb, qty, sat, st, cat, pst in sample_mutasi:
                c.execute("""
                    INSERT INTO warehouse_transfers (no_mutasi, tanggal, gudang_asal, gudang_tujuan, kode_bahan, nama_bahan, qty, satuan, status, catatan, posted_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nm, tgl, ga, gt, kb, nb, qty, sat, st, cat, pst, now_str))

        conn.commit()
    except Exception as e:
        print(f"Error seeding NUSAERP: {e}")
    finally:
        if conn:
            conn.close()


def find_master_barang(cursor, nama):
    return cursor.execute(
        "SELECT * FROM master_barang WHERE lower(trim(nama_barang)) = lower(trim(?))", (nama,)
    ).fetchone()


def get_master_list(filter_gudang=None):
    conn = sqlite3.connect(DB_ITEMS_PATH)
    conn.row_factory = dict_factory
    c = conn.cursor()
    if filter_gudang:
        master = c.execute(
            "SELECT * FROM master_barang WHERE gudang = ? ORDER BY nama_barang COLLATE NOCASE ASC",
            (filter_gudang,),
        ).fetchall()
    else:
        master = c.execute("SELECT * FROM master_barang ORDER BY nama_barang COLLATE NOCASE ASC").fetchall()

    latest = {}
    for r in c.execute("SELECT nama_barang, total_akhir FROM items ORDER BY tanggal_lengkap DESC, id DESC").fetchall():
        key = str(r["nama_barang"]).strip().lower()
        if key not in latest:
            latest[key] = parse_qty(r["total_akhir"])

    # Cari tanggal expired terdekat (FEFO) dari transaksi masuk yang memiliki tanggal_expired
    exp_rows = c.execute(
        "SELECT nama_barang, MIN(tanggal_expired) AS min_exp "
        "FROM items WHERE jenis_transaksi = 'masuk' AND tanggal_expired != '' "
        "GROUP BY lower(trim(nama_barang))"
    ).fetchall()
    exp_map = {str(r["nama_barang"]).strip().lower(): r["min_exp"] for r in exp_rows}
    conn.close()

    today_date = datetime.now().date()
    for m in master:
        key = m["nama_barang"].strip().lower()
        stok = parse_qty(latest.get(key, 0))
        minimum = parse_qty(m["stok_minimum"] or 0)
        if stok < 0:
            status = "minus"
        elif stok == 0:
            status = "habis"
        elif minimum > 0 and stok <= minimum:
            status = "menipis"
        else:
            status = "aman"
        m["stok_saat_ini"] = stok
        m["stok_minimum"] = minimum
        m["status"] = status

        exp_str = exp_map.get(key, "") if stok > 0 else ""
        m["expired_terdekat"] = exp_str
        m["sisa_hari_expired"] = None
        m["status_expired"] = "aman"
        if exp_str:
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
                sisa = (exp_dt - today_date).days
                m["sisa_hari_expired"] = sisa
                if sisa < 0:
                    m["status_expired"] = "expired"
                elif sisa <= 7:
                    m["status_expired"] = "hampir_expired"
            except Exception:
                pass
    return master


def get_full_backup_payload():
    conn_items = sqlite3.connect(DB_ITEMS_PATH)
    conn_items.row_factory = dict_factory
    items = conn_items.execute("SELECT * FROM items ORDER BY id ASC").fetchall()
    master_barang = conn_items.execute("SELECT * FROM master_barang ORDER BY id ASC").fetchall()
    conn_items.close()

    conn_cat = sqlite3.connect(DB_CATEGORIES_PATH)
    conn_cat.row_factory = dict_factory
    categories = conn_cat.execute("SELECT * FROM categories ORDER BY id ASC").fetchall()
    conn_cat.close()

    conn_set = sqlite3.connect(DB_SETTINGS_PATH)
    conn_set.row_factory = dict_factory
    settings_rows = conn_set.execute("SELECT key, value FROM settings").fetchall()
    master_gudang = conn_set.execute("SELECT * FROM master_gudang ORDER BY urutan ASC, id ASC").fetchall()
    opname_history = conn_set.execute("SELECT * FROM opname_history ORDER BY id ASC").fetchall()
    audit_logs = conn_set.execute("SELECT * FROM audit_logs ORDER BY id ASC").fetchall()
    conn_set.close()
    settings = {r["key"]: r["value"] for r in settings_rows}

    conn_po = sqlite3.connect(DB_PURCHASE_PATH)
    conn_po.row_factory = dict_factory
    purchases = conn_po.execute("SELECT * FROM purchases ORDER BY id ASC").fetchall()
    conn_po.close()

    return {
        "backup_version": "1.3",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "master_barang": master_barang,
        "master_gudang": master_gudang,
        "opname_history": opname_history,
        "audit_logs": audit_logs,
        "categories": categories,
        "purchases": purchases,
        "settings": settings,
    }


def create_auto_backup(tag="auto"):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        for db_file, prefix in [
            (DB_ITEMS_PATH, "inventori"),
            (DB_CATEGORIES_PATH, "kategori"),
            (DB_SETTINGS_PATH, "pengaturan"),
            (DB_PURCHASE_PATH, "purchase"),
        ]:
            if os.path.exists(db_file):
                shutil.copy2(db_file, os.path.join(BACKUP_DIR, f"{prefix}_{date_str}.db"))

        payload = get_full_backup_payload()
        backup_json_path = os.path.join(BACKUP_DIR, f"backup_lengkap_{date_str}.json")
        with open(backup_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return backup_json_path
    except Exception:
        return None


def list_local_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = []
    for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if fname.endswith(".json") or fname.endswith(".db"):
            fpath = os.path.join(BACKUP_DIR, fname)
            stat = os.stat(fpath)
            files.append({
                "filename": fname,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return files


def restore_from_payload(payload):
    items = payload.get("items")
    categories = payload.get("categories")
    settings = payload.get("settings")

    if items is not None:
        conn_items = sqlite3.connect(DB_ITEMS_PATH)
        c_items = conn_items.cursor()
        c_items.execute("DELETE FROM items")
        c_items.execute("DELETE FROM master_barang")
        for mb in (payload.get("master_barang") or []):
            gudang_mb = mb.get("gudang", "Gudang ME") or "Gudang ME"
            c_items.execute(
                "INSERT OR IGNORE INTO master_barang (kode_barang, nama_barang, gudang, sub_lokasi, ada_expired, kategori, satuan, stok_minimum, harga_beli, harga_jual, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mb.get("kode_barang") or generate_kode_barang(c_items, gudang_mb),
                    mb.get("nama_barang", ""),
                    gudang_mb,
                    mb.get("sub_lokasi", ""),
                    int(mb.get("ada_expired", 1 if gudang_mb == "Gudang Makanan" else 0) or 0),
                    mb.get("kategori", "Umum"),
                    mb.get("satuan", "Pcs"),
                    parse_qty(mb.get("stok_minimum", 0)),
                    int(mb.get("harga_beli", 0) or 0),
                    int(mb.get("harga_jual", 0) or 0),
                    mb.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )
        for it in items:
            tgl_str = it.get("tanggal_lengkap") or f"{it.get('tahun', 2026):04d}-{int(it.get('bulan', 1)):02d}-{int(it.get('tanggal', 1)):02d}"
            tgl_lengkap, tgl, bln, thn = parse_date_parts(tgl_str)
            jenis = it.get("jenis_transaksi", "masuk")
            seb = parse_qty(it.get("jumlah_sebelumnya", 0))
            ord_qty = parse_qty(it.get("jumlah_order", 0))
            tot = parse_qty(it.get("total_akhir", hitung_total_akhir(seb, ord_qty, jenis)))
            c_items.execute(
                """
                INSERT INTO items (
                    tanggal_lengkap, tanggal, bulan, tahun, kategori,
                    nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                    total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang,
                    gudang, sub_lokasi, tanggal_expired
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tgl_lengkap, tgl, bln, thn,
                    it.get("kategori", "Umum"),
                    it.get("nama_barang", ""),
                    it.get("satuan", "Pcs"),
                    jenis,
                    seb,
                    ord_qty,
                    tot,
                    it.get("pemakaian_dimana", ""),
                    it.get("keterangan", ""),
                    it.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    it.get("kode_barang", "") or "",
                    it.get("gudang", "Gudang ME") or "Gudang ME",
                    it.get("sub_lokasi", "") or "",
                    it.get("tanggal_expired", "") or "",
                ),
            )
        conn_items.commit()
        conn_items.close()
        sinkron_master_barang()

    if categories is not None:
        conn_cat = sqlite3.connect(DB_CATEGORIES_PATH)
        c_cat = conn_cat.cursor()
        c_cat.execute("DELETE FROM categories")
        for cat in categories:
            c_cat.execute(
                "INSERT OR IGNORE INTO categories (nama_kategori, deskripsi, created_at) VALUES (?, ?, ?)",
                (
                    cat.get("nama_kategori", "Umum"),
                    cat.get("deskripsi", ""),
                    cat.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )
        conn_cat.commit()
        conn_cat.close()

    if settings is not None and isinstance(settings, dict):
        conn_set = sqlite3.connect(DB_SETTINGS_PATH)
        c_set = conn_set.cursor()
        for k, v in settings.items():
            c_set.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(k), str(v)),
            )
        if payload.get("master_gudang") is not None:
            c_set.execute("DELETE FROM master_gudang")
            for idx, mg in enumerate(payload.get("master_gudang") or [], start=1):
                c_set.execute(
                    "INSERT OR IGNORE INTO master_gudang (nama, label, prefix, sub_lokasi, wajib_expired, urutan) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        mg.get("nama", ""),
                        mg.get("label", mg.get("nama", "")),
                        mg.get("prefix", "BRG"),
                        mg.get("sub_lokasi", ""),
                        int(mg.get("wajib_expired", 0) or 0),
                        int(mg.get("urutan", idx) or idx),
                    ),
                )
        if payload.get("opname_history") is not None:
            c_set.execute("DELETE FROM opname_history")
            for op in (payload.get("opname_history") or []):
                c_set.execute(
                    "INSERT INTO opname_history (tanggal, gudang, kode_barang, nama_barang, satuan, stok_sistem, stok_fisik, selisih, alasan, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        op.get("tanggal", ""),
                        op.get("gudang", "Gudang ME"),
                        op.get("kode_barang", ""),
                        op.get("nama_barang", ""),
                        op.get("satuan", "Pcs"),
                        parse_qty(op.get("stok_sistem", 0)),
                        parse_qty(op.get("stok_fisik", 0)),
                        parse_qty(op.get("selisih", 0)),
                        op.get("alasan", ""),
                        op.get("created_at", ""),
                    ),
                )
        if payload.get("audit_logs") is not None:
            c_set.execute("DELETE FROM audit_logs")
            for al in (payload.get("audit_logs") or []):
                c_set.execute(
                    "INSERT INTO audit_logs (waktu, aksi, modul, detail, keterangan) VALUES (?, ?, ?, ?, ?)",
                    (
                        al.get("waktu", ""),
                        al.get("aksi", ""),
                        al.get("modul", ""),
                        al.get("detail", ""),
                        al.get("keterangan", ""),
                    ),
                )
        conn_set.commit()
        conn_set.close()

    purchases = payload.get("purchases")
    if purchases is not None:
        conn_po = sqlite3.connect(DB_PURCHASE_PATH)
        c_po = conn_po.cursor()
        c_po.execute("DELETE FROM purchases")
        for po in purchases:
            tgl_lengkap, tgl, bln, thn = parse_date_parts(po.get("tanggal_lengkap", ""))
            jml = parse_qty(po.get("jumlah", 0))
            hrg = int(po.get("harga_satuan", 0) or 0)
            tot = int(round(jml * hrg))
            c_po.execute(
                """
                INSERT INTO purchases (
                    no_po, tanggal_lengkap, tanggal, bulan, tahun, supplier,
                    kode_barang, nama_barang, kategori, satuan, jumlah,
                    harga_satuan, total_harga, status, lokasi_tujuan, keterangan,
                    stok_masuk_id, received_at, created_at,
                    gudang, sub_lokasi, tanggal_expired
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    po.get("no_po") or generate_no_po(c_po, tgl_lengkap),
                    tgl_lengkap, tgl, bln, thn,
                    po.get("supplier", ""),
                    po.get("kode_barang", ""),
                    po.get("nama_barang", ""),
                    po.get("kategori", "Umum"),
                    po.get("satuan", "Pcs"),
                    jml, hrg, tot,
                    po.get("status", "dipesan"),
                    po.get("lokasi_tujuan", "Gudang ME"),
                    po.get("keterangan", ""),
                    po.get("stok_masuk_id"),
                    po.get("received_at", ""),
                    po.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    po.get("gudang", "Gudang ME") or "Gudang ME",
                    po.get("sub_lokasi", "") or "",
                    po.get("tanggal_expired", "") or "",
                ),
            )
        conn_po.commit()
        conn_po.close()


def parse_date_parts(tanggal_str):
    try:
        dt = datetime.strptime(tanggal_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d"), dt.day, dt.month, dt.year
    except Exception:
        now = datetime.now()
        return now.strftime("%Y-%m-%d"), now.day, now.month, now.year


def catat_barang_masuk_dari_po(po):
    """Saat PO diterima, otomatis tambahkan transaksi masuk (+) ke inventori.db
    dan perbarui harga_beli terakhir di master_barang."""
    conn = sqlite3.connect(DB_ITEMS_PATH)
    conn.row_factory = dict_factory
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tgl_str = po.get("tanggal_lengkap") or datetime.now().strftime("%Y-%m-%d")
    tgl_lengkap, tgl, bln, thn = parse_date_parts(tgl_str)

    nama = str(po.get("nama_barang", "")).strip()
    master = find_master_barang(c, nama)
    if not master:
        kode = po.get("kode_barang") or generate_kode_barang(c)
        c.execute(
            "INSERT INTO master_barang (kode_barang, nama_barang, kategori, satuan, stok_minimum, harga_beli, harga_jual, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?, 0, ?)",
            (kode, nama, po.get("kategori", "Umum"), po.get("satuan", "Pcs"), int(po.get("harga_satuan", 0) or 0), now_str),
        )
        master = find_master_barang(c, nama)
    else:
        if int(po.get("harga_satuan", 0) or 0) > 0:
            c.execute(
                "UPDATE master_barang SET harga_beli = ? WHERE id = ?",
                (int(po.get("harga_satuan", 0)), master["id"]),
            )

    last_row = c.execute(
        "SELECT total_akhir FROM items WHERE lower(trim(nama_barang)) = lower(trim(?)) ORDER BY tanggal_lengkap DESC, id DESC LIMIT 1",
        (master["nama_barang"],),
    ).fetchone()
    stok_seb = parse_qty(last_row["total_akhir"]) if last_row else 0
    jml_masuk = parse_qty(po.get("jumlah", 0))
    total_akhir = parse_qty(stok_seb + jml_masuk)

    gudang = po.get("gudang") or master.get("gudang") or "Gudang ME"
    sub_lokasi = po.get("sub_lokasi") or master.get("sub_lokasi") or ""
    tgl_exp = po.get("tanggal_expired") or ""

    ket_po = f"Terima dari Purchase No. {po.get('no_po', '')} | Supplier: {po.get('supplier', '-')}"
    if po.get("keterangan"):
        ket_po += f" ({po.get('keterangan')})"

    c.execute(
        """
        INSERT INTO items (
            tanggal_lengkap, tanggal, bulan, tahun, kategori,
            nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
            total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang,
            gudang, sub_lokasi, tanggal_expired
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'masuk', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tgl_lengkap, tgl, bln, thn,
            master["kategori"], master["nama_barang"], master["satuan"],
            stok_seb, jml_masuk, total_akhir,
            po.get("lokasi_tujuan") or f"{gudang}" + (f" - {sub_lokasi}" if sub_lokasi else ""),
            ket_po, now_str, master["kode_barang"],
            gudang, sub_lokasi, tgl_exp,
        ),
    )
    stok_id = c.lastrowid
    conn.commit()
    conn.close()
    return stok_id


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path.rstrip("/") or "/"

        # GET /api/network-info (Info IP LAN/Wi-Fi & Daftar Gudang + Sub-Lokasi)
        if clean_path == "/api/network-info":
            port = self.server.server_port if hasattr(self, "server") and self.server else 8090
            lan_ips = get_lan_ips()
            self._send_json({
                "port": port,
                "lan_ips": lan_ips,
                "lan_urls": [f"http://{ip}:{port}" for ip in lan_ips],
                "urls": [f"http://{ip}:{port}" for ip in lan_ips],
                "gudang_list": get_daftar_gudang(),
            })
            return

        # GET /api/master-gudang (Daftar semua gudang/divisi aktif)
        if clean_path == "/api/master-gudang":
            self._send_json({"gudang_list": get_daftar_gudang()})
            return

        # GET /api/items
        if clean_path == "/api/items":
            params = parse_qs(parsed.query)
            bulan = params.get("bulan", [None])[0]
            tahun = params.get("tahun", [None])[0]
            kategori = params.get("kategori", [None])[0]
            gudang = params.get("gudang", [None])[0]
            jenis = params.get("jenis", [None])[0]
            q = params.get("q", [None])[0]

            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()

            query = "SELECT * FROM items WHERE 1=1"
            args = []

            if bulan and bulan.isdigit():
                query += " AND bulan = ?"
                args.append(int(bulan))
            if tahun and tahun.isdigit():
                query += " AND tahun = ?"
                args.append(int(tahun))
            if kategori:
                query += " AND kategori = ?"
                args.append(kategori)
            if gudang:
                query += " AND gudang = ?"
                args.append(gudang)
            if jenis in ("masuk", "keluar"):
                query += " AND jenis_transaksi = ?"
                args.append(jenis)
            if q:
                query += " AND (nama_barang LIKE ? OR kode_barang LIKE ? OR pemakaian_dimana LIKE ? OR sub_lokasi LIKE ? OR keterangan LIKE ? OR kategori LIKE ?)"
                like_q = f"%{q}%"
                args.extend([like_q, like_q, like_q, like_q, like_q, like_q])

            query += " ORDER BY tanggal_lengkap DESC, id DESC"
            cursor.execute(query, args)
            rows = cursor.fetchall()
            for r in rows:
                r["jumlah_sebelumnya"] = parse_qty(r.get("jumlah_sebelumnya", 0))
                r["jumlah_order"] = parse_qty(r.get("jumlah_order", 0))
                r["total_akhir"] = parse_qty(r.get("total_akhir", 0))
            conn.close()
            self._send_json({"items": rows})
            return

        # GET /api/stock-summary (Stok terakhir tiap nama barang)
        if clean_path == "/api/stock-summary":
            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            # Ambil record paling akhir (berdasarkan tanggal_lengkap DESC, id DESC) untuk setiap nama_barang
            cursor.execute("""
                SELECT nama_barang, kategori, satuan, total_akhir, tanggal_lengkap, id
                FROM items
                ORDER BY tanggal_lengkap DESC, id DESC
            """)
            all_rows = cursor.fetchall()
            conn.close()

            latest_by_name = {}
            for r in all_rows:
                key = r["nama_barang"].strip().lower()
                if key and key not in latest_by_name:
                    latest_by_name[key] = r

            self._send_json({"stocks": list(latest_by_name.values())})
            return

        # GET /api/master-barang (Master Barang + stok saat ini + status)
        if clean_path == "/api/master-barang":
            params = parse_qs(parsed.query)
            gudang = params.get("gudang", [None])[0]
            self._send_json({"barang": get_master_list(gudang), "gudang_list": get_daftar_gudang()})
            return

        # GET /api/purchases (Daftar Purchase Order dari purchase.db)
        if clean_path == "/api/purchases":
            params = parse_qs(parsed.query)
            bulan = params.get("bulan", [None])[0]
            tahun = params.get("tahun", [None])[0]
            kategori = params.get("kategori", [None])[0]
            gudang = params.get("gudang", [None])[0]
            status = params.get("status", [None])[0]
            q = params.get("q", [None])[0]

            conn = sqlite3.connect(DB_PURCHASE_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            query = "SELECT * FROM purchases WHERE 1=1"
            args = []
            if bulan and bulan.isdigit():
                query += " AND bulan = ?"
                args.append(int(bulan))
            if tahun and tahun.isdigit():
                query += " AND tahun = ?"
                args.append(int(tahun))
            if kategori:
                query += " AND kategori = ?"
                args.append(kategori)
            if gudang:
                query += " AND gudang = ?"
                args.append(gudang)
            if status in ("pengajuan", "dipesan", "diterima", "batal"):
                query += " AND status = ?"
                args.append(status)
            if q:
                query += " AND (no_po LIKE ? OR supplier LIKE ? OR nama_barang LIKE ? OR kode_barang LIKE ? OR sub_lokasi LIKE ? OR keterangan LIKE ?)"
                lq = f"%{q}%"
                args.extend([lq, lq, lq, lq, lq, lq])
            query += " ORDER BY tanggal_lengkap DESC, id DESC"
            cursor.execute(query, args)
            rows = cursor.fetchall()
            for r in rows:
                r["jumlah"] = parse_qty(r.get("jumlah", 0))
            conn.close()
            self._send_json({"purchases": rows})
            return

        # GET /api/categories
        if clean_path == "/api/categories":
            conn = sqlite3.connect(DB_CATEGORIES_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories ORDER BY nama_kategori ASC")
            rows = cursor.fetchall()
            conn.close()
            self._send_json({"categories": rows})
            return

        # ==================== F&B GET ENDPOINTS ====================
        # GET /api/recipes (Daftar Resep & Perhitungan Food Cost)
        if clean_path == "/api/recipes":
            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            c = conn.cursor()
            recipes = c.execute("SELECT * FROM recipes ORDER BY nama_menu ASC").fetchall()
            # Ambil master barang harga beli untuk kalkulasi food cost realtime
            master_harga = {str(m["nama_barang"]).strip().lower(): m["harga_beli"] for m in c.execute("SELECT nama_barang, harga_beli FROM master_barang").fetchall()}

            for r in recipes:
                items = c.execute("SELECT * FROM recipe_items WHERE recipe_id = ? ORDER BY id ASC", (r["id"],)).fetchall()
                total_biaya_bahan = 0
                for it in items:
                    kb = str(it["nama_bahan"]).strip().lower()
                    hrg = master_harga.get(kb, 0)
                    it["harga_satuan"] = hrg
                    it["biaya_bahan"] = round(parse_qty(it["qty_per_porsi"]) * hrg, 2)
                    total_biaya_bahan += it["biaya_bahan"]

                r["items"] = items
                r["total_biaya_bahan"] = round(total_biaya_bahan, 2)
                hrg_jual = r["harga_jual"] or 0
                r["food_cost_pct"] = round((total_biaya_bahan / hrg_jual) * 100, 2) if hrg_jual > 0 else 0
                r["margin_nominal"] = round(hrg_jual - total_biaya_bahan, 2)
                r["margin_pct"] = round(100 - r["food_cost_pct"], 2) if hrg_jual > 0 else 0
                target_fc = r["target_fc"] or 0.3
                target_pct = target_fc * 100 if target_fc <= 1 else target_fc
                r["target_fc_pct"] = target_pct
                r["status_fc"] = "Ideal" if r["food_cost_pct"] <= target_pct else "Over Budget"

            conn.close()
            self._send_json({"recipes": recipes})
            return

        # GET /api/waste (Daftar Catatan Waste & Susut)
        if clean_path == "/api/waste":
            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            c = conn.cursor()
            records = c.execute("SELECT * FROM waste_records ORDER BY tanggal DESC, id DESC").fetchall()
            for r in records:
                r["qty"] = parse_qty(r.get("qty", 0))
                r["biaya_per_satuan"] = parse_qty(r.get("biaya_per_satuan", 0))
                r["kerugian_total"] = parse_qty(r.get("kerugian_total", 0))
            conn.close()
            self._send_json({"waste_records": records})
            return

        # GET /api/transfers (Daftar Mutasi Antar-Gudang)
        if clean_path == "/api/transfers":
            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            c = conn.cursor()
            records = c.execute("SELECT * FROM warehouse_transfers ORDER BY tanggal DESC, id DESC").fetchall()
            for r in records:
                r["qty"] = parse_qty(r.get("qty", 0))
            conn.close()
            self._send_json({"transfers": records})
            return

        # GET /api/kartu-stok (Stock Ledger Dinamis per Barang & per Gudang)
        if clean_path == "/api/kartu-stok":
            params = parse_qs(parsed.query)
            nama_barang = params.get("nama_barang", [None])[0]
            gudang = params.get("gudang", [None])[0]

            conn = sqlite3.connect(DB_ITEMS_PATH)
            conn.row_factory = dict_factory
            c = conn.cursor()

            query = "SELECT * FROM items WHERE 1=1"
            args = []
            if nama_barang:
                query += " AND lower(trim(nama_barang)) = lower(trim(?))"
                args.append(nama_barang)
            if gudang:
                query += " AND gudang = ?"
                args.append(gudang)
            query += " ORDER BY tanggal_lengkap ASC, id ASC"

            rows = c.execute(query, args).fetchall()
            ledger = []
            saldo_running = 0

            for r in rows:
                qty_in = parse_qty(r["jumlah_order"]) if r["jenis_transaksi"] == "masuk" else 0
                qty_out = parse_qty(r["jumlah_order"]) if r["jenis_transaksi"] == "keluar" else 0
                saldo_awal = saldo_running
                saldo_running = round(saldo_awal + qty_in - qty_out, 3)

                # Identifikasi jenis transaksi & No Dokumen
                ket = r.get("keterangan", "")
                jenis_label = "Penyesuaian"
                no_dok = f"TRX-{r['id']:04d}"
                if "PO No." in ket:
                    jenis_label = "Penerimaan Pembelian"
                    m = re.search(r"PO-\d{6}-\d+", ket)
                    if m: no_dok = m.group(0)
                elif "[PRODUKSI RESEP]" in ket:
                    jenis_label = "Pemakaian Produksi"
                    no_dok = f"PRD-{r['id']:04d}"
                elif "[WASTE]" in ket:
                    jenis_label = "Waste / Rusak"
                    m = re.search(r"WST-\d+", ket)
                    if m: no_dok = m.group(0)
                elif "[MUTASI KELUAR]" in ket or "[MUTASI MASUK]" in ket:
                    jenis_label = "Mutasi Gudang"
                    m = re.search(r"MUT-\d+", ket)
                    if m: no_dok = m.group(0)
                elif r["jenis_transaksi"] == "masuk":
                    jenis_label = "Barang Masuk"
                else:
                    jenis_label = "Barang Keluar"

                ledger.append({
                    "id": r["id"],
                    "tanggal": r["tanggal_lengkap"],
                    "no_dokumen": no_dok,
                    "jenis_transaksi": jenis_label,
                    "gudang": r["gudang"],
                    "kode_barang": r.get("kode_barang", ""),
                    "nama_barang": r["nama_barang"],
                    "masuk": qty_in,
                    "keluar": qty_out,
                    "saldo_awal": saldo_awal,
                    "saldo_akhir": saldo_running,
                    "satuan": r["satuan"],
                    "keterangan": ket,
                    "pemakaian_dimana": r.get("pemakaian_dimana", "")
                })

            conn.close()
            # Tampilkan transaksi terbaru di atas untuk user viewing
            self._send_json({
                "ledger": list(reversed(ledger)),
                "saldo_akhir_total": saldo_running
            })
            return

        # GET /api/settings
        if clean_path == "/api/settings":
            conn = sqlite3.connect(DB_SETTINGS_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            rows = cursor.fetchall()
            conn.close()
            settings_dict = {r["key"]: r["value"] for r in rows}
            self._send_json({"settings": settings_dict})
            return

        # GET /api/audit-logs
        if clean_path == "/api/audit-logs":
            conn = sqlite3.connect(DB_SETTINGS_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200")
            rows = cursor.fetchall()
            conn.close()
            self._send_json({"logs": rows})
            return

        # GET /api/opname/history
        if clean_path == "/api/opname/history":
            params = parse_qs(parsed.query)
            gudang = params.get("gudang", [None])[0]
            conn = sqlite3.connect(DB_SETTINGS_PATH)
            conn.row_factory = dict_factory
            cursor = conn.cursor()
            if gudang:
                cursor.execute("SELECT * FROM opname_history WHERE gudang = ? ORDER BY id DESC LIMIT 200", (gudang,))
            else:
                cursor.execute("SELECT * FROM opname_history ORDER BY id DESC LIMIT 200")
            rows = cursor.fetchall()
            conn.close()
            self._send_json({"history": rows})
            return

        # GET /api/backup
        if clean_path == "/api/backup":
            payload = get_full_backup_payload()
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"backup_database_barang_{datetime.now().strftime('%Y-%m-%d_%H%M')}.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # GET /api/backups/list
        if clean_path == "/api/backups/list":
            self._send_json({"backups": list_local_backups(), "backup_dir": BACKUP_DIR})
            return

        # GET /api/shutdown
        if clean_path == "/api/shutdown":
            self._send_json({"shutdown": True})
            import threading
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path.rstrip("/")

        # POST /api/items
        if clean_path == "/api/items":
            try:
                data = self._read_json()
                tanggal_str = str(data.get("tanggal_lengkap", "")).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tanggal_str)

                kategori = str(data.get("kategori", "Umum")).strip() or "Umum"
                nama_barang = str(data.get("nama_barang", "")).strip()
                satuan = str(data.get("satuan", "Pcs")).strip() or "Pcs"
                jenis_transaksi = str(data.get("jenis_transaksi", "masuk")).strip().lower()
                if jenis_transaksi not in ("masuk", "keluar"):
                    jenis_transaksi = "masuk"

                jumlah_sebelumnya = parse_qty(data.get("jumlah_sebelumnya", 0))
                jumlah_order = parse_qty(data.get("jumlah_order", 0))
                total_akhir = hitung_total_akhir(jumlah_sebelumnya, jumlah_order, jenis_transaksi)
                pemakaian_dimana = str(data.get("pemakaian_dimana", "")).strip()
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                tanggal_expired = str(data.get("tanggal_expired", "")).strip()
                keterangan = str(data.get("keterangan", "")).strip()

                if not nama_barang:
                    self._send_json({"error": "Nama barang wajib diisi"}, 400)
                    return

                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()

                master = find_master_barang(cursor, nama_barang)
                if not master:
                    conn.close()
                    self._send_json({"error": f"Barang '{nama_barang}' belum terdaftar di Master Barang. Daftarkan dulu di tab Master Barang."}, 400)
                    return
                nama_barang = master["nama_barang"]
                kode_barang = master["kode_barang"]
                gudang = str(data.get("gudang", "")).strip() or master.get("gudang") or "Gudang ME"
                if not sub_lokasi:
                    sub_lokasi = master.get("sub_lokasi") or ""

                if total_akhir < 0:
                    conn.close()
                    self._send_json({"error": f"Stok tidak mencukupi! Stok sebelumnya {jumlah_sebelumnya}, barang keluar {jumlah_order} (sisa {total_akhir}). Periksa jumlah keluar."}, 400)
                    return

                cursor.execute(
                    """
                    INSERT INTO items (
                        tanggal_lengkap, tanggal, bulan, tahun, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang,
                        gudang, sub_lokasi, tanggal_expired
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tgl_lengkap, tgl, bln, thn, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang,
                        gudang, sub_lokasi, tanggal_expired,
                    ),
                )
                item_id = cursor.lastrowid
                conn.commit()
                cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
                new_item = cursor.fetchone()
                conn.close()

                create_auto_backup()
                self._send_json({"item": new_item}, 201)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/master-barang
        if clean_path == "/api/master-barang":
            conn = None
            try:
                data = self._read_json()
                nama = str(data.get("nama_barang", "")).strip()
                kode = str(data.get("kode_barang", "")).strip().upper()
                gudang = str(data.get("gudang", "Gudang ME")).strip() or "Gudang ME"
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                ada_expired = 1 if data.get("ada_expired") in (1, "1", True, "true") or gudang == "Gudang Makanan" else 0
                kategori = str(data.get("kategori", "Umum")).strip() or "Umum"
                satuan = str(data.get("satuan", "Pcs")).strip() or "Pcs"
                stok_min = max(parse_qty(data.get("stok_minimum", 0)), 0)
                harga_beli = max(int(data.get("harga_beli", 0) or 0), 0)
                harga_jual = max(int(data.get("harga_jual", 0) or 0), 0)
                if not nama:
                    self._send_json({"error": "Nama barang wajib diisi"}, 400)
                    return
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                if not kode:
                    kode = generate_kode_barang(cursor, gudang)
                cursor.execute(
                    "INSERT INTO master_barang (kode_barang, nama_barang, gudang, sub_lokasi, ada_expired, kategori, satuan, stok_minimum, harga_beli, harga_jual, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (kode, nama, gudang, sub_lokasi, ada_expired, kategori, satuan, stok_min, harga_beli, harga_jual, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                conn.commit()
                conn.close()
                create_auto_backup()
                self._send_json({"success": True, "kode_barang": kode}, 201)
            except sqlite3.IntegrityError:
                if conn: conn.close()
                self._send_json({"error": "Kode barang atau nama barang sudah terdaftar"}, 400)
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/purchases (Tambah PO baru ke purchase.db)
        if clean_path == "/api/purchases":
            conn = None
            try:
                data = self._read_json()
                tanggal_str = str(data.get("tanggal_lengkap", "")).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tanggal_str)
                no_po = str(data.get("no_po", "")).strip().upper()
                supplier = str(data.get("supplier", "")).strip()
                nama_barang = str(data.get("nama_barang", "")).strip()
                kategori = str(data.get("kategori", "Umum")).strip() or "Umum"
                satuan = str(data.get("satuan", "Pcs")).strip() or "Pcs"
                jumlah = max(parse_qty(data.get("jumlah", 0)), 0)
                harga_satuan = max(int(data.get("harga_satuan", 0) or 0), 0)
                total_harga = int(round(jumlah * harga_satuan))
                status = str(data.get("status", "dipesan")).strip().lower()
                if status not in ("pengajuan", "dipesan", "diterima", "batal"):
                    status = "dipesan"
                gudang = str(data.get("gudang", "")).strip()
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                tanggal_expired = str(data.get("tanggal_expired", "")).strip()
                lokasi_tujuan = str(data.get("lokasi_tujuan", "")).strip()
                keterangan = str(data.get("keterangan", "")).strip()

                if not nama_barang:
                    self._send_json({"error": "Nama barang wajib diisi"}, 400)
                    return
                if jumlah <= 0:
                    self._send_json({"error": "Jumlah pembelian harus lebih dari 0"}, 400)
                    return

                # Cek / sinkron ke Master Barang
                conn_m = sqlite3.connect(DB_ITEMS_PATH)
                conn_m.row_factory = dict_factory
                cm = conn_m.cursor()
                master = find_master_barang(cm, nama_barang)
                if not master:
                    conn_m.close()
                    self._send_json({"error": f"Barang '{nama_barang}' belum ada di Master Barang. Daftarkan dulu di Master Barang."}, 400)
                    return
                nama_barang = master["nama_barang"]
                kode_barang = master["kode_barang"]
                kategori = master["kategori"]
                satuan = master["satuan"]
                if not gudang:
                    gudang = master.get("gudang") or "Gudang ME"
                if not sub_lokasi:
                    sub_lokasi = master.get("sub_lokasi") or ""
                if not lokasi_tujuan:
                    lokasi_tujuan = f"{gudang}" + (f" - {sub_lokasi}" if sub_lokasi else "")
                if harga_satuan > 0:
                    cm.execute("UPDATE master_barang SET harga_beli = ? WHERE id = ?", (harga_satuan, master["id"]))
                    conn_m.commit()
                conn_m.close()

                conn = sqlite3.connect(DB_PURCHASE_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                if not no_po:
                    no_po = generate_no_po(cursor, tgl_lengkap)

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                stok_masuk_id = None
                received_at = ""
                if status == "diterima":
                    stok_masuk_id = catat_barang_masuk_dari_po({
                        "no_po": no_po,
                        "tanggal_lengkap": tgl_lengkap,
                        "supplier": supplier,
                        "kode_barang": kode_barang,
                        "nama_barang": nama_barang,
                        "kategori": kategori,
                        "satuan": satuan,
                        "jumlah": jumlah,
                        "harga_satuan": harga_satuan,
                        "gudang": gudang,
                        "sub_lokasi": sub_lokasi,
                        "tanggal_expired": tanggal_expired,
                        "lokasi_tujuan": lokasi_tujuan,
                        "keterangan": keterangan,
                    })
                    received_at = now_str

                cursor.execute(
                    """
                    INSERT INTO purchases (
                        no_po, tanggal_lengkap, tanggal, bulan, tahun, supplier,
                        kode_barang, nama_barang, kategori, satuan, jumlah,
                        harga_satuan, total_harga, status, lokasi_tujuan, keterangan,
                        stok_masuk_id, received_at, created_at,
                        gudang, sub_lokasi, tanggal_expired
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        no_po, tgl_lengkap, tgl, bln, thn, supplier,
                        kode_barang, nama_barang, kategori, satuan, jumlah,
                        harga_satuan, total_harga, status, lokasi_tujuan, keterangan,
                        stok_masuk_id, received_at, now_str,
                        gudang, sub_lokasi, tanggal_expired,
                    ),
                )
                po_id = cursor.lastrowid
                conn.commit()
                new_po = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                conn.close()
                create_auto_backup()
                self._send_json({"purchase": new_po}, 201)
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/purchases/<id>/terima (1 klik: Terima barang PO -> otomatis tambah stok gudang)
        if clean_path.startswith("/api/purchases/") and clean_path.endswith("/terima"):
            conn = None
            try:
                parts = clean_path.split("/")
                po_id = int(parts[-2])
                conn = sqlite3.connect(DB_PURCHASE_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                po = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                if not po:
                    conn.close()
                    self._send_json({"error": "Data PO tidak ditemukan"}, 404)
                    return
                if po["status"] == "diterima" and po.get("stok_masuk_id"):
                    conn.close()
                    self._send_json({"error": "Barang dari PO ini sudah pernah diterima ke gudang."}, 400)
                    return

                stok_id = catat_barang_masuk_dari_po(po)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    "UPDATE purchases SET status = 'diterima', stok_masuk_id = ?, received_at = ? WHERE id = ?",
                    (stok_id, now_str, po_id),
                )
                conn.commit()
                updated = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                conn.close()
                create_auto_backup()
                self._send_json({"purchase": updated, "stok_masuk_id": stok_id})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/verify-pin
        if clean_path == "/api/verify-pin":
            try:
                data = self._read_json()
                pin = data.get("pin", "")
                if verifikasi_pin(pin):
                    self._send_json({"valid": True})
                else:
                    self._send_json({"valid": False, "error": "PIN Supervisor salah!"}, 403)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/opname/sesuaikan (Stok Opname Penyesuaian Fisik vs Sistem)
        if clean_path == "/api/opname/sesuaikan":
            try:
                data = self._read_json()
                pin = data.get("pin", "")
                if not verifikasi_pin(pin):
                    self._send_json({"error": "PIN Supervisor salah! Penyesuaian stok ditolak."}, 403)
                    return

                nama_barang = str(data.get("nama_barang", "")).strip()
                gudang = str(data.get("gudang", "")).strip()
                stok_fisik = parse_qty(data.get("stok_fisik", 0))
                alasan = str(data.get("alasan", "")).strip()

                if not nama_barang:
                    self._send_json({"error": "Nama barang wajib diisi."}, 400)
                    return
                if not alasan:
                    self._send_json({"error": "Wajib isi alasan selisih stok (contoh: Rusak, Susut, Salah Hitung)."}, 400)
                    return

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                master = find_master_barang(cursor, nama_barang)
                if not master:
                    conn.close()
                    self._send_json({"error": f"Barang '{nama_barang}' tidak ditemukan."}, 404)
                    return

                # Hitung stok sistem saat ini
                stok_sistem = hitung_stok_saat_ini(cursor, master["nama_barang"])
                selisih = round(stok_fisik - stok_sistem, 3)

                now_dt = datetime.now()
                tgl_lengkap = now_dt.strftime("%Y-%m-%d")
                tgl = now_dt.day
                bln = now_dt.month
                thn = now_dt.year
                created_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")

                if selisih == 0:
                    conn.close()
                    self._send_json({"message": "Stok fisik sama dengan sistem. Tidak ada perubahan stok."})
                    return

                # Buat baris transaksi penyesuaian
                jenis = "masuk" if selisih > 0 else "keluar"
                jml_order = abs(selisih)
                total_akhir = stok_fisik
                keterangan_transaksi = f"[STOK OPNAME] Penyesuaian fisik ({stok_sistem} -> {stok_fisik}). Alasan: {alasan}"

                cursor.execute("""
                    INSERT INTO items (
                        tanggal_lengkap, tanggal, bulan, tahun, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tgl_lengkap, tgl, bln, thn, master.get("kategori", "Umum"),
                    master["nama_barang"], master.get("satuan", "Pcs"), jenis, stok_sistem, jml_order,
                    total_akhir, "Stok Opname", keterangan_transaksi, created_at, master["kode_barang"],
                    gudang or master.get("gudang", "Gudang ME"), master.get("sub_lokasi", "")
                ))
                conn.commit()
                conn.close()

                # Simpan ke riwayat opname di pengaturan.db
                conn_set = sqlite3.connect(DB_SETTINGS_PATH)
                c_set = conn_set.cursor()
                c_set.execute("""
                    INSERT INTO opname_history (
                        tanggal, gudang, kode_barang, nama_barang, satuan,
                        stok_sistem, stok_fisik, selisih, alasan, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tgl_lengkap, gudang or master.get("gudang", "Gudang ME"), master["kode_barang"],
                    master["nama_barang"], master.get("satuan", "Pcs"),
                    stok_sistem, stok_fisik, selisih, alasan, created_at
                ))
                conn_set.commit()
                conn_set.close()

                record_audit(
                    "STOK_OPNAME",
                    "Gudang",
                    f"{master['kode_barang']} - {master['nama_barang']}: Sistem {stok_sistem} -> Fisik {stok_fisik} (Selisih {selisih:+})",
                    alasan
                )
                create_auto_backup()
                self._send_json({
                    "success": True,
                    "nama_barang": master["nama_barang"],
                    "stok_sistem": stok_sistem,
                    "stok_fisik": stok_fisik,
                    "selisih": selisih
                })
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/master-gudang (Tambah Gudang/Divisi Baru)
        if clean_path == "/api/master-gudang":
            try:
                data = self._read_json()
                nama = str(data.get("nama", "")).strip()
                prefix = str(data.get("prefix", "BRG")).strip().upper()
                label = str(data.get("label", "")).strip() or nama
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                wajib_expired = 1 if data.get("wajib_expired") in (1, "1", True, "true") else 0

                if not nama or not prefix:
                    self._send_json({"error": "Nama gudang dan prefix kode barang wajib diisi."}, 400)
                    return

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = sqlite3.connect(DB_SETTINGS_PATH)
                c = conn.cursor()
                c.execute("""
                    INSERT INTO master_gudang (nama, label, prefix, sub_lokasi, wajib_expired, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (nama, label, prefix, sub_lokasi, wajib_expired, now_str))
                conn.commit()
                conn.close()

                record_audit("TAMBAH_GUDANG", "Master Gudang", f"{nama} (Prefix: {prefix})", f"Sub-lokasi: {sub_lokasi}")
                self._send_json({"success": True, "gudang_list": get_daftar_gudang()}, 201)
            except sqlite3.IntegrityError:
                self._send_json({"error": "Nama gudang tersebut sudah ada."}, 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/categories
        if clean_path == "/api/categories":
            try:
                data = self._read_json()
                nama_kategori = str(data.get("nama_kategori", "")).strip()
                deskripsi = str(data.get("deskripsi", "")).strip()

                if not nama_kategori:
                    self._send_json({"error": "Nama kategori wajib diisi"}, 400)
                    return

                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = sqlite3.connect(DB_CATEGORIES_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO categories (nama_kategori, deskripsi, created_at) VALUES (?, ?, ?)",
                    (nama_kategori, deskripsi, created_at),
                )
                cat_id = cursor.lastrowid
                conn.commit()
                cursor.execute("SELECT * FROM categories WHERE id = ?", (cat_id,))
                new_cat = cursor.fetchone()
                conn.close()

                create_auto_backup()
                self._send_json({"category": new_cat}, 201)
            except sqlite3.IntegrityError:
                self._send_json({"error": "Nama kategori tersebut sudah ada"}, 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/settings
        if clean_path == "/api/settings":
            try:
                data = self._read_json()
                conn = sqlite3.connect(DB_SETTINGS_PATH)
                cursor = conn.cursor()
                for key, value in data.items():
                    cursor.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (str(key), str(value)),
                    )
                conn.commit()
                conn.close()
                create_auto_backup()
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/backup/now
        if clean_path == "/api/backup/now":
            path = create_auto_backup(tag="manual")
            self._send_json({"success": True, "path": path, "backups": list_local_backups()})
            return

        # POST /api/restore
        if clean_path == "/api/restore":
            try:
                payload = self._read_json()
                create_auto_backup(tag="sebelum_restore")
                restore_from_payload(payload)
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": f"Gagal me-restore database: {e}"}, 400)
            return

        # ==================== F&B POST ENDPOINTS ====================
        # POST /api/recipes (Simpan Resep Baru / Update BOM)
        if clean_path == "/api/recipes":
            conn = None
            try:
                data = self._read_json()
                kode_menu = str(data.get("kode_menu", "")).strip().upper()
                nama_menu = str(data.get("nama_menu", "")).strip()
                kategori = str(data.get("kategori", "Makanan")).strip() or "Makanan"
                harga_jual = max(parse_qty(data.get("harga_jual", 0)), 0)
                target_fc = parse_qty(data.get("target_fc", 0.3))
                porsi_standar = max(parse_qty(data.get("porsi_standar", 1)), 0.1)
                keterangan = str(data.get("keterangan", "")).strip()
                items = data.get("items", [])

                if not nama_menu:
                    self._send_json({"error": "Nama menu wajib diisi."}, 400)
                    return

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()

                if not kode_menu:
                    count_rcp = c.execute("SELECT COUNT(*) FROM recipes").fetchone()["COUNT(*)"]
                    kode_menu = f"MNU{count_rcp + 1:03d}"

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("""
                    INSERT INTO recipes (kode_menu, nama_menu, kategori, harga_jual, target_fc, porsi_standar, keterangan, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kode_menu) DO UPDATE SET
                        nama_menu = excluded.nama_menu,
                        kategori = excluded.kategori,
                        harga_jual = excluded.harga_jual,
                        target_fc = excluded.target_fc,
                        porsi_standar = excluded.porsi_standar,
                        keterangan = excluded.keterangan
                """, (kode_menu, nama_menu, kategori, harga_jual, target_fc, porsi_standar, keterangan, now_str))

                rcp = c.execute("SELECT id FROM recipes WHERE kode_menu = ?", (kode_menu,)).fetchone()
                rcp_id = rcp["id"]

                # Simpan list bahan BOM
                c.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (rcp_id,))
                for it in items:
                    nb = str(it.get("nama_bahan", "")).strip()
                    if not nb: continue
                    kb = str(it.get("kode_bahan", "")).strip()
                    qty_porsi = parse_qty(it.get("qty_per_porsi", 0))
                    satuan = str(it.get("satuan", "gram")).strip() or "gram"
                    cat = str(it.get("catatan", "")).strip()
                    c.execute("""
                        INSERT INTO recipe_items (recipe_id, kode_bahan, nama_bahan, qty_per_porsi, satuan, catatan)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (rcp_id, kb, nb, qty_porsi, satuan, cat))

                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("SIMPAN_RESEP", "F&B", f"Resep {nama_menu} ({kode_menu}) disimpan dengan {len(items)} bahan.")
                self._send_json({"success": True, "kode_menu": kode_menu, "recipe_id": rcp_id})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/recipes/<id>/eksekusi (Potong Stok Bahan Otomatis Sesuai Porsi Terjual/Masak)
        if clean_path.startswith("/api/recipes/") and clean_path.endswith("/eksekusi"):
            conn = None
            try:
                parts = clean_path.split("/")
                rcp_id = int(parts[-2])
                data = self._read_json()
                porsi = max(parse_qty(data.get("porsi", 1)), 0.01)
                gudang = str(data.get("gudang", "Gudang Makanan")).strip() or "Gudang Makanan"
                tgl_str = str(data.get("tanggal", datetime.now().strftime("%Y-%m-%d"))).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tgl_str)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()

                rcp = c.execute("SELECT * FROM recipes WHERE id = ?", (rcp_id,)).fetchone()
                if not rcp:
                    conn.close()
                    self._send_json({"error": "Resep tidak ditemukan"}, 404)
                    return

                items_bom = c.execute("SELECT * FROM recipe_items WHERE recipe_id = ?", (rcp_id,)).fetchall()
                if not items_bom:
                    conn.close()
                    self._send_json({"error": "Resep ini belum memiliki komposisi bahan."}, 400)
                    return

                deductions = []
                for it in items_bom:
                    nb = it["nama_bahan"]
                    qty_pakai = round(parse_qty(it["qty_per_porsi"]) * porsi, 3)
                    stok_seb = hitung_stok_saat_ini(c, nb, gudang)
                    total_akhir = round(stok_seb - qty_pakai, 3)

                    ket = f"[PRODUKSI RESEP] {porsi} porsi {rcp['nama_menu']} ({rcp['kode_menu']})"
                    c.execute("""
                        INSERT INTO items (
                            tanggal_lengkap, tanggal, bulan, tahun, kategori,
                            nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                            total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                        ) VALUES (?, ?, ?, ?, 'Bahan Makanan', ?, ?, 'keluar', ?, ?, ?, 'Kitchen / Produksi', ?, ?, ?, ?, 'Kitchen')
                    """, (
                        tgl_lengkap, tgl, bln, thn,
                        nb, it["satuan"], stok_seb, qty_pakai, total_akhir,
                        ket, now_str, it.get("kode_bahan", ""), gudang
                    ))
                    deductions.append({"nama_bahan": nb, "qty": qty_pakai, "satuan": it["satuan"], "sisa": total_akhir})

                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("EKSEKUSI_PRODUKSI", "F&B", f"Produksi {porsi} porsi {rcp['nama_menu']}. {len(deductions)} bahan terpotong.")
                self._send_json({"success": True, "porsi": porsi, "menu": rcp["nama_menu"], "deductions": deductions})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/waste (Catat Waste / Spoilage / Rusak & Otomatis Potong Stok)
        if clean_path == "/api/waste":
            conn = None
            try:
                data = self._read_json()
                tgl_str = str(data.get("tanggal", datetime.now().strftime("%Y-%m-%d"))).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tgl_str)
                gudang = str(data.get("gudang", "Gudang Makanan")).strip()
                nama_bahan = str(data.get("nama_bahan", "")).strip()
                kategori_waste = str(data.get("kategori_waste", "Rusak/busuk")).strip()
                qty = max(parse_qty(data.get("qty", 0)), 0.001)
                satuan = str(data.get("satuan", "gram")).strip() or "gram"
                alasan = str(data.get("alasan", "")).strip()
                disetujui = str(data.get("disetujui_oleh", "Supervisor")).strip() or "Supervisor"

                if not nama_bahan:
                    self._send_json({"error": "Nama bahan wajib diisi."}, 400)
                    return
                if not alasan:
                    self._send_json({"error": "Wajib mengisi alasan kerusakan/waste."}, 400)
                    return

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()

                master = find_master_barang(c, nama_bahan)
                biaya_per_satuan = master["harga_beli"] if master else 0
                if "biaya_per_satuan" in data and parse_qty(data["biaya_per_satuan"]) > 0:
                    biaya_per_satuan = parse_qty(data["biaya_per_satuan"])
                kerugian_total = round(qty * biaya_per_satuan, 2)

                # Generate No. Waste
                count_w = c.execute("SELECT COUNT(*) FROM waste_records").fetchone()["COUNT(*)"]
                no_waste = f"WST-{count_w + 1:04d}"

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                kode_bahan = master["kode_barang"] if master else ""

                c.execute("""
                    INSERT INTO waste_records (
                        no_waste, tanggal, gudang, kode_bahan, nama_bahan, kategori_waste,
                        qty, satuan, biaya_per_satuan, kerugian_total, alasan, disetujui_oleh, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    no_waste, tgl_lengkap, gudang, kode_bahan, nama_bahan, kategori_waste,
                    qty, satuan, biaya_per_satuan, kerugian_total, alasan, disetujui, now_str
                ))

                # Otomatis catat di jurnal items sebagai barang keluar
                stok_seb = hitung_stok_saat_ini(c, nama_bahan, gudang)
                total_akhir = round(stok_seb - qty, 3)
                ket_item = f"[WASTE {no_waste}] {kategori_waste} ({qty} {satuan}). Alasan: {alasan} (Acc: {disetujui})"

                c.execute("""
                    INSERT INTO items (
                        tanggal_lengkap, tanggal, bulan, tahun, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'keluar', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tgl_lengkap, tgl, bln, thn, master.get("kategori", "Bahan Makanan") if master else "Bahan Makanan",
                    nama_bahan, satuan, stok_seb, qty, total_akhir,
                    f"Waste ({gudang})", ket_item, now_str, kode_bahan, gudang, "Waste/Rusak"
                ))

                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("CATAT_WASTE", "F&B", f"{no_waste} {nama_bahan} {qty} {satuan} kerugian Rp {kerugian_total:,.0f}")
                self._send_json({"success": True, "no_waste": no_waste, "kerugian_total": kerugian_total})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/transfers (Buat Mutasi Antar-Gudang)
        if clean_path == "/api/transfers":
            conn = None
            try:
                data = self._read_json()
                tgl_str = str(data.get("tanggal", datetime.now().strftime("%Y-%m-%d"))).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tgl_str)
                gudang_asal = str(data.get("gudang_asal", "")).strip()
                gudang_tujuan = str(data.get("gudang_tujuan", "")).strip()
                nama_bahan = str(data.get("nama_bahan", "")).strip()
                qty = max(parse_qty(data.get("qty", 0)), 0.001)
                satuan = str(data.get("satuan", "gram")).strip() or "gram"
                status = str(data.get("status", "Draft")).strip().title()
                catatan = str(data.get("catatan", "")).strip()

                if not nama_bahan or not gudang_asal or not gudang_tujuan:
                    self._send_json({"error": "Bahan, gudang asal, dan gudang tujuan wajib dipilih."}, 400)
                    return
                if gudang_asal == gudang_tujuan:
                    self._send_json({"error": "Gudang asal dan tujuan tidak boleh sama."}, 400)
                    return

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()

                master = find_master_barang(c, nama_bahan)
                kode_bahan = master["kode_barang"] if master else ""
                count_m = c.execute("SELECT COUNT(*) FROM warehouse_transfers").fetchone()["COUNT(*)"]
                no_mutasi = f"MUT-{count_m + 1:04d}"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                posted_at = now_str if status == "Posted" else ""

                c.execute("""
                    INSERT INTO warehouse_transfers (
                        no_mutasi, tanggal, gudang_asal, gudang_tujuan, kode_bahan, nama_bahan,
                        qty, satuan, status, catatan, posted_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    no_mutasi, tgl_lengkap, gudang_asal, gudang_tujuan, kode_bahan, nama_bahan,
                    qty, satuan, status, catatan, posted_at, now_str
                ))

                # Jika langsung berstatus Posted: potong gudang asal & tambah gudang tujuan
                if status == "Posted":
                    stok_asal_seb = hitung_stok_saat_ini(c, nama_bahan, gudang_asal)
                    sisa_asal = round(stok_asal_seb - qty, 3)
                    c.execute("""
                        INSERT INTO items (
                            tanggal_lengkap, tanggal, bulan, tahun, kategori,
                            nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                            total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'keluar', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tgl_lengkap, tgl, bln, thn, master.get("kategori", "Bahan Makanan") if master else "Bahan Makanan",
                        nama_bahan, satuan, stok_asal_seb, qty, sisa_asal,
                        f"Transfer ke {gudang_tujuan}", f"[MUTASI KELUAR {no_mutasi}] Kirim ke {gudang_tujuan}. {catatan}",
                        now_str, kode_bahan, gudang_asal, ""
                    ))

                    stok_tuj_seb = hitung_stok_saat_ini(c, nama_bahan, gudang_tujuan)
                    total_tuj = round(stok_tuj_seb + qty, 3)
                    c.execute("""
                        INSERT INTO items (
                            tanggal_lengkap, tanggal, bulan, tahun, kategori,
                            nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                            total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'masuk', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tgl_lengkap, tgl, bln, thn, master.get("kategori", "Bahan Makanan") if master else "Bahan Makanan",
                        nama_bahan, satuan, stok_tuj_seb, qty, total_tuj,
                        f"Terima dari {gudang_asal}", f"[MUTASI MASUK {no_mutasi}] Terima dari {gudang_asal}. {catatan}",
                        now_str, kode_bahan, gudang_tujuan, ""
                    ))

                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("MUTASI_GUDANG", "F&B", f"{no_mutasi} {nama_bahan} {qty} {satuan} dari {gudang_asal} ke {gudang_tujuan} [{status}]")
                self._send_json({"success": True, "no_mutasi": no_mutasi, "status": status})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        # POST /api/transfers/<id>/post (Posting Mutasi yang sebelumnya Draft)
        if clean_path.startswith("/api/transfers/") and clean_path.endswith("/post"):
            conn = None
            try:
                tr_id = int(clean_path.split("/")[-2])
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()
                mut = c.execute("SELECT * FROM warehouse_transfers WHERE id = ?", (tr_id,)).fetchone()
                if not mut:
                    conn.close()
                    self._send_json({"error": "Data mutasi tidak ditemukan"}, 404)
                    return
                if mut["status"] == "Posted":
                    conn.close()
                    self._send_json({"error": "Mutasi ini sudah berstatus Posted sebelumnya."}, 400)
                    return

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                tgl_lengkap, tgl, bln, thn = parse_date_parts(mut["tanggal"])
                nb = mut["nama_bahan"]
                qty = parse_qty(mut["qty"])
                satuan = mut["satuan"]
                ga = mut["gudang_asal"]
                gt = mut["gudang_tujuan"]
                no_mutasi = mut["no_mutasi"]
                master = find_master_barang(c, nb)
                kb = master["kode_barang"] if master else mut.get("kode_bahan", "")

                # 1. Keluar dari asal
                stok_asal_seb = hitung_stok_saat_ini(c, nb, ga)
                sisa_asal = round(stok_asal_seb - qty, 3)
                c.execute("""
                    INSERT INTO items (
                        tanggal_lengkap, tanggal, bulan, tahun, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'keluar', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tgl_lengkap, tgl, bln, thn, master.get("kategori", "Bahan Makanan") if master else "Bahan Makanan",
                    nb, satuan, stok_asal_seb, qty, sisa_asal,
                    f"Transfer ke {gt}", f"[MUTASI KELUAR {no_mutasi}] Kirim ke {gt}. {mut['catatan']}",
                    now_str, kb, ga, ""
                ))

                # 2. Masuk ke tujuan
                stok_tuj_seb = hitung_stok_saat_ini(c, nb, gt)
                total_tuj = round(stok_tuj_seb + qty, 3)
                c.execute("""
                    INSERT INTO items (
                        tanggal_lengkap, tanggal, bulan, tahun, kategori,
                        nama_barang, satuan, jenis_transaksi, jumlah_sebelumnya, jumlah_order,
                        total_akhir, pemakaian_dimana, keterangan, created_at, kode_barang, gudang, sub_lokasi
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'masuk', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tgl_lengkap, tgl, bln, thn, master.get("kategori", "Bahan Makanan") if master else "Bahan Makanan",
                    nb, satuan, stok_tuj_seb, qty, total_tuj,
                    f"Terima dari {ga}", f"[MUTASI MASUK {no_mutasi}] Terima dari {ga}. {mut['catatan']}",
                    now_str, kb, gt, ""
                ))

                # Update status mutasi
                c.execute("UPDATE warehouse_transfers SET status = 'Posted', posted_at = ? WHERE id = ?", (now_str, tr_id))
                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("POST_MUTASI", "F&B", f"{no_mutasi} {nb} {qty} {satuan} berhasil di-posting dari {ga} ke {gt}.")
                self._send_json({"success": True, "no_mutasi": no_mutasi})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        self._send_json({"error": f"Endpoint tidak ditemukan: {clean_path}"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path.rstrip("/")

        # PUT /api/master-gudang/<id> (Edit Gudang/Divisi)
        if clean_path.startswith("/api/master-gudang/"):
            try:
                gud_id = int(clean_path.split("/")[-1])
                data = self._read_json()
                nama = str(data.get("nama", "")).strip()
                prefix = str(data.get("prefix", "BRG")).strip().upper()
                label = str(data.get("label", "")).strip() or nama
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                wajib_expired = 1 if data.get("wajib_expired") in (1, "1", True, "true") else 0

                if not nama or not prefix:
                    self._send_json({"error": "Nama gudang dan prefix wajib diisi."}, 400)
                    return

                conn = sqlite3.connect(DB_SETTINGS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()
                old = c.execute("SELECT * FROM master_gudang WHERE id = ?", (gud_id,)).fetchone()
                if not old:
                    conn.close()
                    self._send_json({"error": "Gudang tidak ditemukan."}, 404)
                    return

                c.execute("""
                    UPDATE master_gudang SET nama = ?, label = ?, prefix = ?, sub_lokasi = ?, wajib_expired = ?
                    WHERE id = ?
                """, (nama, label, prefix, sub_lokasi, wajib_expired, gud_id))
                conn.commit()
                conn.close()

                # Jika nama gudang berubah, perbarui juga di items, master_barang, purchases
                if old["nama"] != nama:
                    conn_inv = sqlite3.connect(DB_ITEMS_PATH)
                    c_inv = conn_inv.cursor()
                    c_inv.execute("UPDATE items SET gudang = ? WHERE gudang = ?", (nama, old["nama"]))
                    c_inv.execute("UPDATE master_barang SET gudang = ? WHERE gudang = ?", (nama, old["nama"]))
                    conn_inv.commit()
                    conn_inv.close()

                    conn_po = sqlite3.connect(DB_PURCHASE_PATH)
                    c_po = conn_po.cursor()
                    c_po.execute("UPDATE purchases SET gudang = ? WHERE gudang = ?", (nama, old["nama"]))
                    conn_po.commit()
                    conn_po.close()

                record_audit("EDIT_GUDANG", "Master Gudang", f"{old['nama']} -> {nama} (Prefix: {prefix})", f"Sub-lokasi: {sub_lokasi}")
                self._send_json({"success": True, "gudang_list": get_daftar_gudang()})
            except sqlite3.IntegrityError:
                self._send_json({"error": "Nama gudang tersebut sudah dipakai."}, 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/master-barang/"):
            conn = None
            try:
                master_id = int(clean_path.split("/")[-1])
                data = self._read_json()
                nama = str(data.get("nama_barang", "")).strip()
                kode = str(data.get("kode_barang", "")).strip().upper()
                gudang = str(data.get("gudang", "Gudang ME")).strip() or "Gudang ME"
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                ada_expired = 1 if data.get("ada_expired") in (1, "1", True, "true") or gudang == "Gudang Makanan" else 0
                kategori = str(data.get("kategori", "Umum")).strip() or "Umum"
                satuan = str(data.get("satuan", "Pcs")).strip() or "Pcs"
                stok_min = max(parse_qty(data.get("stok_minimum", 0)), 0)
                harga_beli = max(int(data.get("harga_beli", 0) or 0), 0)
                harga_jual = max(int(data.get("harga_jual", 0) or 0), 0)
                if not nama or not kode:
                    self._send_json({"error": "Kode dan nama barang wajib diisi"}, 400)
                    return
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                old = cursor.execute("SELECT * FROM master_barang WHERE id = ?", (master_id,)).fetchone()
                if not old:
                    conn.close()
                    self._send_json({"error": "Barang tidak ditemukan"}, 404)
                    return
                cursor.execute(
                    "UPDATE master_barang SET kode_barang = ?, nama_barang = ?, gudang = ?, sub_lokasi = ?, ada_expired = ?, kategori = ?, satuan = ?, stok_minimum = ?, harga_beli = ?, harga_jual = ? WHERE id = ?",
                    (kode, nama, gudang, sub_lokasi, ada_expired, kategori, satuan, stok_min, harga_beli, harga_jual, master_id),
                )
                # Jika nama/kode/gudang berubah, samakan di seluruh riwayat transaksi
                cursor.execute(
                    "UPDATE items SET nama_barang = ?, kode_barang = ?, gudang = ? WHERE lower(trim(nama_barang)) = lower(trim(?))",
                    (nama, kode, gudang, old["nama_barang"]),
                )
                conn.commit()
                conn.close()
                create_auto_backup()
                self._send_json({"success": True})
            except sqlite3.IntegrityError:
                if conn: conn.close()
                self._send_json({"error": "Kode barang atau nama barang sudah dipakai barang lain"}, 400)
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/purchases/"):
            conn = None
            try:
                po_id = int(clean_path.split("/")[-1])
                data = self._read_json()
                tanggal_str = str(data.get("tanggal_lengkap", "")).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tanggal_str)
                no_po = str(data.get("no_po", "")).strip().upper()
                supplier = str(data.get("supplier", "")).strip()
                nama_barang = str(data.get("nama_barang", "")).strip()
                jumlah = max(parse_qty(data.get("jumlah", 0)), 0)
                harga_satuan = max(int(data.get("harga_satuan", 0) or 0), 0)
                total_harga = int(round(jumlah * harga_satuan))
                status = str(data.get("status", "dipesan")).strip().lower()
                if status not in ("pengajuan", "dipesan", "diterima", "batal"):
                    status = "dipesan"
                gudang = str(data.get("gudang", "")).strip()
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                tanggal_expired = str(data.get("tanggal_expired", "")).strip()
                lokasi_tujuan = str(data.get("lokasi_tujuan", "")).strip()
                keterangan = str(data.get("keterangan", "")).strip()

                conn_m = sqlite3.connect(DB_ITEMS_PATH)
                conn_m.row_factory = dict_factory
                cm = conn_m.cursor()
                master = find_master_barang(cm, nama_barang)
                if not master:
                    conn_m.close()
                    self._send_json({"error": f"Barang '{nama_barang}' belum ada di Master Barang."}, 400)
                    return
                nama_barang = master["nama_barang"]
                kode_barang = master["kode_barang"]
                kategori = master["kategori"]
                satuan = master["satuan"]
                if not gudang:
                    gudang = master.get("gudang") or "Gudang ME"
                if not sub_lokasi:
                    sub_lokasi = master.get("sub_lokasi") or ""
                if not lokasi_tujuan:
                    lokasi_tujuan = f"{gudang}" + (f" - {sub_lokasi}" if sub_lokasi else "")
                if harga_satuan > 0:
                    cm.execute("UPDATE master_barang SET harga_beli = ? WHERE id = ?", (harga_satuan, master["id"]))
                    conn_m.commit()
                conn_m.close()

                conn = sqlite3.connect(DB_PURCHASE_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                old = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                if not old:
                    conn.close()
                    self._send_json({"error": "Data PO tidak ditemukan"}, 404)
                    return
                if not no_po:
                    no_po = old["no_po"] or generate_no_po(cursor, tgl_lengkap)

                stok_masuk_id = old.get("stok_masuk_id")
                received_at = old.get("received_at") or ""
                if status == "diterima" and not stok_masuk_id:
                    stok_masuk_id = catat_barang_masuk_dari_po({
                        "no_po": no_po,
                        "tanggal_lengkap": tgl_lengkap,
                        "supplier": supplier,
                        "kode_barang": kode_barang,
                        "nama_barang": nama_barang,
                        "kategori": kategori,
                        "satuan": satuan,
                        "jumlah": jumlah,
                        "harga_satuan": harga_satuan,
                        "gudang": gudang,
                        "sub_lokasi": sub_lokasi,
                        "tanggal_expired": tanggal_expired,
                        "lokasi_tujuan": lokasi_tujuan,
                        "keterangan": keterangan,
                    })
                    received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                cursor.execute(
                    """
                    UPDATE purchases SET
                        no_po = ?, tanggal_lengkap = ?, tanggal = ?, bulan = ?, tahun = ?,
                        supplier = ?, kode_barang = ?, nama_barang = ?, kategori = ?, satuan = ?,
                        jumlah = ?, harga_satuan = ?, total_harga = ?, status = ?,
                        lokasi_tujuan = ?, keterangan = ?, stok_masuk_id = ?, received_at = ?,
                        gudang = ?, sub_lokasi = ?, tanggal_expired = ?
                    WHERE id = ?
                    """,
                    (
                        no_po, tgl_lengkap, tgl, bln, thn,
                        supplier, kode_barang, nama_barang, kategori, satuan,
                        jumlah, harga_satuan, total_harga, status,
                        lokasi_tujuan, keterangan, stok_masuk_id, received_at,
                        gudang, sub_lokasi, tanggal_expired, po_id,
                    ),
                )
                conn.commit()
                updated = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                conn.close()
                create_auto_backup()
                self._send_json({"purchase": updated})
            except Exception as e:
                if conn: conn.close()
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/items/"):
            try:
                item_id = int(clean_path.split("/")[-1])
                data = self._read_json()
                tanggal_str = str(data.get("tanggal_lengkap", "")).strip()
                tgl_lengkap, tgl, bln, thn = parse_date_parts(tanggal_str)

                kategori = str(data.get("kategori", "Umum")).strip() or "Umum"
                nama_barang = str(data.get("nama_barang", "")).strip()
                satuan = str(data.get("satuan", "Pcs")).strip() or "Pcs"
                jenis_transaksi = str(data.get("jenis_transaksi", "masuk")).strip().lower()
                if jenis_transaksi not in ("masuk", "keluar"):
                    jenis_transaksi = "masuk"

                jumlah_sebelumnya = parse_qty(data.get("jumlah_sebelumnya", 0))
                jumlah_order = parse_qty(data.get("jumlah_order", 0))
                total_akhir = hitung_total_akhir(jumlah_sebelumnya, jumlah_order, jenis_transaksi)
                pemakaian_dimana = str(data.get("pemakaian_dimana", "")).strip()
                sub_lokasi = str(data.get("sub_lokasi", "")).strip()
                tanggal_expired = str(data.get("tanggal_expired", "")).strip()
                keterangan = str(data.get("keterangan", "")).strip()

                if not nama_barang:
                    self._send_json({"error": "Nama barang wajib diisi"}, 400)
                    return

                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()

                master = find_master_barang(cursor, nama_barang)
                if not master:
                    conn.close()
                    self._send_json({"error": f"Barang '{nama_barang}' belum terdaftar di Master Barang."}, 400)
                    return
                nama_barang = master["nama_barang"]
                kode_barang = master["kode_barang"]
                gudang = str(data.get("gudang", "")).strip() or master.get("gudang") or "Gudang ME"
                if not sub_lokasi:
                    sub_lokasi = master.get("sub_lokasi") or ""

                if total_akhir < 0:
                    conn.close()
                    self._send_json({"error": f"Stok tidak mencukupi! Stok sebelumnya {jumlah_sebelumnya}, barang keluar {jumlah_order} (sisa {total_akhir})."}, 400)
                    return

                cursor.execute(
                    """
                    UPDATE items SET
                        tanggal_lengkap = ?, tanggal = ?, bulan = ?, tahun = ?,
                        kategori = ?, nama_barang = ?, satuan = ?, jenis_transaksi = ?,
                        jumlah_sebelumnya = ?, jumlah_order = ?, total_akhir = ?,
                        pemakaian_dimana = ?, keterangan = ?, kode_barang = ?,
                        gudang = ?, sub_lokasi = ?, tanggal_expired = ?
                    WHERE id = ?
                    """,
                    (
                        tgl_lengkap, tgl, bln, thn,
                        kategori, nama_barang, satuan, jenis_transaksi,
                        jumlah_sebelumnya, jumlah_order, total_akhir,
                        pemakaian_dimana, keterangan, kode_barang,
                        gudang, sub_lokasi, tanggal_expired, item_id
                    ),
                )
                conn.commit()
                cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
                updated_item = cursor.fetchone()
                conn.close()

                if not updated_item:
                    self._send_json({"error": "Data tidak ditemukan"}, 404)
                    return

                record_audit(
                    "EDIT_TRANSAKSI",
                    "Gudang",
                    f"ID #{item_id}: {nama_barang} ({jenis_transaksi}) {jumlah_order} {satuan} (Sisa {total_akhir})",
                    f"Tgl: {tgl_lengkap} | Gudang: {gudang} | Lokasi: {pemakaian_dimana}"
                )
                create_auto_backup()
                self._send_json({"item": updated_item}, 200)
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path.rstrip("/")

        # DELETE /api/master-gudang/<id>
        if clean_path.startswith("/api/master-gudang/"):
            try:
                gud_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_SETTINGS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()
                g = c.execute("SELECT * FROM master_gudang WHERE id = ?", (gud_id,)).fetchone()
                if not g:
                    conn.close()
                    self._send_json({"error": "Gudang tidak ditemukan"}, 404)
                    return

                # Pastikan tidak ada barang di gudang ini sebelum dihapus
                conn_inv = sqlite3.connect(DB_ITEMS_PATH)
                c_inv = conn_inv.cursor()
                count_item = c_inv.execute("SELECT COUNT(*) FROM master_barang WHERE gudang = ?", (g["nama"],)).fetchone()[0]
                conn_inv.close()

                if count_item > 0:
                    conn.close()
                    self._send_json({"error": f"Gudang '{g['nama']}' masih memiliki {count_item} barang terdaftar. Pindahkan atau hapus barangnya terlebih dahulu."}, 400)
                    return

                c.execute("DELETE FROM master_gudang WHERE id = ?", (gud_id,))
                conn.commit()
                conn.close()

                record_audit("HAPUS_GUDANG", "Master Gudang", f"Hapus {g['nama']}", "")
                self._send_json({"success": True, "gudang_list": get_daftar_gudang()})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/master-barang/"):
            try:
                master_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                m = cursor.execute("SELECT * FROM master_barang WHERE id = ?", (master_id,)).fetchone()
                if not m:
                    conn.close()
                    self._send_json({"error": "Barang tidak ditemukan"}, 404)
                    return
                used = cursor.execute(
                    "SELECT COUNT(*) AS n FROM items WHERE lower(trim(nama_barang)) = lower(trim(?))",
                    (m["nama_barang"],),
                ).fetchone()["n"]
                if used > 0:
                    conn.close()
                    self._send_json({"error": f"Barang ini sudah punya {used} riwayat transaksi, tidak bisa dihapus."}, 400)
                    return
                cursor.execute("DELETE FROM master_barang WHERE id = ?", (master_id,))
                conn.commit()
                conn.close()
                create_auto_backup()
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/items/"):
            try:
                item_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                item_lama = cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
                cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()

                if deleted == 0:
                    self._send_json({"error": "Data tidak ditemukan"}, 404)
                    return

                if item_lama:
                    record_audit(
                        "HAPUS_TRANSAKSI",
                        "Gudang",
                        f"ID #{item_id}: {item_lama.get('nama_barang')} ({item_lama.get('jenis_transaksi')}) {item_lama.get('jumlah_order')} {item_lama.get('satuan')}",
                        f"Tgl: {item_lama.get('tanggal_lengkap')} | Lokasi: {item_lama.get('pemakaian_dimana')}"
                    )
                create_auto_backup()
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/purchases/"):
            try:
                po_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_PURCHASE_PATH)
                conn.row_factory = dict_factory
                cursor = conn.cursor()
                po_lama = cursor.execute("SELECT * FROM purchases WHERE id = ?", (po_id,)).fetchone()
                cursor.execute("DELETE FROM purchases WHERE id = ?", (po_id,))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()
                if deleted == 0:
                    self._send_json({"error": "Data PO tidak ditemukan"}, 404)
                    return

                if po_lama:
                    record_audit(
                        "HAPUS_PO",
                        "Purchasing",
                        f"PO #{po_lama.get('no_po')}: {po_lama.get('nama_barang')} ({po_lama.get('jumlah')} {po_lama.get('satuan')})",
                        f"Supplier: {po_lama.get('supplier')} | Status: {po_lama.get('status')}"
                    )
                create_auto_backup()
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/categories/"):
            try:
                cat_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_CATEGORIES_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()

                if deleted == 0:
                    self._send_json({"error": "Kategori tidak ditemukan"}, 404)
                    return

                create_auto_backup()
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        if clean_path.startswith("/api/recipes/"):
            try:
                rcp_id = int(clean_path.split("/")[-1])
                conn = sqlite3.connect(DB_ITEMS_PATH)
                conn.row_factory = dict_factory
                c = conn.cursor()
                rcp = c.execute("SELECT * FROM recipes WHERE id = ?", (rcp_id,)).fetchone()
                if not rcp:
                    conn.close()
                    self._send_json({"error": "Resep tidak ditemukan"}, 404)
                    return
                c.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (rcp_id,))
                c.execute("DELETE FROM recipes WHERE id = ?", (rcp_id,))
                conn.commit()
                conn.close()
                create_auto_backup()
                record_audit("HAPUS_RESEP", "F&B", f"Hapus Resep: {rcp.get('nama_menu')} ({rcp.get('kode_menu')})", "")
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)
            return

        self._send_json({"error": "Not found"}, 404)


def run_server(port=8090, open_browser=False):
    init_db()
    # Jika ada proses server versi lama yang masih berjalan di port ini, minta berhenti otomatis
    try:
        import time
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/shutdown", timeout=1)
        time.sleep(0.6)
    except Exception:
        pass
    # Bind ke 0.0.0.0 agar bisa diakses dari komputer/laptop/HP lain via LAN & Wi-Fi
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, AppHandler)
    local_url = f"http://127.0.0.1:{port}"
    lan_ips = get_lan_ips()
    print(f"============================================================")
    print(f" Server Aplikasi Inventori & Purchase Berjalan!")
    print(f" -> Akses di Komputer Ini : {local_url}")
    print(f" -> Halaman Purchase (PO) : {local_url}/purchase.html")
    if lan_ips:
        for ip in lan_ips:
            print(f" -> Akses LAN / Wi-Fi     : http://{ip}:{port}")
            print(f" -> Purchase via Wi-Fi    : http://{ip}:{port}/purchase.html")
    print(f" Lokasi Database          : {BASE_DIR}")
    print(f" Jangan tutup jendela ini selama menggunakan aplikasi.")
    print(f"============================================================")
    if open_browser:
        webbrowser.open(local_url)
    httpd.serve_forever()


if __name__ == "__main__":
    # Jika dijalankan dari file .EXE langsung, otomatis buka browser
    auto_open = getattr(sys, "frozen", False) or ("--open" in sys.argv)
    run_server(8090, open_browser=auto_open)
