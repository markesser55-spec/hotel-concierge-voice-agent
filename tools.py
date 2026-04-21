"""
Gemini tool implementations (hotel concierge)
============================================
Defines async functions the LLM can call via automatic function calling: they
validate inputs where needed, talk to `database.py`, and return JSON strings for
the model. Also builds the `hotel_concierge_tools` schema for `llm.py`.
"""

import json
import os
import aiohttp
from loguru import logger
from dotenv import load_dotenv
from pydantic import ValidationError
from google import genai
from google.genai import types as genai_types
from pipecat.services.llm_service import FunctionCallParams
import database
from models import ModifyCheckoutRequest

load_dotenv()

# Initialize GenAI specifically for embedding queries inside tools
genai_client = genai.Client()

# Must precisely match the model used in seed_knowledge.py
EMBEDDING_MODEL = "gemini-embedding-001"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# ==========================================
# HOTEL LOCATION CONSTANTS
# ==========================================
HOTEL_NAME     = "The Grand Horizon Resort & Spa"
HOTEL_ADDRESS  = "4360 E Camelback Rd, Phoenix, AZ 85018"
HOTEL_LAT      = 33.5094
HOTEL_LNG      = -111.9977
SEARCH_RADIUS  = 3218.0  # 2 mile radius (Google Places API requires meters)
    
# ==========================================
# RESERVATION TOOLS - Supabase DB
# ==========================================

async def lookup_guest_reservation(params: FunctionCallParams, phone_number: str):
    """
    Looks up a guest profile and their active hotel reservations using their phone number.
    Call this tool immediately when a user asks about their stay or requests a modification.
    
    Args:
        phone_number: The guest's phone number, e.g., '+15551234567'
    """
    logger.info(f"Tool Execution: lookup_guest_reservation for {phone_number}")
    
    # 1. Fetch data from our Database layer
    guest = await database.get_guest_profile(phone_number)

    if not guest:
        logger.info(f"No guest found with phone number: {phone_number}")
        return await params.result_callback({"status": "error", "message": "No guest found with that phone number."})
    
    logger.info(f"Found guest: {guest.get('id')})")
    reservations = await database.get_guest_reservations(guest.get("id"))

    if not reservations:
        logger.info(f"No reservations found for guest: {guest.get('id')}")
    
    # 2. Package the raw data cleanly for Gemini's context window
    result = {
        "status": "success",
        "guest_name": guest.get("full_name"),
        "guest_tier": guest.get("loyalty_tier"),
        "guest_notes": guest.get("past_stays_notes"),
        "reservations": reservations,
    }

    return await params.result_callback(result)

async def modify_reservation_date(params: FunctionCallParams, reservation_id: str, new_check_out_date: str):
    """
    Modifies a guest's checkout date. 
    Requires the reservation_id and the new_check_out_date in YYYY-MM-DD format.
    
    Args:
        reservation_id: The unique reservation ID, e.g., 'CONF-9876'
        new_check_out_date: The new checkout date strictly in YYYY-MM-DD format.
    """
    logger.info(f"Tool Execution: modify_checkout_date for {reservation_id} to {new_check_out_date}")

    try:
        # 1. PYDANTIC SECURITY BOUNCER
        # We pass Gemini's raw output into our strict model.
        safe_request = ModifyCheckoutRequest(
            reservation_id=reservation_id,
            new_check_out_date=new_check_out_date,
        )

        validated_data = safe_request.model_dump(mode='json')

        # 2. IF APPROVED, EXECUTE DATABASE UPDATE
        update_result = await database.modify_reservation_date(
            validated_data["reservation_id"],
            validated_data["new_check_out_date"],
        )

        if update_result:
            logger.info(f"Successfully updated checkout date to: {update_result.get('check_out_date')}")
            return await params.result_callback({"status": "success", "message": "Checkout date updated successfully."})
        else:
            logger.error(f"Failed to update checkout date for {reservation_id}")
            return await params.result_callback({"status": "error", "message": "Failed to update checkout date."})
    except ValidationError as e:
        # 3. SELF-HEALING AI
        # If Gemini passes "next Tuesday" instead of "2026-05-18", Pydantic throws an error.
        # We catch the error so the app doesn't crash, and return a helpful error message to Gemini so it can try again!
        logger.warning(f"Tool Warning: AI passed invalid data. Sending error back to LLM... {str(e)}")
        return await params.result_callback({"status": "error", "message": "Invalid date format. Please provide the date in YYYY-MM-DD format. Example: 2026-05-18"})

# ==========================================
# UTILITY TOOLS (Mock Function)
# ==========================================

