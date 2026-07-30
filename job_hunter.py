"""
Job Hunter Bot
==============
Scraper multi-sumber (Kalibrr, Dealls, Talentics, JobSpy/LinkedIn/Indeed)
yang menyimpan hasil ke database Notion. Didesain untuk dijalankan berkala
(mis. 3x/hari) via GitHub Actions.

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
SEARCH_TERMS = [
    "fullstack developer",
    ".net developer",
    "ai engineer",
    "machine learning",
    "project manager",
    "ui ux designer",
]

# LAPIS PERTAMA (Soft Filter): dipakai untuk menyaring cepat berdasarkan JUDUL
# lowongan saja (longgar).
SOFT_KEYWORDS = [
    "developer", "engineer", "programmer", "software",
    "backend", "back-end", "frontend", "front-end",
    "fullstack", "full stack", "full-stack",
    "mobile", "web", "data",
    "ai", "artificial intelligence", "machine learning", "ml",
    ".net", "dotnet", "react", "ios", "android",
    "project manager", "product manager", "scrum", "manager",
    "ui", "ux", "designer", "design",
]

# DAFTAR HITAM: Jika judul mengandung kata ini
EXCLUDE_KEYWORDS = [
    "marketing", "sales", "hr", "human resource", "finance", "accounting", 
    "content", "social media", "copywriter", "graphic", "business development",
    "admin", "entry", "support", "customer", "secretary", "legal", "operation"
]

# LAPIS KEDUA (Strict Keywords): saringan presisi tinggi berbasis deskripsi.
KEYWORDS = [
    # Fullstack / Backend / Mobile
    "c#", ".net", "laravel", "php", "react", "node.js", "typescript",
    "dart", "javascript", "flutter",
    # AI / Data / Hardware
    "python", "machine learning", "edge", "computer vision", "inference",
    "smote", "arm", "bare-metal",
    # Architecture / DevOps / DB
    "postgresql", "supabase", "clean architecture", "ci/cd", "agile", "scrum",
    # UI/UX / Desain
    "figma", "ui/ux", "tailwind",
]

LOCATION = "Indonesia"

# ==========================================
# TARGET "BURU SAMPAI DAPAT N BARU"
# ==========================================
# Berapa lowongan BARU (belum ada di Notion) yang ingin diburu per keyword,
# per sekali jalan. Selama belum tercapai, bot akan terus membuka halaman
# berikutnya walau menemukan banyak data lama di halaman-halaman awal.
TARGET_NEW_JOBS_PER_TERM = 30

# Batas pengaman jumlah halaman per keyword (Kalibrr/Dealls/Talentics).
# Mencegah bot menembak API tanpa henti kalau ternyata sisa datanya
# memang semua sudah pernah tersimpan / sumber datanya terbatas.
MAX_PAGES_PER_TERM = 15

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

    for term in SEARCH_TERMS:
        term_new_count = 0
        page = 1

        while term_new_count < TARGET_NEW_JOBS_PER_TERM and page <= MAX_PAGES_PER_TERM:
            logger.info(
                f"🔍 Kalibrr | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{TARGET_NEW_JOBS_PER_TERM})"
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

                if not any(bad_kw in title.lower() for bad_kw in EXCLUDE_KEYWORDS):
                    if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                        if link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            term_new_count += 1
                            time.sleep(1)

                            if term_new_count >= TARGET_NEW_JOBS_PER_TERM:
                                logger.info(
                                    f"   🎯 Target {TARGET_NEW_JOBS_PER_TERM} lowongan baru "
                                    f"utk '{term}' tercapai di Kalibrr."
                                )
                                break

            page += 1

        if page > MAX_PAGES_PER_TERM:
            logger.info(
                f"   🛑 Batas aman {MAX_PAGES_PER_TERM} halaman utk '{term}' tercapai "
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

    for term in SEARCH_TERMS:
        term_new_count = 0
        page = 1

        while term_new_count < TARGET_NEW_JOBS_PER_TERM and page <= MAX_PAGES_PER_TERM:
            logger.info(
                f"🔍 Dealls | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{TARGET_NEW_JOBS_PER_TERM})"
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
                company = company_info.get("name") if isinstance(company_info, dict) else "Unknown"
                job_id = job.get("id") or job.get("slug", "")
                link = f"https://dealls.com/jobs/{job_id}"
                notion_title = f"[Dealls: {company}] - {title}"

                if not any(bad_kw in title.lower() for bad_kw in EXCLUDE_KEYWORDS):
                    if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                        if link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            term_new_count += 1
                            time.sleep(1)

                            if term_new_count >= TARGET_NEW_JOBS_PER_TERM:
                                logger.info(
                                    f"   🎯 Target {TARGET_NEW_JOBS_PER_TERM} lowongan baru "
                                    f"utk '{term}' tercapai di Dealls."
                                )
                                break

            page += 1

        if page > MAX_PAGES_PER_TERM:
            logger.info(
                f"   🛑 Batas aman {MAX_PAGES_PER_TERM} halaman utk '{term}' tercapai "
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

    for term in SEARCH_TERMS:
        term_new_count = 0
        page = 1

        while term_new_count < TARGET_NEW_JOBS_PER_TERM and page <= MAX_PAGES_PER_TERM:
            logger.info(
                f"🔍 Talentics | '{term}' | Halaman {page} "
                f"(baru terkumpul: {term_new_count}/{TARGET_NEW_JOBS_PER_TERM})"
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
                company_info = job.get("company")
                company = (
                    company_info.get("name", "Unknown")
                    if isinstance(company_info, dict)
                    else job.get("company_name", "Unknown")
                )
                slug = job.get("slug") or job.get("id", "")
                link = job.get("url") or f"https://jobs.talentics.id/jobs/{slug}"
                notion_title = f"[Talentics: {company}] - {title}"

                if not any(bad_kw in title.lower() for bad_kw in EXCLUDE_KEYWORDS):
                    if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                        if link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            term_new_count += 1
                            time.sleep(1)

                            if term_new_count >= TARGET_NEW_JOBS_PER_TERM:
                                logger.info(
                                    f"   🎯 Target {TARGET_NEW_JOBS_PER_TERM} lowongan baru "
                                    f"utk '{term}' tercapai di Talentics."
                                )
                                break

            page += 1
            time.sleep(2)  # anti-DDoS delay antar halaman

        if page > MAX_PAGES_PER_TERM:
            logger.info(
                f"   🛑 Batas aman {MAX_PAGES_PER_TERM} halaman utk '{term}' tercapai "
                f"(baru didapat: {term_new_count})."
            )

    return match_count


# ==========================================
# ENGINE 4: JOBSPY (LinkedIn & Indeed)
# ==========================================
def fetch_jobspy(existing_links: set) -> int:
    logger.info("[ENGINE 4] Memulai JobSpy (platform global)...")
    match_count = 0

    for term in SEARCH_TERMS:
        term_new_count = 0
        results_wanted = JOBSPY_RESULTS_STEP
        attempt = 1

        while term_new_count < TARGET_NEW_JOBS_PER_TERM and attempt <= JOBSPY_MAX_ATTEMPTS:
            logger.info(
                f"🔍 JobSpy | '{term}' | percobaan {attempt}/{JOBSPY_MAX_ATTEMPTS} "
                f"(results_wanted={results_wanted}, baru terkumpul: "
                f"{term_new_count}/{TARGET_NEW_JOBS_PER_TERM})"
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
                title_lower = str(row.get("title", "")).lower()
                desc_lower = str(row.get("description", "")).lower()
                link = str(row.get("job_url", ""))
                company = str(row.get("company", "Unknown"))
                original_title = str(row.get("title", "Tanpa Judul"))
                notion_title = f"[{company}] - {original_title}"

                if any(kw in title_lower or kw in desc_lower for kw in KEYWORDS):
                    if link and link not in existing_links:
                        send_to_notion(notion_title, link)
                        existing_links.add(link)
                        match_count += 1
                        term_new_count += 1
                        new_this_round += 1
                        time.sleep(1.5)

                        if term_new_count >= TARGET_NEW_JOBS_PER_TERM:
                            logger.info(
                                f"   🎯 Target {TARGET_NEW_JOBS_PER_TERM} lowongan baru "
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
