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
# ENGINE 1: KALIBRR (Versi Pagination Loop)
# ==========================================
def fetch_kalibrr(existing_links):
    print("\n🚀 [ENGINE 1] Membaca API Kalibrr...")
    match_count = 0
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': 'application/json'
    }
    
    for term in SEARCH_TERMS:
        # Loop untuk memeriksa halaman 1 sampai 3
        for page in range(1, 4):
            print(f"🔍 Mencari '{term}' di Kalibrr (Halaman {page})...")
            encoded_term = term.replace(" ", "%20")
            
            # Menggunakan parameter page secara dinamis
            api_url = f"https://www.kalibrr.id/api/job_board/search?query={encoded_term}&limit=30&page={page}"
            
            try:
                response = requests.get(api_url, headers=req_headers)
                if response.status_code == 200:
                    res_json = response.json()
                    jobs = res_json.get('jobs', [])
                    
                    if not jobs:
                        break # Halaman kosong, hentikan menyelam lebih dalam
                        
                    page_new_elements = 0
                    for job in jobs:
                        if not isinstance(job, dict): continue
                        title = job.get('name', 'Tanpa Judul')
                        
                        company_info = job.get('company_info', {})
                        company = "Perusahaan Tidak Diketahui"
                        company_code = ""
                        if isinstance(company_info, dict):
                            company = company_info.get('name', 'Perusahaan Tidak Diketahui')
                            company_code = company_info.get('code', '')
                            
                        job_id = job.get('id', '')
                        link = f"https://www.kalibrr.id/c/{company_code}/jobs/{job_id}"
                        notion_title = f"[Kalibrr: {company}] - {title}"
                        
                        # Saringan Lunak: Ambil yang judulnya relevan dengan dunia IT/Management
                        if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                            if link not in existing_links:
                                send_to_notion(notion_title, link)
                                existing_links.add(link)
                                match_count += 1
                                page_new_elements += 1
                                time.sleep(1) 
                                
                    # Jika satu halaman penuh sudah pernah ditarik sebelumnya, hentikan loop page
                    if page_new_elements == 0 and len(jobs) > 0:
                        break
                else:
                    print(f"⚠️ API Kalibrr error: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Error API Kalibrr untuk '{term}' Halaman {page}: {e}")
                break
                
    return match_count

# ==========================================
# ENGINE 2: DEALLS (Versi Pagination Loop)
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
        # Kita buat loop untuk memeriksa halaman 1 sampai 3
        for page in range(1, 4): 
            print(f"🔍 Mencari '{term}' di Dealls (Halaman {page})...")
            encoded_term = term.replace(" ", "%20")
            
            # Perhatikan parameter page sekarang dinamis: page={page}
            api_url = f"https://api.sejutacita.id/v1/explore-job/job?keyword={encoded_term}&page={page}&sortParam=mostRelevant&sortBy=asc&boostTheBoostedJob=true&published=true&limit=20&status=active&externalPlatformApplyUrlSet=null"
            
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
                    
                    # Jika halaman tersebut ternyata kosong, tidak perlu lanjut ke halaman berikutnya
                    if not jobs:
                        break
                        
                    page_new_elements = 0
                    for job in jobs:
                        if not isinstance(job, dict): continue
                        title = job.get('role', 'Tanpa Judul')
                        company_info = job.get('company')
                        company = company_info.get('name') if isinstance(company_info, dict) else "Unknown"
                        job_id = job.get('id') or job.get('slug', '')
                        link = f"https://dealls.com/jobs/{job_id}"
                        notion_title = f"[Dealls: {company}] - {title}"
                        
                        # Gabungkan dengan Soft Filter Judul dari diskusi sebelumnya
                        if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                            if link not in existing_links:
                                send_to_notion(notion_title, link)
                                existing_links.add(link)
                                match_count += 1
                                page_new_elements += 1
                                time.sleep(1)
                                
                    # OPTIMASI KUOTA: Jika di satu halaman penuh TIDAK ADA satu pun lowongan baru 
                    # (semuanya sudah ada di Notion), artinya kita sudah mencapai batas data lama.
                    # Berhentilah menyelam lebih dalam untuk menghemat waktu & kuota.
                    if page_new_elements == 0 and len(jobs) > 0:
                        print("   💡 Semua data di halaman ini sudah ada di Notion. Menghentikan pencarian lebih dalam.")
                        break
                        
                else:
                    print(f"⚠️ API Dealls error untuk '{term}' Halaman {page}: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Error API Dealls untuk '{term}' Halaman {page}: {e}")
                break # Keluar dari loop page jika terjadi error koneksi
                
    return match_count

