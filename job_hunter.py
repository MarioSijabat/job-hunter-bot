import requests
import time
import os
import pandas as pd
from datetime import datetime
from jobspy import scrape_jobs

# --- KONFIGURASI NOTION ---
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
DATABASE_ID = os.environ.get('DATABASE_ID')

# Kata kunci spesifik untuk filter (Lapis Kedua)
KEYWORDS = ['python', 'machine learning', 'edge', 'iot', 'computer vision', 'raspberry pi', 'backend']

# --- KONFIGURASI JOBSPY ---
# Kata kunci pencarian utama di platform (Lapis Pertama)
SEARCH_TERMS = ["machine learning", "python developer", "iot engineer"]
LOCATION = "Indonesia" # Bisa diganti "Remote" atau "Jakarta"

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
        
    print(f"Ditemukan {len(existing_links)} lowongan di database.")
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
            print(f"✅ BARU: Masuk Notion -> {title}")
        else:
            print(f"❌ Gagal: {response.text}")
    except Exception as e:
        print(f"Error Koneksi: {e}")

def fetch_and_filter_jobs():
    existing_links = get_existing_links()
    print("\nMemulai perburuan via JobSpy (LinkedIn, Indeed, Glassdoor)...")
    match_count = 0

    for term in SEARCH_TERMS:
        print(f"\n🔍 Mencari: '{term}' di {LOCATION}")
        try:
            # Menggunakan JobSpy untuk menyedot data
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=term,
                location=LOCATION,
                results_wanted=15, # Dibatasi 15 per kata kunci agar tidak di-banned GitHub
                hours_old=24,      # Hanya mencari lowongan yang di-posting 24 jam terakhir
                country_usa=False
            )

            if jobs is None or jobs.empty:
                print(f"Tidak ada hasil baru untuk '{term}'.")
                continue

            # Iterasi (membaca) setiap baris data yang didapat
            for index, row in jobs.iterrows():
                title_lower = str(row.get('title', '')).lower()
                desc_lower = str(row.get('description', '')).lower()
                link = str(row.get('job_url', ''))
                
                original_title = str(row.get('title', 'Tanpa Judul'))
                company = str(row.get('company', 'Perusahaan Tidak Diketahui'))
                
                # Format yang akan masuk ke Notion
                notion_title = f"[{company}] - {original_title}"

                # Cek kecocokan dengan skill spesifikmu
                if any(kw in title_lower or kw in desc_lower for kw in KEYWORDS):
                    if link and link not in existing_links:
                        send_to_notion(notion_title, link)
                        existing_links.add(link) # Ingat link ini agar tidak double
                        match_count += 1
                        time.sleep(1.5) # Jeda sopan
                    else:
                        print(f"⏩ Lewati (Sudah ada): {original_title}")

        except Exception as e:
            print(f"⚠️ Error saat scraping '{term}': {e}")

    print(f"\nSelesai! {match_count} peluang BARU dari platform raksasa berhasil dikumpulkan.")

if __name__ == "__main__":
    fetch_and_filter_jobs()