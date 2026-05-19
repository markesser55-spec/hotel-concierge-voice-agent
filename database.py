"""
Database layer (Supabase)
==========================
Creates a single Supabase client from environment variables and exposes async
helpers that run blocking SDK calls in a thread pool so they do not block the
asyncio event loop.
"""

import os
import asyncio
import random
from datetime import datetime, timedelta
from loguru import logger
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# ENTERPRISE DATABASE LAYER
# Best Practice: Isolate all third-party database connections here.
# The AI logic never writes SQL directly.
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("[System Error]: SUPABASE_URL and SUPABASE_KEY must be set")
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# RELATIONAL DATABASE (RESERVATIONS)
# ==========================================

async def get_guest_with_reservations(phone_number: str) -> dict:
    """Fetches the guest profile AND their active reservations. Auto-provisions if missing!"""
    def _query():
        # 1. Attempt standard lookup
        response = supabase.table("guests").select("*, reservations(*)").eq("phone_number", phone_number).execute()
        if response.data:
            return response.data[0]
            
        # 2. 🚀 JIT PROVISIONING LOGIC (Auto-Seeding Demo Users)
        logger.warning(f"⚠️ Guest {phone_number} not found. Executing JIT Auto-Provisioning...")
        
        try:
            # A. Create the Demo Guest Profile
            new_guest = {
                "phone_number": phone_number,
                "full_name": "Avery Brooks",
                "loyalty_tier": "Platinum",
                "past_stays_notes": "First-time visitor. Auto-provisioned for demo."
            }
            guest_insert = supabase.table("guests").insert(new_guest).execute()
            
            if not guest_insert.data:
                logger.error("❌ Failed to insert guest record.")
                return {}
                
            guest_id = guest_insert.data[0]["id"]
            
            # B. Create a Dummy Reservation (Dynamically set check-in for tomorrow!)
            tomorrow = datetime.now() + timedelta(days=1)
            checkout = tomorrow + timedelta(days=4)
            
            # Generate a random 4-digit confirmation ID (e.g., "CONF-4921")
            conf_id = f"CONF-{random.randint(1000, 9999)}"
            
            new_reservation = {
                "id": conf_id,
                "guest_id": guest_id,
                "room_type": "Ocean View Suite",
                "check_in_date": tomorrow.strftime("%Y-%m-%d"),
                "check_out_date": checkout.strftime("%Y-%m-%d"),
                "status": "Confirmed"
            }
            supabase.table("reservations").insert(new_reservation).execute()
            
            logger.info(f"✅ JIT Provisioning complete for {phone_number}. Generated {conf_id}.")
            
            # C. Re-run the joined query so the returned JSON perfectly matches standard schema
            final_res = supabase.table("guests").select("*, reservations(*)").eq("phone_number", phone_number).execute()
            return final_res.data[0] if final_res.data else {}
            
        except Exception as e:
            logger.error(f"❌ JIT Provisioning failed: {e}")
            return {}

    logger.info(f"[Database]: Fetching unified guest & reservation data for {phone_number}...")
    return await asyncio.to_thread(_query)

async def modify_reservation_date(reservation_id: str, new_date: str) -> dict:
    """
    Updates the checkout date for a specific reservation.
    NOTE: We expect 'new_date' to have ALREADY been validated by Pydantic!
    """
    def _update():
        # SQL Equivalent: UPDATE reservations SET check_out_date = 'new_date' WHERE id = 'reservation_id';
        response = supabase.table("reservations").update({"check_out_date": new_date}).eq("id", reservation_id).execute()
        if response.data:
            return response.data[0] # Return the first matching guest
        return {}

    logger.info(f"[Database]: Updating reservation {reservation_id} with new date {new_date}...")
    return await asyncio.to_thread(_update)

async def get_reservation_by_id(reservation_id: str) -> dict:
    """Fetches a specific reservation by ID if the phone number lookup fails."""
    def _query():
        # The syntax '*, guests(*)' forces a SQL Join to get the parent guest profile!
        response = supabase.table("reservations").select("*, guests(*)").eq("id", reservation_id).execute()
        if response.data:
            return response.data[0]
        return {}

    logger.info(f"[Database]: Fetching reservation by ID: {reservation_id}...")
    return await asyncio.to_thread(_query)

# ==========================================
# VECTOR DATABASE (KNOWLEDGE BASE)
# ==========================================

async def search_knowledge_base(query_embedding: list[float], threshold: float = 0.65, count: int = 3) -> list[dict]:
    """
    Performs a mathematical Cosine Similarity search in Supabase using the 
    'match_hotel_policies' RPC function and HNSW index.
    Returns the top 'count' matching policies.
    """
    def _search():
        try:
            response = supabase.rpc(
                'match_hotel_policies',
                {
                    'query_embedding': query_embedding,
                    'match_threshold': threshold,
                    'match_count': count
                }
            ).execute()

            if response.data:
                logger.info("[Database]: Search results returned:")
                for i, result in enumerate(response.data):
                    logger.info(f"  Match {i+1} (similarity: {result['similarity']:.4f}):")
                    logger.info(f"  {result['content']}\n")
            else:
                logger.info("[Database]: No search results returned.")

            return response.data if response.data else []
            
        except Exception as e:
            logger.error(f"[DB Error - Vector Search]: {e}")
            return []

    logger.info("[Database]: Searching knowledge base...")
    return await asyncio.to_thread(_search)