# ==========================================
# ENGINE 3: JOBS TALENTICS (Versi Pagination Loop)
# ==========================================
def fetch_talentics(existing_links):
    print("\n🚀 [ENGINE 3] Membaca API Jobs Talentics...")
    match_count = 0
    
    req_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://jobs.talentics.id/jobs?search=python',
        'x-requested-with': 'XMLHttpRequest',
        'x-csrf-token': 'Odhevyozb6UPmct9CXewnFDEsAXbbIpOevjNHyAA',
        'Cookie': '_gcl_au=1.1.1430381327.1779116016; _hjSessionUser_2874056=eyJpZCI6IjhhMzcyYjlmLWQyYTgtNTA1Ni05YmRmLWE1ZTZhMmRmNzU0OSIsImNyZWF0ZWQiOjE3NzkxMTYwMTY2NTYsImV4aXN0aW5nIjp0cnVlfQ==; _hjSession_2874056=eyJpZCI6IjRhNjM4ZDdmLTU5NDctNDIwMS1iNTFiLWM1NmZlNjg4ZGJlOCIsImMiOjE3NzkxMTYwMTY2NTcsInMiOjEsInIiOjEsInNiIjowLCJzciI6MCwic2UiOjAsImZzIjoxLCJzcCI6MX0=; g_state={"i_l":0,"i_ll":1779116019210,"i_b":"5NAC+v5ApKoIOAb96BtNorvk6ym0KAJLWRUcQR2JA64","i_e":{"enable_itp_optimization":23},"i_et":1779116016543}; XSRF-TOKEN=eyJpdiI6Ildzb21TY0lPcGN3eWhjaU5jTWxzd1E9PSIsInZhbHVlIjoiSXA0eFErT21wYWJMV09QTmI0SVlMTklycFVFN0ovMjFZT1U2aFdyWWFhNUFnNkV3UXFQNENBZXlrRGV6UnFEUmFHakcyRUVmeWwwc3BBYk4vYnY2MFVJNDRWenN1cFlUYmo4UG5odWMvZ1dsSnlvaWVTZVFJZnFjcHhvSmdEaFAiLCJtYWMiOiJhODUzMGIyNmQ0ZDViOGM4MjY0MTc0Zjk2ODBhZGI5M2JlOGJlOTEyNDQwMjk4ODM3OWNhMjg0MDNmNjM0NGZjIiwidGFnIjoiIn0%3D; talentics_session=eyJpdiI6IkVwVkdZaGJnWE1Bd0dJNTV1Z0VibHc9PSIsInZhbHVlIjoiTGxXdEZGL3pPMTZwVUZDczZOaVFsaXMwby9FVldhbFZJYnlVMmttUDFnMzI0bjF4MkRHcUM4UXFoS01GWmhhMWdZZi9QVTVuVk5qTEQybnZEM3RaNzc2Z0pzOEkzMWF6R2s1Z3lNcVRkTE5tQldiV0hXSGNwM3N3VDNUbnlWQ0wiLCJtYWMiOiIxZjMyNmRjOWNjNzFjMjA5MWU1NGRiYTBlYWExNTlkNzc3OWJiZTk5NGRkNzU0ZjNkZTYzMDFhZWU4MDM3Y2I4IiwidGFnIjoiIn0%3D; _ce.s=lcw~1779116019730'
    }
    
    for term in SEARCH_TERMS:
        # Loop untuk memeriksa halaman 1 sampai 3
        for page in range(1, 4):
            print(f"🔍 Mencari '{term}' di Jobs Talentics (Halaman {page})...")
            encoded_term = term.replace(" ", "%20")
            
            # Parameter page dinamis
            api_url = f"https://jobs.talentics.id/ajaxs/jobs?search={encoded_term}&page={page}"
            
            try:
                response = requests.get(api_url, headers=req_headers)
                if response.status_code == 200:
                    res_json = response.json()
                    jobs = res_json.get('data', []) if isinstance(res_json, dict) else []
                    
                    if not jobs:
                        break
                        
                    page_new_elements = 0
                    for job in jobs:
                        if not isinstance(job, dict): continue
                        title = job.get('title') or job.get('name', 'Tanpa Judul')
                        
                        company_info = job.get('company')
                        company = company_info.get('name', 'Unknown') if isinstance(company_info, dict) else job.get('company_name', 'Unknown')
                            
                        slug = job.get('slug') or job.get('id', '')
                        link = job.get('url') or f"https://jobs.talentics.id/jobs/{slug}"
                        notion_title = f"[Talentics: {company}] - {title}"
                        
                        # Saringan Lunak: Ambil yang judulnya relevan dengan dunia IT/Management
                        if any(skw in title.lower() for skw in SOFT_KEYWORDS):
                            if link not in existing_links:
                                send_to_notion(notion_title, link)
                                existing_links.add(link)
                                match_count += 1
                                page_new_elements += 1
                                time.sleep(1)
                                
                    if page_new_elements == 0 and len(jobs) > 0:
                        break
                else:
                    print(f"⚠️ API Talentics menolak akses (Status: {response.status_code})")
            except Exception as e:
                print(f"⚠️ Error API Talentics untuk '{term}' Halaman {page}: {e}")
                break
                
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