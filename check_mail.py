import urllib.request
import json

req = urllib.request.Request(
    'https://www.mailinator.com/v2/domains/public/inboxes/sachlenstest?limit=2',
    headers={'User-Agent': 'Mozilla/5.0'}
)
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print(json.dumps(data, indent=2))
except Exception as e:
    print(e)
