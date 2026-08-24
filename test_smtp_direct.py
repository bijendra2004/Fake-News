import smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg.set_content("Test email")
msg["Subject"] = "Test"
msg["From"] = "sachlensuserauth@gmail.com"
msg["To"] = "sachlensuserauth@gmail.com"

try:
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login("sachlensuserauth@gmail.com", "fgdpoylgqrxnmjvm")
    server.send_message(msg)
    server.quit()
    print("SMTP LOGIN SUCCESSFUL")
except Exception as e:
    print(f"SMTP FAILED: {e}")
