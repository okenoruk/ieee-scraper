# IEEE Xplore Scraper via UI Remote-Lib Proxy

Web scraper untuk mengambil data paper dari **IEEE Xplore** melalui proxy **remote-lib.ui.ac.id** (akses institusi Universitas Indonesia). Dirancang untuk kebutuhan **Systematic Literature Review (SLR)**.

## Fitur

- 🔐 Login otomatis ke SSO UI → remote-lib
- 🔍 Pencarian kata kunci di IEEE Xplore
- 📄 Scraping hingga 10 halaman hasil (~250 paper)
- 📝 Detail lengkap: abstrak, keywords, DOI, citations, PDF link
- 📊 Export ke CSV (kompatibel Excel)
- 🧩 Arsitektur extensible — mudah ditambahkan scraper untuk sumber lain

## Struktur Proyek

```
ieee-scraper/
├── main.py              # Entry-point utama
├── config.py            # Konfigurasi dari .env
├── auth.py              # Login SSO & navigasi proxy
├── export.py            # Export ke CSV
├── scrapers/
│   ├── __init__.py
│   ├── base.py          # BaseScraper + PaperRecord (abstract class)
│   └── ieee.py          # IEEE Xplore scraper
├── .env                 # Kredensial & konfigurasi (jangan commit!)
├── .gitignore
├── requirements.txt
└── output/              # Hasil scraping (CSV)
```

## Instalasi

```bash
pip install -r requirements.txt
playwright install chromium
```

## Penggunaan

### 1. Mode Standar (via SSO UI Proxy)
```bash
# Default (query & settings dari .env)
python main.py

# Filter paper 3 atau 5 tahun terakhir
python main.py --years 3
python main.py --years 5
python main.py --years off               # Nonaktifkan filter tahun (semua tahun)

# Custom query & batasi halaman
python main.py --query "deep learning" --pages 5

# Headless mode (tanpa browser window)
python main.py --headless

# Custom output filename
python main.py -o hasil_slr.csv
```

### 2. Mode Langsung (Direct - Tanpa Login SSO & Tanpa Proxy)
Jika Anda tidak ingin login SSO atau tidak memiliki kredensial proxy:

```bash
# Opsi A: Jalankan script direct_scraper.py
python direct_scraper.py --query "social network analysis" --years 3 --pages 3

# Opsi B: Gunakan flag --direct di main.py
python main.py --direct --query "machine learning" --years 5 --pages 5
```

Atau set `MODE=direct` dan `YEAR_FILTER=3` di file `.env`.

### 3. Re-scrape Khusus Data Belum Lengkap (Abstract, Authors, atau DOI Kosong)
Jika ada data yang saat di-scrape metadata pentingnya gagal terambil (misalnya `abstract`, `authors`, atau `doi` kosong akibat koneksi lambat atau layout SPA belum selesai render):

1. **Otomatis Sebelum Finalisasi**: Scraper secara otomatis mendeteksi semua record sebelum menyimpan CSV final. Jika ada paper dengan abstract, authors, atau DOI yang masih kosong, scraper akan melakukan scrape ulang secara presisi hanya pada data yang belum lengkap tersebut dengan multi-level fallback (DOM selector, `<meta>` tags di HTML `<head>`, dan state JavaScript `window.xplGlobal`).
2. **Re-scrape File CSV Eksisting**: Anda dapat memperbaiki file CSV yang sudah ada sebelumnya tanpa harus mengulang pencarian dari awal:

```bash
# Melalui mode direct (tanpa login)
python direct_scraper.py --rescrape-csv "output/ieee_Disaster_OR_Disaster_Response_AND_social_3y_2.csv"

# Atau melalui main.py
python main.py --direct --rescrape-csv "output/ieee_Disaster_OR_Disaster_Response_AND_social_3y_2.csv"
```

> [!TIP]
> **Gradual / Incremental Auto-Save**: Setiap kali 1 paper berhasil di-scrape dan diperkaya detailnya, paper tersebut langsung disimpan dan di-flush seketika ke file CSV. Jika script terhenti di tengah jalan (misal karena koneksi drop atau di-stop manual), seluruh data yang sudah di-scrape tetap tersimpan dengan aman tanpa hilang!

> [!NOTE]
> **Pembersihan Teks Otomatis**: Semua teks (judul, abstract, authors, keywords) otomatis dibersihkan dari karakter newline (`\n`), tab (`\t`), quote rusak, dan spasi ganda. Abstract dijamin menjadi 1 baris rapi per record sehingga tidak merusak parser CSV / Excel. Nama authors dan keywords juga otomatis di-deduplikasi tanpa kata sampah seperti "Learn more".

## Konfigurasi (.env)

```env
SSO_USERNAME=muhammad21
SSO_PASSWORD=your_password
SEARCH_QUERY=social network analysis
MAX_PAGES=10
MODE=proxy          # proxy / direct
YEAR_FILTER=3       # 3 (last 3 yrs), 5 (last 5 yrs), atau 0 (off/all)
OUTPUT_DIR=output
```

## Data yang Di-scrape

| Field              | Keterangan                                |
|--------------------|-------------------------------------------|
| `title`            | Judul paper                               |
| `authors`          | Daftar penulis (dipisah titik koma)       |
| `year`             | Tahun terbit                              |
| `abstract`         | Abstrak lengkap                           |
| `keywords`         | Keywords (dipisah titik koma)             |
| `publication_title`| Nama jurnal/konferensi/prosiding          |
| `publication_type` | Tipe: Conference, Journal, Early Access   |
| `publisher`        | Penerbit                                  |
| `volume`           | Volume                                    |
| `issue`            | Issue/nomor                               |
| `pages`            | Halaman (mis: 1-6)                        |
| `doi`              | Digital Object Identifier                 |
| `url`              | Link langsung ke paper                    |
| `pdf_url`          | Link PDF                                  |
| `citation_count`   | Jumlah sitasi                             |
| `ieee_access_type` | Tipe akses (Open Access / lainnya)        |
| `source_database`  | Sumber database (IEEE Xplore)             |

## Menambahkan Sumber Baru

Untuk menambah scraper sumber lain (Scopus, SpringerLink, dll.), buat class baru yang meng-inherit `BaseScraper`:

```python
# scrapers/scopus.py
from scrapers.base import BaseScraper, PaperRecord

class ScopusScraper(BaseScraper):
    name = "Scopus"

    def search(self, query: str) -> None:
        ...

    def scrape_results(self, max_pages: int = 10) -> list[PaperRecord]:
        ...

    def scrape_paper_detail(self, paper: PaperRecord) -> PaperRecord:
        ...
```
