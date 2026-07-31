"""
Job Hunter Bot
==============
Scraper multi-sumber (Kalibrr, Dealls, Talentics, JobSpy/LinkedIn/Indeed)
yang menyimpan hasil ke database Notion. Didesain untuk dijalankan berkala
(3x/hari) via GitHub Actions.
"""

import os
import re
import time
import random
import logging
from datetime import datetime

import requests
from jobspy import scrape_jobs
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("job_hunter_bot")

# ==========================================
# KONFIGURASI NOTION
# ==========================================
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ==========================================
# ANTI-BLOKIR: USER-AGENT ROTATION
# ==========================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Edg/124.0.0.0 Safari/537.36",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


# ==========================================
# KONFIGURASI TARGET
# ==========================================
# SEARCH_TERMS = query yang benar-benar dikirim ke API tiap portal
# (Kalibrr/Dealls/Talentics) & ke JobSpy (LinkedIn/Indeed). Ini adalah
# "kail" utama, jadi disusun per KELUARGA PERAN agar cakupan luas untuk
# profil IT kamu, sementara SOFT_KEYWORDS/EXCLUDE_KEYWORDS menyaring
# presisinya di lapis berikutnya.
SEARCH_TERMS = {
    # Format: "term": (target_lowongan_baru, batas_halaman_aman)
    #
    # Ada dua jenis term:
    # 1. TERM UTAMA - istilah umum/density tinggi yang HRD IT paling sering
    #    pakai. Diberi kuota buru penuh (target & halaman lebih besar).
    # 2. TERM PELENGKAP/SINONIM - menutup celah ketika HRD menulis judul
    #    yang tidak eksplisit (mis. "Web Designer" utk peran UI/UX, atau
    #    "Web Developer" tanpa embel-embel fullstack/backend/frontend).
    #    Diberi kuota kecil karena fungsinya menangkap "ekor panjang", bukan
    #    jadi sumber volume utama - supaya tidak menghabiskan kuota request
    #    API untuk istilah yang hasilnya sedikit relevan.
    #
    # CATATAN: kata generik tunggal seperti "manager"/"analyst"/"engineer"/
    # "designer" SENGAJA tidak dipakai sendirian sebagai term, karena di
    # portal Indonesia mayoritas hasilnya non-IT (Sales Manager, Financial
    # Analyst, Mechanical Engineer, dst) - bot akan menghabiskan banyak
    # halaman menyaring noise sebelum ketemu yang relevan, bukan menambah
    # hasil. Presisi tetap dijaga di level frasa (2 kata), sementara
    # cakupan sinonim diperluas lewat entri pelengkap di bawah.

    # --- Software Engineering (umum) ---
    "software engineer": (15, 15),
    "programmer": (15, 15),
    "software developer": (8, 8),

    # --- Web: Backend / Frontend / Fullstack ---
    "backend developer": (15, 15),
    "frontend developer": (15, 15),
    "fullstack developer": (15, 15),
    "web developer": (8, 8),
    ".net developer": (15, 15),

    # --- Mobile ---
    "mobile developer": (10, 10),
    "android developer": (8, 8),
    "ios developer": (8, 8),

    # --- QA / Testing ---
    "quality assurance": (15, 15),
    "qa engineer": (10, 10),
    "software tester": (8, 8),

    # --- Analyst ---
    "system analyst": (15, 15),
    "business analyst": (15, 15),
    "it business analyst": (8, 8),

    # --- Manajemen Proyek/Produk ---
    "project manager": (15, 15),
    "product manager": (10, 10),
    "scrum master": (8, 8),

    # --- UI/UX ---
    "ui ux designer": (15, 15),
    "ui designer": (8, 8),                  # pelengkap: HRD sering tulis salah satu saja
    "ux designer": (8, 8),                  # pelengkap
    "web designer": (8, 8),                 # pelengkap: kadang dipakai utk peran UI/UX
    "product designer": (8, 8),             # pelengkap: istilah umum di startup

    # --- Data / AI ---
    "data engineer": (10, 10),
    "data scientist": (8, 8),               # pelengkap
    "data analyst": (8, 8),                 # pelengkap: dekat dgn system/business analyst
    "machine learning engineer": (10, 10),
    "ai engineer": (10, 10),
}

