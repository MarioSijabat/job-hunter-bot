import feedparser
import requests
import time
import os
from datetime import datetime

import feedparser
import requests
import time
from datetime import datetime

# --- KONFIGURASI NOTION ---
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
DATABASE_ID = os.environ.get('DATABASE_ID')

KEYWORDS = ['python', 'machine learning', 'edge', 'iot', 'computer vision', 'backend']
RSS_URL = 'https://weworkremotely.com/categories/remote-programming-jobs.rss'

# Header standar yang diminta oleh API Notion
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def send_to_notion(title, link):
    """Fungsi untuk membuat baris baru di Database Notion"""
    url = "https://api.notion.com/v1/pages"
    
    # Format JSON sesuai struktur tabel Notion
    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {
                "title": [{"text": {"content": title}}]
            },
            "Link": {
                "url": link
            },
            "Date": {
                "date": {"start": datetime.now().isoformat()}
            }
        }
    }
    
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        if response.status_code == 200:
            print(f"✅ Sukses masuk Notion: {title}")
        else:
            print(f"❌ Gagal: {response.text}")
    except Exception as e:
        print(f"Error Koneksi: {e}")

def fetch_and_filter_jobs():
    print("Mencari lowongan baru dan mengirim ke Notion...")
    feed = feedparser.parse(RSS_URL)
    
    match_count = 0
    for entry in feed.entries[:15]: 
        title = entry.title.lower()
        
        if any(keyword in title for keyword in KEYWORDS):
            send_to_notion(entry.title, entry.link)
            match_count += 1
            time.sleep(1) # Jeda aman untuk API Notion
            
    if match_count == 0:
        print("Belum ada lowongan spesifik hari ini.")
    else:
        print(f"Selesai! {match_count} lowongan berhasil direkap ke Notion.")

if __name__ == "__main__":
    fetch_and_filter_jobs()