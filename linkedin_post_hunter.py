"""
LinkedIn Post Hunter Bot
========================
Scraper yang mencari lowongan pekerjaan organik langsung dari LinkedIn Posts
(bukan menu LinkedIn Jobs) menggunakan Google Custom Search Engine (CSE) API.

Strategi: Query `site:linkedin.com/posts` via Google CSE untuk menemukan
postingan rekruter/engineer yang memuat info hiring. Hasil disimpan ke
Notion Database terpisah dari Job Hunter Bot utama.

Didesain untuk dijalankan berkala (2x/hari) via GitHub Actions.
"""

import os
import re
import time
import random
import logging
from datetime import datetime
from typing import Optional

import requests
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
logger = logging.getLogger("linkedin_post_hunter")

# ==========================================
# KONFIGURASI NOTION
# ==========================================
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_LINKEDIN_DB_ID = os.environ.get("NOTION_LINKEDIN_DB_ID")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ==========================================
# KONFIGURASI GOOGLE CUSTOM SEARCH ENGINE
# ==========================================
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID")
GOOGLE_CSE_BASE_URL = "https://www.googleapis.com/customsearch/v1"

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
# KONFIGURASI SEARCH QUERIES
# ==========================================
# Setiap entry menghasilkan satu request ke Google CSE API.
# Format: query string yang akan digabung dengan `site:linkedin.com/posts`
# secara otomatis oleh fungsi pencarian.
#
# Kuota gratis Google CSE: 100 query/hari.
# Dengan ~10 terms × 2 run/hari = ~20 query/run = 40 query/hari (aman).
SEARCH_QUERIES = [
    # --- Software Engineering (umum) ---
    '"hiring" "software engineer" "Indonesia"',
    '"hiring" "software developer" "remote"',

    # --- Web: Backend / Frontend / Fullstack ---
    '"hiring" "backend developer" "Indonesia"',
    '"hiring" "frontend developer" "Indonesia"',
    '"hiring" "fullstack" "remote" "Indonesia"',

    # --- Data / AI ---
    '"hiring" "data engineer" "Indonesia"',

    # --- QA ---
    '"hiring" "quality assurance" "Indonesia"',

    # --- UI/UX ---
    '"hiring" "UI UX" "designer" "Indonesia"',

    # --- Manajemen ---
    '"hiring" "project manager" OR "product manager" "Indonesia"',

    # --- Variasi posting umum ---
    '"we\'re hiring" "developer" "Indonesia"',
    '"open position" "software" "Indonesia"',
]

# ==========================================
# KEYWORD FILTER (RELEVANSI)
# ==========================================
# Soft keywords: jika ditemukan di snippet/title, post dianggap relevan.
# Dicocokkan berbasis word-boundary agar tidak salah tangkap.
SOFT_KEYWORDS = [
    # Software umum
    "developer", "engineer", "programmer", "software",
    # Backend / Frontend / Fullstack
    "backend", "back-end", "frontend", "front-end",
    "fullstack", "full stack", "full-stack",
    # Mobile
    "mobile", "android", "ios",
    # Stack spesifik
    ".net", "dotnet", "react", "vue", "angular", "node", "laravel", "php",
    "java", "kotlin", "swift", "golang", "flutter", "python", "typescript",
    # Data / AI
    "data", "ai", "machine learning", "ml",
    "data engineer", "data scientist", "data analyst",
    # QA / Testing
    "quality assurance", "qa", "tester", "testing", "automation", "sdet",
    # Analyst
    "analyst", "system analyst", "business analyst",
    # Manajemen
    "project manager", "product manager", "scrum",
    # UI/UX
    "ui", "ux", "designer", "ui/ux", "ui ux",
    # DevOps / Cloud
    "devops", "cloud", "sre",
    # Hiring signals
    "hiring", "open position", "we're hiring", "join us", "looking for",
    "vacancy", "lowongan", "dicari", "dibutuhkan",
]

# Daftar hitam: jika ditemukan, post dibuang meskipun lolos SOFT_KEYWORDS.
EXCLUDE_KEYWORDS = [
    "marketing", "sales", "hr", "human resource", "finance", "accounting",
    "content creator", "social media", "copywriter", "graphic designer",
    "business development", "admin", "customer service", "secretary",
    "legal", "operation", "warehouse", "driver", "security",
]

# ==========================================
# PENCOCOKAN KEYWORD BERBASIS WORD-BOUNDARY
# ==========================================
_boundary_pattern_cache: dict = {}