# LAPIS PERTAMA (Soft Filter): dipakai untuk menyaring cepat berdasarkan JUDUL
# lowongan saja (longgar). Dicocokkan berbasis word-boundary lewat
# keyword_hit() di bawah - bukan substring polos - supaya keyword pendek
# seperti "ai"/"ux"/"qa" tidak salah tangkap kata lain.
SOFT_KEYWORDS = [
    # Umum software
    "developer", "engineer", "programmer", "software", "software engineer",
    # Backend / Frontend / Fullstack
    "backend", "back-end", "frontend", "front-end",
    "fullstack", "full stack", "full-stack",
    # Mobile
    "mobile", "android", "ios",
    # Stack spesifik (biar nangkap judul yg sudah sebut teknologi)
    ".net", "dotnet", "react", "vue", "angular", "node", "laravel", "php",
    "java", "kotlin", "swift", "golang", "flutter",
    # Data / AI
    "data", "ai", "artificial intelligence", "machine learning", "ml",
    "data engineer", "data scientist", "data analyst",
    # QA / Testing
    "quality assurance", "qa", "tester", "testing", "quality", "automation",
    "sdet",
    # Analyst
    "analyst", "system analyst", "business analyst",
    # Manajemen
    "project manager", "product manager", "scrum", "manager", "scrum master",
    # UI/UX
    "ui", "ux", "designer", "design", "ui/ux", "ui ux",
    # DevOps / Cloud (dekat dgn profil developer)
    "devops", "cloud", "sre",
]

# DAFTAR HITAM: Jika judul mengandung kata ini, lowongan dibuang meskipun
# lolos SOFT_KEYWORDS. Dicocokkan dengan word-boundary juga.
EXCLUDE_KEYWORDS = [
    "marketing", "sales", "hr", "human resource", "finance", "accounting",
    "content", "social media", "copywriter", "graphic designer",
    "business development", "admin", "support", "customer", "secretary",
    "legal", "operation",
    # Catatan: "entry" SENGAJA tidak dimasukkan - "Entry Level Software
    # Engineer" tetap relevan buat profil kamu. "graphic" diganti jadi
    # "graphic designer" supaya tidak ikut membuang "UI/UX Designer" yang
    # judulnya kadang menyebut "graphic" sebagai skill tambahan.
]

# LAPIS KEDUA (Strict Keywords): saringan presisi tinggi berbasis deskripsi,
# dipakai khusus di engine JobSpy (LinkedIn/Indeed) karena di sana kita
# punya akses ke teks deskripsi lengkap, bukan cuma judul.
KEYWORDS = [
    # Fullstack / Backend / Mobile
    "c#", ".net", "laravel", "php", "react", "node.js", "typescript",
    "dart", "javascript", "flutter", "kotlin", "swift", "golang", "java",
    "vue", "angular", "html", "css", "rest api", "graphql", "backend", "frontend",
    "fullstack", "full stack", "full-stack", "mobile", "android", "ios",
    # AI / Data / Hardware
    "python", "machine learning", "edge", "computer vision", "inference",
    "smote", "arm", "bare-metal", "data engineer", "data scientist",
    # Architecture / DevOps / DB
    "postgresql", "supabase", "clean architecture", "ci/cd", "agile", "scrum",
    "docker", "kubernetes", "aws", "azure", "gcp", "sql",
    # QA / Testing
    "selenium", "cypress", "postman", "automation testing", "manual testing",
    "software testing", "test case", "sdet",
    # Analyst
    "system analysis", "business analysis", "uml", "erd", "user story",
    "requirement gathering", "jira", "confluence",
    # UI/UX / Desain
    "figma", "ui/ux", "tailwind", "bootstrap", "material ui", "adobe xd", "photoshop", "illustrator",
]

