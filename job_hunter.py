import requests
import time
import os
import pandas as pd
from datetime import datetime
from jobspy import scrape_jobs

# --- KONFIGURASI NOTION ---
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
DATABASE_ID = os.environ.get('DATABASE_ID')

# ==========================================
# KONFIGURASI TARGET (Diperbarui untuk 4 Role Utama)
# ==========================================

# 1. LAPIS PERTAMA (Search Terms): Kata kunci yang diketikkan bot di kolom pencarian portal.
# Kita perluas jaringnya untuk mencakup Fullstack, AI, PM, dan UI/UX.
SEARCH_TERMS = [
    "fullstack developer", 
    ".net developer", 
    "ai engineer", 
    "machine learning", 
    "project manager", 
    "ui ux designer"
]

# 2. LAPIS KEDUA (Strict Keywords): Saringan presisi tinggi.
# Lowongan dari Lapis Pertama akan DIBUANG jika deskripsinya tidak mengandung minimal satu kata di bawah ini.
# Daftar ini adalah cerminan langsung dari tumpukan teknologi (tech stack) dan keahlian aslimu.
KEYWORDS = [
    # Fullstack / Backend / Mobile
    'c#', '.net', 'laravel', 'php', 'react', 'node.js', 'typescript', 'dart', 'javascript', 'flutter',
    
    # AI / Data / Hardware (Keahlian Spesifik)
    'python', 'machine learning', 'edge', 'computer vision', 'inference', 'smote', 'arm', 'bare-metal',
    
    # Architecture / DevOps / DB
    'postgresql', 'supabase', 'clean architecture', 'ci/cd', 'agile', 'scrum',
    
    # UI/UX / Desain
    'figma', 'ui/ux', 'tailwind'
]

LOCATION = "Indonesia"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_existing_links():
    print("Mengecek ingatan bot di Notion...")
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    existing_links = set()
    try:
        response = requests.post(url, headers=HEADERS, json={})
        if response.status_code == 200:
            results = response.json().get('results', [])
            for page in results:
                link_url = page.get('properties', {}).get('Link', {}).get('url')
                if link_url:
                    existing_links.add(link_url)
    except Exception as e:
        print(f"Error membaca Notion: {e}")
    print(f"Ditemukan {len(existing_links)} lowongan di database.\n")
    return existing_links

def send_to_notion(title, link):
    url = "https://api.notion.com/v1/pages"
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Link": {"url": link},
            "Date": {"date": {"start": datetime.now().isoformat()}}
        }
    }
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        if response.status_code == 200:
            print(f"✅ BARU: {title}")
        else:
            print(f"❌ Gagal: {response.text}")
    except Exception as e:
        print(f"Error Koneksi: {e}")

# ==========================================
# ENGINE 1: KALIBRR (Hidden API / No Blok)
# ==========================================
def fetch_kalibrr(existing_links):
    print("🚀 [ENGINE 1] Membaca API Kalibrr...")
    match_count = 0
    req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for term in SEARCH_TERMS:
        print(f"🔍 Mencari '{term}' di Kalibrr...")
        api_url = f"https://www.kalibrr.id/api/job_board/search?query={term}&location=Indonesia&limit=30"
        
        try:
            response = requests.get(api_url, headers=req_headers)
            if response.status_code == 200:
                jobs = response.json().get('jobs', [])
                for job in jobs:
                    title = job.get('name', 'Tanpa Judul')
                    company = job.get('company_info', {}).get('name', 'Perusahaan Tidak Diketahui')
                    job_id = job.get('id', '')
                    company_code = job.get('company_info', {}).get('code', '')
                    
                    link = f"https://www.kalibrr.id/c/{company_code}/jobs/{job_id}"
                    desc = job.get('description', '').lower()
                    notion_title = f"[Kalibrr: {company}] - {title}"
                    
                    if any(kw in title.lower() or kw in desc for kw in KEYWORDS):
                        if link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            time.sleep(1) 
        except Exception as e:
            print(f"⚠️ Error API Kalibrr untuk '{term}': {e}")
            
    return match_count

# ==========================================
# ENGINE 1: KALIBRR (Hidden API / No Blok)
# ==========================================
def fetch_kalibrr(existing_links):
    print("🚀 [ENGINE 1] Membaca API Kalibrr...")
    match_count = 0
    req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for term in SEARCH_TERMS:
        print(f"🔍 Mencari '{term}' di Kalibrr...")
        api_url = f"https://www.kalibrr.id/api/job_board/search?query={term}&location=Indonesia&limit=30"
        
        try:
            response = requests.get(api_url, headers=req_headers)
            if response.status_code == 200:
                jobs = response.json().get('jobs', [])
                for job in jobs:
                    title = job.get('name', 'Tanpa Judul')
                    company = job.get('company_info', {}).get('name', 'Perusahaan Tidak Diketahui')
                    job_id = job.get('id', '')
                    company_code = job.get('company_info', {}).get('code', '')
                    
                    link = f"https://www.kalibrr.id/c/{company_code}/jobs/{job_id}"
                    notion_title = f"[Kalibrr: {company}] - {title}"
                    
                    # FILTER DILONGGARKAN: Langsung masukkan jika belum ada di Notion
                    if link not in existing_links:
                        send_to_notion(notion_title, link)
                        existing_links.add(link)
                        match_count += 1
                        time.sleep(1) 
        except Exception as e:
            print(f"⚠️ Error API Kalibrr untuk '{term}': {e}")
            
    return match_count