def _compile_boundary_pattern(keyword: str) -> re.Pattern:
    """Membangun regex untuk satu keyword dengan word-boundary di sisi yang
    diawali/diakhiri karakter alfanumerik. Untuk keyword yang diawali/diakhiri
    karakter non-alfanumerik (mis. ".net", "c#"), boundary di sisi itu
    dilepas karena \\b tidak berfungsi baik pada karakter non-word.
    """
    kw = keyword.lower().strip()
    escaped = re.escape(kw)
    left = r"\b" if kw[0].isalnum() else ""
    right = r"\b" if kw[-1].isalnum() else ""
    return re.compile(left + escaped + right)


def keyword_hit(text: str, keywords: list) -> bool:
    """True jika salah satu keyword ditemukan sebagai KATA UTUH (bukan
    sekadar substring) di dalam text. Mencegah false-positive pada
    keyword pendek seperti 'ai', 'qa', dll."""
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
    retry=retry_if_exception_type(
        (requests.exceptions.RequestException, RetryableHTTPError)
    ),
    before_sleep=_log_retry,
    reraise=True,
)


@retry(**RETRY_CONFIG)
def safe_get(url: str, headers: Optional[dict] = None, timeout: int = 15,
             **kwargs) -> requests.Response:
    """GET request dengan UA rotation otomatis + retry/backoff.
    `reraise=True` -> setelah semua percobaan gagal, exception asli
    dilempar ke pemanggil supaya bisa ditangani (graceful degradation).
    """
    merged_headers = dict(headers or {})
    merged_headers.setdefault("User-Agent", random_user_agent())
    response = requests.get(url, headers=merged_headers, timeout=timeout, **kwargs)
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableHTTPError(response.status_code, url)
    return response


@retry(**RETRY_CONFIG)
def safe_post(url: str, headers: Optional[dict] = None, json: Optional[dict] = None,
              timeout: int = 15) -> requests.Response:
    """POST request dengan retry/backoff untuk operasi Notion API."""
    merged_headers = dict(headers or {})
    merged_headers.setdefault("User-Agent", random_user_agent())
    response = requests.post(url, headers=merged_headers, json=json, timeout=timeout)
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableHTTPError(response.status_code, url)
    return response


# ==========================================
# NOTION: BACA (DEDUPLICATION)
# ==========================================
def get_existing_urls() -> set:
    """Mengambil SELURUH URL yang sudah ada di database Notion LinkedIn
    menggunakan paginasi penuh (start_cursor/has_more), bukan cuma
    100 baris pertama. Digunakan untuk mencegah duplikasi."""
    logger.info("Mengecek ingatan bot di Notion (paginasi penuh)...")
    url = f"https://api.notion.com/v1/databases/{NOTION_LINKEDIN_DB_ID}/query"
    existing_urls = set()
    has_more = True
    start_cursor = None
    page_count = 0

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            response = safe_post(url, headers=NOTION_HEADERS, json=payload)
        except Exception as e:
            logger.error(f"❌ Gagal membaca Notion setelah retry habis: {e}")
            break

        if response.status_code != 200:
            logger.error(
                f"❌ Notion API error {response.status_code}: "
                f"{response.text[:300]}"
            )
            break

        data = response.json()
        results = data.get("results", [])
        for page in results:
            post_url = page.get("properties", {}).get("URL", {}).get("url")
            if post_url:
                existing_urls.add(post_url)

        page_count += 1
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
        logger.info(
            f"   ...halaman Notion ke-{page_count} terbaca ({len(results)} entri)"
        )

    logger.info(
        f"Ditemukan total {len(existing_urls)} post tersimpan di database.\n"
    )
    return existing_urls


# ==========================================
# NOTION: TULIS
# ==========================================
def send_to_notion(
    post_title: str,
    post_url: str,
    author: str,
    content: str,
    posted_date: str,
    search_query: str,
) -> bool:
    """Mengirim satu post LinkedIn ke Notion Database.
    Returns True jika berhasil, False jika gagal."""
    url = "https://api.notion.com/v1/pages"

    # Notion rich_text dibatasi 2000 karakter per blok
    content_truncated = content[:2000] if content else ""

    data = {
        "parent": {"database_id": NOTION_LINKEDIN_DB_ID},
        "properties": {
            "Post Title": {
                "title": [{"text": {"content": post_title[:255]}}]
            },
            "URL": {"url": post_url},
            "Author": {
                "rich_text": [{"text": {"content": author[:255]}}]
            },
            "Content": {
                "rich_text": [{"text": {"content": content_truncated}}]
            },
            "Posted Date": {
                "rich_text": [{"text": {"content": posted_date[:100]}}]
            },
            "Date Added": {
                "date": {"start": datetime.now().isoformat()}
            },
            "Search Query": {
                "rich_text": [{"text": {"content": search_query[:255]}}]
            },
        },
    }

    try:
        response = safe_post(url, headers=NOTION_HEADERS, json=data)
        if response.status_code == 200:
            logger.info(f"✅ BARU: {post_title}")
            return True
        else:
            logger.error(
                f"❌ Gagal kirim ke Notion ({response.status_code}): "
                f"{response.text[:200]}"
            )
            return False
    except Exception as e:
        logger.error(f"❌ Error koneksi Notion setelah retry habis: {e}")
        return False