async def escalate_to_human(params: FunctionCallParams, reason: str, urgency: str):
    """
    Escalates the conversation to a human front desk agent. 
    Use this instantly if the user is angry, has a billing dispute, or requests something you cannot do.
    
    Args:
        reason: A short summary of why the user needs a human.
        urgency: 'Low', 'Medium', or 'High'
    """
    logger.info(f"Escalation Triggered! Reason: {reason} | Urgency: {urgency}")
    return await params.result_callback({"status": "success", "message": "Escalation ticket created. Tell the guest you are transferring them to the Front Desk Manager."})


# ==========================================
# KNOWLEDGE BASE TOOL (RAG) - Supabase Vector DB
# ==========================================

async def search_hotel_policies(params: FunctionCallParams, question: str):
    """
    Searches the hotel policy manual to answer questions about amenities, 
    pool hours, parking, pets, check-in times, and other hotel rules.
    Use this anytime a guest asks a general question about the hotel.
    
    Args:
        question: The specific question the guest is asking, e.g. "What time does the pool close?"
    """
    logger.info(f"Tool Execution: search_hotel_policies for '{question}'")

    try:
        # 1. Turn the user's question into Math
        # MUST use RETRIEVAL_QUERY and force 768 dimensions to match the DB
        response = await genai_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=question,
            config=genai_types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=768,
            ),
        )
        query_embedding = response.embeddings[0].values

        # 2. Search Postgres using Cosine Similarity (Calls our HNSW RPC)
        results = await database.search_knowledge_base(query_embedding)

        if not results:
            await params.result_callback({
                "status": "success", 
                "message": "No relevant policies found. Tell the guest you don't know and offer to escalate to the Front Desk."
            })
            return
            
        # 3. Format the matching policies nicely for Gemini to read
        policies = [match["content"] for match in results]
        
        return await params.result_callback({
            "status": "success",
            "relevant_policies": policies
        })

    except Exception as e:
        logger.error(f"Tool Error - RAG Search: {e}")
        return await params.result_callback({"status": "error", "message": str(e)})


# ==========================================
# EXTERNAL LIVE API TOOLS (GOOGLE MAPS)
# ==========================================

async def search_nearby_places(params: FunctionCallParams, query: str):
    """
    Searches Google Maps for nearby restaurants, stores, pharmacies, or attractions.
    Use this when a guest asks for recommendations outside of the hotel property.
    
    Args:
        query: What the guest is looking for (e.g., "Italian restaurant", "closest pharmacy").
    """

    logger.info(f"Live API Execution: Google Maps searching for '{query}'")

    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return await params.result_callback({"status": "error", "message": "Google Maps API key is missing."})

    # ENTERPRISE TRICK 1: FieldMasks
    # Google Maps returns massive amounts of JSON (photos, geometry boundaries).
    # We strictly limit the payload to exactly 4 fields, slashing latency and saving LLM context!
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating,places.regularOpeningHours.openNow",
    }
    
    # ENTERPRISE TRICK 2: Context Anchoring
    # If the user just says "Find pizza", Google won't know where to look. 
    # We automatically inject our dummy hotel's location into the hidden search!
    payload = {
    "textQuery": query,
    "languageCode": "en",
    "pageSize": 3,  # Only fetch 3 from Google in the first place
    "locationBias": {
        "circle": {
            "center": {
                "latitude": HOTEL_LAT,
                "longitude": HOTEL_LNG
            },
            "radius": SEARCH_RADIUS
            }
        }
    }

    try:
        # Make a non-blocking asynchronous network request
        async with aiohttp.ClientSession() as session:
            async with session.post(GOOGLE_PLACES_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Google Maps API Error: {response.status}: {error_text}")
                    await params.result_callback({"status": "error", "message": "Failed to contact Google Maps."})
                    return

                data = await response.json()
                places_data = data.get("places", [])

                if not places_data:
                    await params.result_callback({"status": "success", "message": "No places found matching that description nearby."})
                    return
                
                # Format the top 3 results
                formatted_results = []
                for place in places_data[:3]:
                    name = place.get("displayName", {}).get("text", "Unknown Place")
                    rating = place.get("rating", "No rating")
                    address = place.get("formattedAddress", "Unknown Address")

                    is_open = place.get("regularOpeningHours", {}).get("openNow")
                    status_str = "Currently Open" if is_open is True else ("Currently Closed" if is_open is False else "Hours Unknown")

                    formatted_results.append({
                        "name": name,
                        "rating": f"{rating} stars" if rating != "No rating" else rating,
                        "address": address,
                        "status": status_str
                    })

                logger.info("[Google Maps] Results returning to agent:")
                logger.info(json.dumps(formatted_results, indent=2))

                return await params.result_callback(formatted_results)

    except Exception as e:
        logger.error(f"Tool Error - Google Maps API: {e}")
        return await params.result_callback({"status": "error", "message": str(e)})

# We export a list of our tools so we can easily hand the "Menu" to Gemini in Block 3.
hotel_concierge_tools = [
    lookup_guest_reservation,
    modify_reservation_date,
    escalate_to_human,
    search_hotel_policies,
    search_nearby_places,
]