LOCATION = "Indonesia"

# ==========================================
# PENCOCOKAN KEYWORD BERBASIS WORD-BOUNDARY
# ==========================================
_boundary_pattern_cache: dict = {}


def _compile_boundary_pattern(keyword: str) -> re.Pattern:
    """Membangun regex untuk satu keyword dengan word-boundary di sisi yang
    diawali/diakhiri karakter alfanumerik. Untuk keyword yang diawali/diakhiri
    karakter non-alfanumerik (mis. ".net", "c#", "ci/cd"), boundary di sisi
    itu dilepas karena \\b tidak berfungsi baik pada karakter non-word.
    """
    kw = keyword.lower().strip()
    escaped = re.escape(kw)
    left = r"\b" if kw[0].isalnum() else ""
    right = r"\b" if kw[-1].isalnum() else ""
    return re.compile(left + escaped + right)


def keyword_hit(text: str, keywords) -> bool:
    """True jika salah satu keyword ditemukan sebagai KATA UTUH (bukan
    sekadar substring) di dalam text. Mencegah "ai" salah tangkap "Retail",
    "qa" salah tangkap "square", dst."""
    text_lower = text.lower()
    for kw in keywords:
        pattern = _boundary_pattern_cache.get(kw)
        if pattern is None:
            pattern = _compile_boundary_pattern(kw)
            _boundary_pattern_cache[kw] = pattern
        if pattern.search(text_lower):
            return True
    return False


# ==========================================
# TARGET "BURU SAMPAI DAPAT N BARU"
# ==========================================
# Kuota per-term (target lowongan baru & batas halaman aman) sudah
# didefinisikan langsung di dalam SEARCH_TERMS di atas -> lihat komentar
# di sana. Ini membuat term "utama" (density tinggi, jelas IT) diburu
# lebih dalam, sementara term "pelengkap/sinonim" (menutup celah variasi
# judul) cukup diburu sedikit supaya tidak boros request API.

# Khusus JobSpy: karena library-nya tidak mendukung cursor pagination
# (hanya `results_wanted`), strateginya adalah menaikkan results_wanted
# secara bertahap dan menembak ulang sampai target tercapai / tidak ada
# progres lagi / percobaan maksimum tercapai.
JOBSPY_MAX_ATTEMPTS = 3
JOBSPY_RESULTS_STEP = 15

# ==========================================
# ANTI-BLOKIR: RETRY + EXPONENTIAL BACKOFF
# ==========================================
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryableHTTPError(Exception):
    """Dilempar saat response HTTP mengindikasikan error sementara
    (rate limit atau error server) supaya tenacity tahu harus retry."""

    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} dari {url}")


def _log_retry(retry_state):
    exc = retry_state.outcome.exception()
    logger.warning(
        f"   ↻ Retry percobaan ke-{retry_state.attempt_number} "
        f"(menunggu backoff)... Penyebab: {exc}"
    )


RETRY_CONFIG = dict(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.exceptions.RequestException, RetryableHTTPError)),
    before_sleep=_log_retry,
    reraise=True,
)


@retry(**RETRY_CONFIG)
def safe_get(url, headers=None, session=None, timeout=15, **kwargs):
    """GET request dengan UA rotation otomatis + retry/backoff.
    `reraise=True` -> setelah semua percobaan gagal, exception asli
    dilempar ke pemanggil supaya bisa ditangani per-engine (graceful degradation).
    """
    merged_headers = dict(headers or {})
    merged_headers.setdefault("User-Agent", random_user_agent())
    requester = session.get if session is not None else requests.get
    response = requester(url, headers=merged_headers, timeout=timeout, **kwargs)
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableHTTPError(response.status_code, url)
    return response


