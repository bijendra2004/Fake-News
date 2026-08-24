import os
import smtplib
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

@router.get("/api/debug-smtp")
def debug_smtp():
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    user = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    app_env = os.getenv("APP_ENV")
    
    if not host:
        return {"error": "SMTP_HOST is empty", "app_env": app_env}
        
    try:
        server = smtplib.SMTP(host, int(port))
        server.starttls()
        server.login(user, password)
        server.quit()
        return {"success": True, "message": "SMTP connection successful", "host": host, "user": user}
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