# ==========================================
# GOOGLE CSE: SEARCH LINKEDIN POSTS
# ==========================================
def search_linkedin_posts(query: str) -> list:
    """Mencari LinkedIn Posts via Google Custom Search Engine API.

    Args:
        query: String pencarian (akan digabung dengan site:linkedin.com/posts).

    Returns:
        List of dict, masing-masing berisi:
        - title: judul hasil pencarian Google
        - link: URL post LinkedIn
        - snippet: cuplikan konten dari Google
    """
    # Google CSE `dateRestrict=d1` membatasi hasil ke 24 jam terakhir
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": f"site:linkedin.com/posts {query}",
        "dateRestrict": "d1",
        "num": 10,  # Maksimum per request Google CSE
    }

    logger.info(f"🔍 Google CSE | Query: {query}")

    try:
        response = safe_get(GOOGLE_CSE_BASE_URL, params=params)
    except Exception as e:
        logger.warning(f"⚠️ Google CSE gagal setelah retry habis: {e}")
        return []

    if response.status_code != 200:
        logger.warning(
            f"⚠️ Google CSE status {response.status_code}: "
            f"{response.text[:300]}"
        )
        return []

    try:
        data = response.json()
    except ValueError:
        logger.warning("⚠️ Google CSE: response bukan JSON valid")
        return []

    items = data.get("items", [])
    if not items:
        logger.info(f"   💡 Tidak ada hasil untuk query: {query}")
        return []

    logger.info(f"   📄 Ditemukan {len(items)} hasil dari Google CSE")
    return items


# ==========================================
# PARSER: EKSTRAKSI DATA DARI HASIL CSE
# ==========================================
def parse_post_from_cse_item(item: dict) -> Optional[dict]:
    """Mengekstrak data terstruktur dari satu item hasil Google CSE.

    Google CSE mengembalikan format:
    - title: "Nama Author di LinkedIn: isi awal post..."
    - link: "https://www.linkedin.com/posts/username_activity-xxxxx"
    - snippet: cuplikan isi post yang lebih panjang

    Returns:
        Dict berisi {title, link, author, content, posted_date} atau
        None jika item tidak valid / bukan post LinkedIn.
    """
    link = item.get("link", "")
    title = item.get("title", "")
    snippet = item.get("snippet", "")

    # Validasi: harus URL LinkedIn post yang valid
    if not link or "/posts/" not in link:
        return None

    # Ekstrak nama author dari title Google
    # Format umum: "Nama Author di LinkedIn: isi post..."
    # atau: "Nama Author on LinkedIn: post content..."
    # atau: "Nama Author posted on LinkedIn"
    author = "Unknown"
    author_match = re.match(
        r"^(.+?)\s+(?:di|on|posted on)\s+LinkedIn", title, re.IGNORECASE
    )
    if author_match:
        author = author_match.group(1).strip()
    else:
        # Fallback: ambil bagian sebelum tanda ` - ` atau ` | `
        fallback_match = re.match(r"^(.+?)\s*[-|]", title)
        if fallback_match:
            author = fallback_match.group(1).strip()

    # Bersihkan snippet dari artefak Google (tanggal, "...", dll.)
    content = snippet.strip()

    # Coba ekstrak tanggal dari snippet (format umum Google: "3 days ago",
    # "Oct 15, 2026", dll.) — biasanya di awal snippet
    posted_date = ""
    date_match = re.search(
        r"(\d{1,2}\s+\w+\s+ago|\w{3}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
        snippet,
    )
    if date_match:
        posted_date = date_match.group(1)

    # Buat judul ringkas untuk Notion
    post_title = title[:200] if title else f"Post by {author}"

    return {
        "title": post_title,
        "link": link,
        "author": author,
        "content": content,
        "posted_date": posted_date,
    }


def is_relevant_post(post: dict) -> bool:
    """Memeriksa apakah post relevan berdasarkan keyword filtering.
    Menggabungkan title dan content untuk pencocokan yang lebih akurat.

    Returns:
        True jika post lolos filter (relevan), False jika tidak.
    """
    combined_text = f"{post.get('title', '')} {post.get('content', '')}"

    # Cek blacklist dulu — jika match, langsung buang
    if keyword_hit(combined_text, EXCLUDE_KEYWORDS):
        return False

    # Cek whitelist — setidaknya satu keyword relevan harus match
    if not keyword_hit(combined_text, SOFT_KEYWORDS):
        return False

    return True


