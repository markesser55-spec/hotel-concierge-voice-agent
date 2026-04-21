"""
Database layer (Supabase)
==========================
Creates a single Supabase client from environment variables and exposes async
helpers that run blocking SDK calls in a thread pool so they do not block the
asyncio event loop.
"""

import os
import asyncio
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

async def get_guest_profile(phone_number: str) -> dict:
    """
    Looks up a guest by their phone number. 
    """
    def _query():
        # SQL Equivalent: SELECT * FROM guests WHERE phone_number = 'phone_number' LIMIT 1;
        response = supabase.table("guests").select("*").eq("phone_number", phone_number).execute()
        if response.data:
            return response.data[0] # Return the first matching guest
        return {} # Return an empty dictionary if no guest is found

    logger.info(f"[Database]: Looking up guest profile for {phone_number}...")
    return await asyncio.to_thread(_query)

async def get_guest_reservations(guest_id: str) -> list:
    """
    Fetches all active reservations for a specific guest using their UUID.
    """
    def _query():
        # SQL Equivalent: SELECT * FROM reservations WHERE guest_id = 'guest_id' AND status = 'active';
        response = supabase.table("reservations").select("*").eq("guest_id", guest_id).execute()
        return response.data if response.data else []

    logger.info(f"[Database]: Looking up guest reservations for {guest_id}...")
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

# ==========================================
# VECTOR DATABASE (KNOWLEDGE BASE)
# ==========================================

async def search_knowledge_base(query_embedding: list[float], threshold: float = 0.5, count: int = 3) -> list[dict]:
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