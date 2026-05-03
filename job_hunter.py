import feedparser
import requests
import time
import os
from datetime import datetime

# --- KONFIGURASI NOTION ---
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
DATABASE_ID = os.environ.get('DATABASE_ID')

KEYWORDS = ['python', 'machine learning', 'edge computing', 'iot', 'computer vision', 'raspberry pi', 'backend']

RSS_URLS = [
    'https://weworkremotely.com/categories/remote-programming-jobs.rss',
    'https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss',
    'https://remoteok.com/api?format=rss'
]

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_existing_links():
    """Membaca Notion untuk melihat link pekerjaan apa saja yang sudah ada"""
    print("Mengecek ingatan bot di Notion...")
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    existing_links = set()
    
    try:
        # Menarik data dari Notion (maksimal 100 data terbaru)
        response = requests.post(url, headers=HEADERS, json={})
        if response.status_code == 200:
            results = response.json().get('results', [])
            for page in results:
                # Mengambil URL dari kolom 'Link' di Notion
                properties = page.get('properties', {})
                link_prop = properties.get('Link', {})
                link_url = link_prop.get('url')
                
                if link_url:
                    existing_links.add(link_url)
    except Exception as e:
        print(f"Error membaca Notion: {e}")
        
    print(f"Ditemukan {len(existing_links)} lowongan di database.")
    return existing_links

def send_to_notion(title, link):
    """Mengirim data baru ke Notion"""
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
    # 1. Panggil ingatan sebelum memulai
    existing_links = get_existing_links()
    
    print("\nMemulai perburuan lowongan dari berbagai sumber...")
    match_count = 0
    
    for url in RSS_URLS:
        print(f"\nMengecek sumber: {url}")
        feed = feedparser.parse(url)
        
        for entry in feed.entries: 
            title = entry.title.lower()
            description = entry.get('summary', '').lower() 
            
            # 2. Cek kecocokan keyword
            if any(keyword in title or keyword in description for keyword in KEYWORDS):
                # 3. Filter Anti-Duplikat: Apakah link ini sudah ada di Notion?
                if entry.link not in existing_links:
                    send_to_notion(entry.title, entry.link)
                    match_count += 1
                    time.sleep(1)
                else:
                    print(f"⏩ Lewati (Sudah ada): {entry.title}")
                
    if match_count == 0:
        print("\nBelum ada lowongan spesifik baru hari ini.")
    else:
        print(f"\nSelesai! {match_count} peluang BARU berhasil dikumpulkan.")

if __name__ == "__main__":
    fetch_and_filter_jobs()