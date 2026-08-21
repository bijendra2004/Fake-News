import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
sys.path.append(os.getcwd())

with open(".env", "r") as f:
    for line in f:
        if line.strip() and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k] = v.strip('"')

from backend.mailer import send_otp_email

try:
    send_otp_email("bijendrayadav@gmail.com", "123456")
    print("Success")
except Exception as e:
    import traceback
    traceback.print_exc()