@retry(**RETRY_CONFIG)
def safe_post(url, headers=None, json=None, timeout=15):
    merged_headers = dict(headers or {})
    merged_headers.setdefault("User-Agent", random_user_agent())
    response = requests.post(url, headers=merged_headers, json=json, timeout=timeout)
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableHTTPError(response.status_code, url)
    return response


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=_log_retry,
    reraise=True,
)
def safe_scrape_jobs(**kwargs):
    """Wrapper JobSpy dengan retry. JobSpy tidak mengekspos tipe exception
    spesifik untuk rate-limit, jadi kita retry Exception generik tapi
    dibatasi 3x percobaan agar tidak menggantung lama jika errornya permanen."""
    return scrape_jobs(**kwargs)


# ==========================================
# NOTION: BACA & TULIS
# ==========================================
def get_existing_links() -> set:
    """Mengambil SELURUH link yang sudah ada di database Notion
    menggunakan paginasi penuh (start_cursor/has_more), bukan cuma 100 baris pertama."""
    logger.info("Mengecek ingatan bot di Notion (paginasi penuh)...")
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    existing_links = set()
    has_more = True
    start_cursor = None
    page_count = 0

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            response = safe_post(url, headers=HEADERS, json=payload)
        except Exception as e:
            logger.error(f"❌ Gagal membaca Notion setelah retry habis: {e}")
            break

        if response.status_code != 200:
            logger.error(f"❌ Notion API error {response.status_code}: {response.text[:300]}")
            break

        data = response.json()
        results = data.get("results", [])
        for page in results:
            link_url = page.get("properties", {}).get("Link", {}).get("url")
            if link_url:
                existing_links.add(link_url)

        page_count += 1
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
        logger.info(f"   ...halaman Notion ke-{page_count} terbaca ({len(results)} entri)")

    logger.info(f"Ditemukan total {len(existing_links)} lowongan tersimpan di database.\n")
    return existing_links


def send_to_notion(title: str, link: str) -> None:
    url = "https://api.notion.com/v1/pages"
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Link": {"url": link},
            "Date": {"date": {"start": datetime.now().isoformat()}},
        },
    }
    try:
        response = safe_post(url, headers=HEADERS, json=data)
        if response.status_code == 200:
            logger.info(f"✅ BARU: {title}")
        else:
            logger.error(f"❌ Gagal kirim ke Notion ({response.status_code}): {response.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Error koneksi Notion setelah retry habis: {e}")


# ==========================================
# ENGINE 1: KALIBRR
# ==========================================
def fetch_kalibrr(existing_links: set) -> int:
    logger.info("[ENGINE 1] Membaca API Kalibrr...")
    match_count = 0

    for term, (target_new, max_pages) in SEARCH_TERMS.items():
        term_new_count = 0
        page = 1

        while term_new_count < target_new and page <= max_pages:
            logger.info(
                f"🔍 Kalibrr | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{target_new})"
            )
            encoded_term = term.replace(" ", "%20")
            api_url = (
                f"https://www.kalibrr.id/api/job_board/search"
                f"?query={encoded_term}&limit=30&page={page}"
            )
            try:
                response = safe_get(api_url, headers={"Accept": "application/json"})
            except Exception as e:
                logger.warning(f"⚠️ Kalibrr '{term}' hal.{page} gagal total setelah retry: {e}")
                break

            if response.status_code != 200:
                logger.warning(f"⚠️ Kalibrr status {response.status_code} untuk '{term}' hal.{page}")
                break

            try:
                res_json = response.json()
            except ValueError:
                logger.warning(f"⚠️ Kalibrr: response bukan JSON valid ('{term}' hal.{page})")
                break

            jobs = res_json.get("jobs", [])
            if not jobs:
                logger.info(f"   💡 Data Kalibrr utk '{term}' sudah habis di halaman {page}.")
                break

            for job in jobs:
                if not isinstance(job, dict):
                    continue

                title = job.get("name", "Tanpa Judul")
                company_info = job.get("company_info", {})
                company = "Perusahaan Tidak Diketahui"
                company_code = ""
                if isinstance(company_info, dict):
                    company = company_info.get("name", "Perusahaan Tidak Diketahui")
                    company_code = company_info.get("code", "")

                job_id = job.get("id", "")
                link = f"https://www.kalibrr.id/c/{company_code}/jobs/{job_id}"
                notion_title = f"[Kalibrr: {company}] - {title}"

                if keyword_hit(title, EXCLUDE_KEYWORDS):
                    continue
                if not keyword_hit(title, SOFT_KEYWORDS):
                    continue
                if link in existing_links:
                    continue

                send_to_notion(notion_title, link)
                existing_links.add(link)
                match_count += 1
                term_new_count += 1
                time.sleep(1)

                if term_new_count >= target_new:
                    logger.info(
                        f"   🎯 Target {target_new} lowongan baru "
                        f"utk '{term}' tercapai di Kalibrr."
                    )
                    break

            page += 1

        if page > max_pages:
            logger.info(
                f"   🛑 Batas aman {max_pages} halaman utk '{term}' tercapai "
                f"(baru didapat: {term_new_count})."
            )

    return match_count