# ==========================================
# ENGINE 2: DEALLS (Hidden API / No Blok)
# ==========================================
def fetch_dealls(existing_links):
    print("\n🚀 [ENGINE 2] Membaca API Dealls (SejutaCita)...")
    match_count = 0
    
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'Origin': 'https://dealls.com',
        'Referer': 'https://dealls.com/',
        'x-client-app-name': 'Deall-Talent-Web',
        'x-client-app-version': '2.49.46'
    }
    
    for term in SEARCH_TERMS:
        print(f"🔍 Mencari '{term}' di Dealls...")
        encoded_term = term.replace(" ", "%20")
        api_url = f"https://api.sejutacita.id/v1/explore-job/job?keyword={encoded_term}&page=1&sortParam=mostRelevant&sortBy=asc&boostTheBoostedJob=true&published=true&limit=20&status=active&externalPlatformApplyUrlSet=null"
        
        try:
            response = requests.get(api_url, headers=req_headers)
            if response.status_code == 200:
                res_json = response.json()
                jobs = []
                
                data_node = res_json.get('data')
                if isinstance(data_node, list): jobs = data_node
                elif isinstance(data_node, dict):
                    for key, value in data_node.items():
                        if isinstance(value, list):
                            jobs = value
                            break
                
                for job in jobs:
                    if not isinstance(job, dict): continue
                    title = job.get('role', 'Tanpa Judul')
                    company_info = job.get('company')
                    company = company_info.get('name') if isinstance(company_info, dict) else "Unknown"
                    job_id = job.get('id') or job.get('slug', '')
                    link = f"https://dealls.com/jobs/{job_id}"
                    notion_title = f"[Dealls: {company}] - {title}"
                    
                    # FILTER DILONGGARKAN: Langsung masukkan jika belum ada di Notion
                    if link not in existing_links:
                        send_to_notion(notion_title, link)
                        existing_links.add(link)
                        match_count += 1
                        time.sleep(1)
            else:
                print(f"⚠️ API Dealls error untuk '{term}': {response.status_code}")
        except Exception as e:
            print(f"⚠️ Error API Dealls untuk '{term}': {e}")
            
    return match_count

# ==========================================
# ENGINE 3: JOBS TALENTICS (Hidden API / GET)
# ==========================================
def fetch_talentics(existing_links):
    print("\n🚀 [ENGINE 3] Membaca API Jobs Talentics...")
    match_count = 0
    req_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for term in SEARCH_TERMS:
        print(f"🔍 Mencari '{term}' di Jobs Talentics...")
        api_url = f"https://jobs.talentics.id/ajaxs/jobs?search={term}&page=1"
        
        try:
            response = requests.get(api_url, headers=req_headers)
            if response.status_code == 200:
                jobs = response.json().get('data', [])
                
                for job in jobs:
                    title = job.get('title') or job.get('name', 'Tanpa Judul')
                    
                    company_info = job.get('company')
                    if isinstance(company_info, dict):
                        company = company_info.get('name', 'Unknown')
                    else:
                        company = job.get('company_name', 'Unknown')
                        
                    slug = job.get('slug') or job.get('id', '')
                    link = job.get('url') or f"https://jobs.talentics.id/jobs/{slug}"
                    desc = job.get('description', '').lower()
                    
                    notion_title = f"[Talentics: {company}] - {title}"
                    
                    if any(kw in title.lower() or kw in desc for kw in KEYWORDS):
                        print(f"   [CEK] Judul: {title} | Skills: {desc[:50]}")
                        if link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            time.sleep(1)
        except Exception as e:
            print(f"⚠️ Error API Talentics untuk '{term}': {e}")
            
    return match_count

# ==========================================
# ENGINE 4: JOBSPY (LinkedIn & Indeed)
# ==========================================
def fetch_jobspy(existing_links):
    print("\n🚀 [ENGINE 4] Memulai JobSpy (Platform Global)...")
    match_count = 0
    for term in SEARCH_TERMS:
        print(f"🔍 Mencari '{term}' via JobSpy...")
        try:
            # GLASSDOOR DIHAPUS DARI LIST UNTUK MENGHINDARI ERROR 403
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed"], 
                search_term=term,
                location=LOCATION,
                results_wanted=15, 
                hours_old=72,      
                country_usa=False
            )
            if jobs is not None and not jobs.empty:
                for _, row in jobs.iterrows():
                    title_lower = str(row.get('title', '')).lower()
                    desc_lower = str(row.get('description', '')).lower()
                    link = str(row.get('job_url', ''))
                    company = str(row.get('company', 'Unknown'))
                    original_title = str(row.get('title', 'Tanpa Judul'))
                    notion_title = f"[{company}] - {original_title}"

                    if any(kw in title_lower or kw in desc_lower for kw in KEYWORDS):
                        if link and link not in existing_links:
                            send_to_notion(notion_title, link)
                            existing_links.add(link)
                            match_count += 1
                            time.sleep(1.5)
        except Exception as e:
            print(f"⚠️ Error saat scraping JobSpy '{term}': {e}")
            
    return match_count

def main_execution():
    existing_links = get_existing_links()
    
    total = 0
    total += fetch_kalibrr(existing_links)
    total += fetch_dealls(existing_links)
    total += fetch_talentics(existing_links)
    total += fetch_jobspy(existing_links)
    
    print(f"\n🎉 EKSEKUSI SELESAI! Total {total} peluang BARU berhasil dimasukkan ke Notion.")

if __name__ == "__main__":
    main_execution()