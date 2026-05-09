"""
Twilio Verify API Integration (Out-of-Band Authentication)
Handles sending and checking 6-digit SMS OTPs.
"""

import os
import asyncio
from loguru import logger
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# Initialize the Twilio REST Client globally
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID")

if not all([ACCOUNT_SID, AUTH_TOKEN, VERIFY_SERVICE_SID]):
    logger.warning("Twilio Verify credentials missing. Authentication will fail.")

twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID else None

async def send_verification_pin(phone_number: str) -> bool:
    """Triggers Twilio Verify to send a 6-digit SMS PIN."""
    logger.info("🔐 [Auth] Attempting to send SMS PIN...") 
    
    if not twilio_client or not VERIFY_SERVICE_SID:
        logger.error("[Auth] Twilio Verify SID missing.")
        return False
        
    def _send():
        return twilio_client.verify.v2.services(VERIFY_SERVICE_SID).verifications.create(
            to=phone_number,
            channel='sms'
        )

    try:
        # Run in a thread so the synchronous SDK doesn't block Pipecat's audio loop
        verification = await asyncio.to_thread(_send)
        logger.info(f"[Auth] SMS PIN sent successfully. Status: {verification.status}")
        return True
    except Exception as e:
        logger.error(f"[Auth] Failed to send PIN: {str(e)}")
        return False

async def check_verification_pin(phone_number: str, pin: str) -> bool:
    """Checks the provided PIN against the Twilio Verify API."""
    # Clean up the pin in case STT puts spaces or dashes in it (e.g., "1 2 3 4 5 6" or "123-456")
    clean_pin = "".join(filter(str.isdigit, str(pin)))
    logger.info("[Auth] Verifying PIN...")
    
    if not twilio_client or not VERIFY_SERVICE_SID:
        return False
        
    def _check():
        return twilio_client.verify.v2.services(VERIFY_SERVICE_SID).verification_checks.create(
            to=phone_number,
            code=clean_pin
        )

    try:
        verification_check = await asyncio.to_thread(_check)
        if verification_check.status == 'approved':
            logger.info("[Auth] PIN Approved! User is authenticated.")
            return True
        else:
            logger.warning(f"[Auth] PIN Denied. Status: {verification_check.status}")
            return False
            
    except Exception as e:
        logger.error(f"[Auth] Verification check failed: {str(e)}")
        return False