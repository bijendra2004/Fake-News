import imaplib
import email

try:
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login('sachlensuserauth@gmail.com', 'fgdpoylgqrxnmjvm')
    mail.select('"[Gmail]/Sent Mail"')
    status, messages = mail.search(None, 'ALL')
    if status == 'OK':
        msg_ids = messages[0].split()
        print(f"Total sent emails: {len(msg_ids)}")
        if msg_ids:
            status, msg_data = mail.fetch(msg_ids[-1], '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    print("LATEST SENT EMAIL:")
                    print("To:", msg['To'])
                    print("Subject:", msg['Subject'])
                    print("Date:", msg['Date'])
    mail.logout()
except Exception as e:
    print(f"IMAP Error: {e}")