# ==========================================
# ENGINE UTAMA: FETCH LINKEDIN POSTS
# ==========================================
def fetch_linkedin_posts(existing_urls: set) -> int:
    """Engine utama yang mengorkestrasi pencarian LinkedIn Posts.

    Flow:
    1. Iterasi semua SEARCH_QUERIES
    2. Untuk setiap query, panggil Google CSE API
    3. Parse hasil, filter relevansi, cek duplikasi
    4. Kirim post baru ke Notion

    Args:
        existing_urls: Set URL yang sudah ada di Notion (untuk dedup).

    Returns:
        Jumlah post baru yang berhasil ditambahkan ke Notion.
    """
    logger.info("[ENGINE] Memulai pencarian LinkedIn Posts via Google CSE...")
    match_count = 0
    skipped_duplicate = 0
    skipped_irrelevant = 0
    skipped_parse_fail = 0

    for query in SEARCH_QUERIES:
        items = search_linkedin_posts(query)

        for item in items:
            # Parse data dari hasil CSE
            post = parse_post_from_cse_item(item)
            if post is None:
                skipped_parse_fail += 1
                continue

            # Cek duplikasi
            if post["link"] in existing_urls:
                skipped_duplicate += 1
                continue

            # Filter relevansi
            if not is_relevant_post(post):
                skipped_irrelevant += 1
                continue

            # Kirim ke Notion
            success = send_to_notion(
                post_title=post["title"],
                post_url=post["link"],
                author=post["author"],
                content=post["content"],
                posted_date=post["posted_date"],
                search_query=query,
            )

            if success:
                existing_urls.add(post["link"])
                match_count += 1

            # Anti-flooding delay
            time.sleep(1.5)

        # Delay antar query untuk menghormati rate limit Google CSE
        time.sleep(2)

    logger.info(
        f"\n📊 Ringkasan Engine:"
        f"\n   ✅ Post baru ditambahkan : {match_count}"
        f"\n   🔁 Duplikat dilewati    : {skipped_duplicate}"
        f"\n   🚫 Tidak relevan        : {skipped_irrelevant}"
        f"\n   ⚠️  Gagal parse          : {skipped_parse_fail}"
    )
    return match_count


# ==========================================
# ORKESTRASI UTAMA (GRACEFUL DEGRADATION)
# ==========================================
def run_engine(name: str, func, existing_urls: set) -> int:
    """Menjalankan satu engine dengan isolasi penuh - jika engine ini
    raise exception apa pun (bukan cuma yang sudah ditangani di dalamnya),
    bot mencatat error lengkap lalu tetap selesai dengan aman."""
    logger.info(f"===== MEMULAI ENGINE: {name} =====")
    try:
        count = func(existing_urls)
        logger.info(
            f"===== ENGINE {name} SELESAI: {count} post baru =====\n"
        )
        return count
    except Exception as e:
        logger.error(
            f"‼️ ENGINE {name} GAGAL TOTAL - Detail: {e}",
            exc_info=True,
        )
        return 0


def validate_config() -> bool:
    """Memvalidasi bahwa semua environment variables yang dibutuhkan
    sudah tersedia. Mengembalikan False jika ada yang hilang."""
    required_vars = {
        "NOTION_TOKEN": NOTION_TOKEN,
        "NOTION_LINKEDIN_DB_ID": NOTION_LINKEDIN_DB_ID,
        "GOOGLE_CSE_API_KEY": GOOGLE_CSE_API_KEY,
        "GOOGLE_CSE_ID": GOOGLE_CSE_ID,
    }

    missing = [name for name, val in required_vars.items() if not val]
    if missing:
        logger.error(
            f"❌ Environment variable berikut belum di-set: {', '.join(missing)}"
        )
        return False
    return True


def main_execution():
    """Entry point utama bot LinkedIn Post Hunter."""
    logger.info("========== LINKEDIN POST HUNTER: MULAI EKSEKUSI ==========")

    # Validasi konfigurasi
    if not validate_config():
        logger.error("❌ Eksekusi dibatalkan karena konfigurasi tidak lengkap.")
        return

    # Baca URL yang sudah tersimpan (deduplication)
    existing_urls = get_existing_urls()

    # Jalankan engine dengan graceful degradation
    total = run_engine("LinkedIn Posts (Google CSE)", fetch_linkedin_posts, existing_urls)

    logger.info(
        f"🎉 EKSEKUSI SELESAI! Total {total} post BARU berhasil "
        f"dimasukkan ke Notion."
    )
    logger.info("========== LINKEDIN POST HUNTER: SELESAI ==========")


if __name__ == "__main__":
    main_execution()
