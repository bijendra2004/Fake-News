import requests

token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpYXQiOjE3ODUyNjgyMjEsInJvbGVzIjpbIlJPTEVfVVNFUiJdLCJhZGRyZXNzIjoic2FjaGxlbnNfdGVzdDEyM0B3ZWItbGlicmFyeS5uZXQiLCJpZCI6IjZhNjkwN2ZjM2FhNmM2OTRkNjBmY2EwMCIsIm1lcmN1cmUiOnsic3Vic2NyaWJlIjpbIi9hY2NvdW50cy82YTY5MDdmYzNhYTZjNjk0ZDYwZmNhMDAiXX19.Zcba_paGvm-y1J8p7MsEM2kv90OOTP3lv7zupeJgjrBu8Peu9V6_Un2jcC0w-vqHC1LUoGEF2gPVcWitXvqtsQ"

resp = requests.get(
    'https://api.mail.tm/messages',
    headers={"Authorization": f"Bearer {token}"}
)

messages = resp.json().get('hydra:member', [])
if not messages:
    print("No messages found.")
else:
    for m in messages:
        print(f"From: {m['from']}")
        print(f"Subject: {m['subject']}")
        
        msg_id = m['id']
        msg_resp = requests.get(
            f'https://api.mail.tm/messages/{msg_id}',
            headers={"Authorization": f"Bearer {token}"}
        )
        print("Body:", msg_resp.json().get('text', ''))