# ==========================================
# ENGINE 2: DEALLS
# ==========================================
def fetch_dealls(existing_links: set) -> int:
    logger.info("[ENGINE 2] Membaca API Dealls (SejutaCita)...")
    match_count = 0
    dealls_headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://dealls.com",
        "Referer": "https://dealls.com/",
        "x-client-app-name": "Deall-Talent-Web",
        "x-client-app-version": "2.49.46",
    }

    for term, (target_new, max_pages) in SEARCH_TERMS.items():
        term_new_count = 0
        page = 1

        while term_new_count < target_new and page <= max_pages:
            logger.info(
                f"🔍 Dealls | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{target_new})"
            )
            encoded_term = term.replace(" ", "%20")
            api_url = (
                "https://api.sejutacita.id/v1/explore-job/job"
                f"?keyword={encoded_term}&page={page}&sortParam=mostRelevant"
                "&sortBy=asc&boostTheBoostedJob=true&published=true&limit=20"
                "&status=active&externalPlatformApplyUrlSet=null"
            )
            try:
                response = safe_get(api_url, headers=dealls_headers)
            except Exception as e:
                logger.warning(f"⚠️ Dealls '{term}' hal.{page} gagal total setelah retry: {e}")
                break

            if response.status_code != 200:
                logger.warning(f"⚠️ Dealls status {response.status_code} untuk '{term}' hal.{page}")
                break

            try:
                res_json = response.json()
            except ValueError:
                logger.warning(f"⚠️ Dealls: response bukan JSON valid ('{term}' hal.{page})")
                break

            jobs = []
            data_node = res_json.get("data")
            if isinstance(data_node, list):
                jobs = data_node
            elif isinstance(data_node, dict):
                for _, value in data_node.items():
                    if isinstance(value, list):
                        jobs = value
                        break

            if not jobs:
                logger.info(f"   💡 Data Dealls utk '{term}' sudah habis di halaman {page}.")
                break

            for job in jobs:
                if not isinstance(job, dict):
                    continue

                title = job.get("role", "Tanpa Judul")
                company_info = job.get("company")
                company = "Unknown"
                company_slug = ""
                if isinstance(company_info, dict):
                    company = company_info.get("name", "Unknown")
                    company_slug = company_info.get("slug", "")
                job_slug = job.get("slug") or job.get("id", "")
                if company_slug:
                    link = f"https://dealls.com/loker/{job_slug}~{company_slug}"
                else:
                    link = f"https://dealls.com/loker/{job_slug}"
                notion_title = f"[Dealls: {company}] - {title}"

                if keyword_hit(title, EXCLUDE_KEYWORDS):
                    continue
                if not keyword_hit(title, SOFT_KEYWORDS):
                    continue
                if link in existing_links:
                    continue

                send_to_notion(notion_title, link)
                existing_links.add(link)
                match_count += 1
                term_new_count += 1
                time.sleep(1)

                if term_new_count >= target_new:
                    logger.info(
                        f"   🎯 Target {target_new} lowongan baru "
                        f"utk '{term}' tercapai di Dealls."
                    )
                    break

            page += 1

        if page > max_pages:
            logger.info(
                f"   🛑 Batas aman {max_pages} halaman utk '{term}' tercapai "
                f"(baru didapat: {term_new_count})."
            )

    return match_count


