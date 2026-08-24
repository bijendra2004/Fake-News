import time
import requests

url = "https://fake-news-cvzg.onrender.com/api/debug-smtp"
print(f"Polling {url}...")

for _ in range(60):
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            print("DEPLOYMENT LIVE!")
            print(resp.json())
            break
        elif resp.status_code != 404:
            print(f"Unexpected status: {resp.status_code}")
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(10)
else:
    print("Timeout waiting for deployment")
