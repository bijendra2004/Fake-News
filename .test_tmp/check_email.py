import requests
import time

# Create a mail.tm account
domain = requests.get('https://api.mail.tm/domains').json()['hydra:member'][0]['domain']
address = f"sachlens_test123@{domain}"
password = "password123"

# Register
resp = requests.post('https://api.mail.tm/accounts', json={"address": address, "password": password})
if resp.status_code not in (200, 201):
    print("Failed to register:", resp.text)
    # Get token anyway in case it exists
token = requests.post('https://api.mail.tm/token', json={"address": address, "password": password}).json()['token']
print(f"EMAIL: {address}")
print(f"TOKEN: {token}")