# ==========================================
# ENGINE 3: TALENTICS
# ==========================================
def fetch_talentics(existing_links: set) -> int:
    logger.info("[ENGINE 3] Membaca API Jobs Talentics (mode sesi otomatis)...")
    match_count = 0
    session = requests.Session()
    session.headers.update({
        "User-Agent": random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://jobs.talentics.id/",
    })

    try:
        logger.info("   [SISTEM] Mengambil cookie & CSRF token awal...")
        init_res = safe_get("https://jobs.talentics.id/jobs", session=session, timeout=10)
        csrf_token = ""
        match = re.search(r'<meta name="csrf-token" content="(.*?)">', init_res.text)
        if match:
            csrf_token = match.group(1)
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": csrf_token,
        })
        logger.info("   [SISTEM] Sesi berhasil diamankan, memulai pencarian...")
    except Exception as e:
        logger.error(f"   ⚠️ Gagal mengambil sesi awal Talentics, engine dilewati: {e}")
        return match_count

    for term, (target_new, max_pages) in SEARCH_TERMS.items():
        term_new_count = 0
        page = 1

        while term_new_count < target_new and page <= max_pages:
            logger.info(
                f"🔍 Talentics | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{target_new})"
            )
            encoded_term = term.replace(" ", "%20")
            api_url = f"https://jobs.talentics.id/ajaxs/jobs?search={encoded_term}&page={page}"
            try:
                response = safe_get(api_url, session=session, timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ Talentics '{term}' hal.{page} gagal total setelah retry: {e}")
                break

            if response.status_code != 200:
                logger.warning(f"⚠️ Talentics menolak akses (status {response.status_code})")
                break

            try:
                res_json = response.json()
            except ValueError:
                logger.warning(f"⚠️ Talentics: response bukan JSON valid ('{term}' hal.{page})")
                break

            jobs = res_json.get("data", []) if isinstance(res_json, dict) else []
            if not jobs:
                logger.info(f"   💡 Data Talentics utk '{term}' sudah habis di halaman {page}.")
                break

            for job in jobs:
                if not isinstance(job, dict):
                    continue

                title = job.get("title") or job.get("name", "Tanpa Judul")
                org_info = job.get("organization") or {}
                company = job.get("organization_name") or org_info.get("name", "Unknown")
                org_slug = org_info.get("slug", "")
                job_slug = job.get("slug") or job.get("id", "")
                if org_slug and job_slug:
                    link = f"https://jobs.talentics.id/{org_slug}/{job_slug}"
                else:
                    link = job.get("url") or f"https://jobs.talentics.id/jobs/{job_slug}"
                notion_title = f"[Talentics: {company}] - {title}"

                if keyword_hit(title, EXCLUDE_KEYWORDS):
                    continue
                if not keyword_hit(title, SOFT_KEYWORDS):
                    continue
                if link in existing_links:
                    continue

                send_to_notion(notion_title, link)
                existing_links.add(link)
                match_count += 1
                term_new_count += 1
                time.sleep(1)

                if term_new_count >= target_new:
                    logger.info(
                        f"   🎯 Target {target_new} lowongan baru "
                        f"utk '{term}' tercapai di Talentics."
                    )
                    break

            page += 1
            time.sleep(2)  # anti-DDoS delay antar halaman

        if page > max_pages:
            logger.info(
                f"   🛑 Batas aman {max_pages} halaman utk '{term}' tercapai "
                f"(baru didapat: {term_new_count})."
            )

    return match_count


# ==========================================
# ENGINE 4: JOBSPY (LinkedIn & Indeed)
# ==========================================
def fetch_jobspy(existing_links: set) -> int:
    logger.info("[ENGINE 4] Memulai JobSpy (platform global)...")
    match_count = 0

    for term, (target_new, max_pages) in SEARCH_TERMS.items():
        term_new_count = 0
        results_wanted = JOBSPY_RESULTS_STEP
        attempt = 1

        while term_new_count < target_new and attempt <= JOBSPY_MAX_ATTEMPTS:
            logger.info(
                f"🔍 JobSpy | '{term}' | percobaan {attempt}/{JOBSPY_MAX_ATTEMPTS} "
                f"(results_wanted={results_wanted}, baru terkumpul: "
                f"{term_new_count}/{target_new})"
            )
            try:
                jobs = safe_scrape_jobs(
                    site_name=["linkedin", "indeed"],
                    search_term=term,
                    location=LOCATION,
                    results_wanted=results_wanted,
                    hours_old=72,
                    country_indeed="Indonesia",
                )
            except Exception as e:
                logger.warning(f"⚠️ JobSpy gagal total untuk '{term}' setelah retry: {e}")
                break

            if jobs is None or jobs.empty:
                logger.info(f"   💡 JobSpy tidak mengembalikan data lagi utk '{term}'.")
                break

            new_this_round = 0
            for _, row in jobs.iterrows():
                title = str(row.get("title", "Tanpa Judul"))
                description = str(row.get("description", ""))
                link = str(row.get("job_url", ""))
                company = str(row.get("company", "Unknown"))
                notion_title = f"[{company}] - {title}"

                if keyword_hit(title, EXCLUDE_KEYWORDS):
                    continue
                if not (keyword_hit(title, KEYWORDS) or keyword_hit(description, KEYWORDS)):
                    continue
                if not link or link in existing_links:
                    continue

                send_to_notion(notion_title, link)
                existing_links.add(link)
                match_count += 1
                term_new_count += 1
                new_this_round += 1
                time.sleep(1.5)

                if term_new_count >= target_new:
                    logger.info(
                        f"   🎯 Target {target_new} lowongan baru "
                        f"utk '{term}' tercapai di JobSpy."
                    )
                    break

            if new_this_round == 0:
                # Sudah minta lebih banyak data tapi tidak ada progres sama sekali ->
                # kemungkinan besar sisanya memang semua sudah pernah tersimpan.
                logger.info(
                    f"   💡 Tidak ada progres baru meski results_wanted={results_wanted}; "
                    f"berhenti utk '{term}'."
                )
                break

            attempt += 1
            results_wanted += JOBSPY_RESULTS_STEP

    return match_count


# ==========================================
# ORKESTRASI UTAMA (GRACEFUL DEGRADATION)
# ==========================================
def run_engine(name: str, func, existing_links: set) -> int:
    """Menjalankan satu engine dengan isolasi penuh - jika engine ini
    raise exception apa pun (bukan cuma yang sudah ditangani di dalamnya),
    bot mencatat error lengkap lalu tetap lanjut ke engine berikutnya."""
    logger.info(f"===== MEMULAI ENGINE: {name} =====")
    try:
        count = func(existing_links)
        logger.info(f"===== ENGINE {name} SELESAI: {count} lowongan baru =====\n")
        return count
    except Exception as e:
        logger.error(
            f"‼️ ENGINE {name} GAGAL TOTAL - dilewati, lanjut ke engine berikutnya. Detail: {e}",
            exc_info=True,
        )
        return 0


def main_execution():
    logger.info("========== JOB HUNTER BOT: MULAI EKSEKUSI ==========")
    existing_links = get_existing_links()

    engines = [
        ("Kalibrr", fetch_kalibrr),
        ("Dealls", fetch_dealls),
        ("Talentics", fetch_talentics),
        ("JobSpy", fetch_jobspy),
    ]

    total = 0
    for name, func in engines:
        total += run_engine(name, func, existing_links)

    logger.info(f"🎉 EKSEKUSI SELESAI! Total {total} peluang BARU berhasil dimasukkan ke Notion.")
    logger.info("========== JOB HUNTER BOT: SELESAI ==========")


if __name__ == "__main__":
    main_execution()