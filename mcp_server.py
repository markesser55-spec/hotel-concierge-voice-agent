"""
MCP Server: Core Data Service
=============================
A standalone Model Context Protocol server.
This server owns the Database connections, Google Maps API, and Vector embeddings.
It communicates with the Voice Agent via blazing-fast stdio.
"""

import os
import json
import aiohttp
from loguru import logger
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types as genai_types

# We import the untouched database module here!
import database

load_dotenv()

# Initialize the MCP Server
mcp = FastMCP(name="Hotel-Data-Server", host='0.0.0.0', port=8001)

# ==========================================
# EXTERNAL API CONSTANTS
# ==========================================
genai_client = genai.Client()
EMBEDDING_MODEL = "gemini-embedding-001"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
HOTEL_LAT = 33.5094
HOTEL_LNG = -111.9977
SEARCH_RADIUS = 3218.0

# ==============================================================================
# SECURE DATABASE TOOLS
# ==============================================================================

@mcp.tool()
async def db_get_guest(phone_number: str) -> str:
    """Fetches a guest profile and reservations by phone number."""
    logger.info(f"[MCP] Fetching guest data for: {phone_number}")
    data = await database.get_guest_with_reservations(phone_number)
    return json.dumps(data) if data else json.dumps({})

@mcp.tool()
async def db_get_reservation(reservation_id: str) -> str:
    """Fetches a reservation by ID."""
    logger.info(f"[MCP] Fetching reservation: {reservation_id}")
    data = await database.get_reservation_by_id(reservation_id)
    return json.dumps(data) if data else json.dumps({})

@mcp.tool()
async def db_modify_reservation(reservation_id: str, new_date: str) -> str:
    """Modifies a checkout date."""
    logger.info(f"[MCP] Modifying reservation {reservation_id} to {new_date}")
    data = await database.modify_reservation_date(reservation_id, new_date)
    return json.dumps(data) if data else json.dumps({})

# ==============================================================================
# EXTERNAL DATA TOOLS
# ==============================================================================

@mcp.tool()
async def search_hotel_policies(question: str) -> str:
    """Searches the hotel policy manual to answer questions about amenities, pets, etc."""
    logger.info(f"[MCP] Searching policies for: '{question}'")
    try:
        response = await genai_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=question,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=768),
        )
        query_embedding = response.embeddings[0].values
        results = await database.search_knowledge_base(query_embedding)
        
        if not results:
            return json.dumps([])
            
        return json.dumps([match["content"] for match in results])
    except Exception as e:
        logger.error(f"[MCP] Policy Search Error: {e}")
        return json.dumps({"error": str(e)})

@mcp.tool()
async def search_nearby_places(query: str) -> str:
    """Searches Google Maps for nearby restaurants, stores, or attractions."""
    logger.info(f"[MCP] Searching Maps for: '{query}'")
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return json.dumps({"error": "Google Maps API key is missing."})

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating,places.regularOpeningHours.openNow",
    }
    payload = {
        "textQuery": query,
        "languageCode": "en",
        "pageSize": 3,
        "locationBias": {"circle": {"center": {"latitude": HOTEL_LAT, "longitude": HOTEL_LNG}, "radius": SEARCH_RADIUS}}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GOOGLE_PLACES_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    return json.dumps({"error": f"Maps API failed: {await response.text()}"})
                
                data = await response.json()
                results = []
                for p in data.get("places", [])[:3]:
                    is_open = p.get("regularOpeningHours", {}).get("openNow")
                    status = "Currently Open" if is_open is True else ("Currently Closed" if is_open is False else "Hours Unknown")
                    results.append({
                        "name": p.get("displayName", {}).get("text", "Unknown"),
                        "rating": f"{p.get('rating', 'No rating')} stars",
                        "address": p.get("formattedAddress", "Unknown"),
                        "status": status
                    })
                return json.dumps(results)
    except Exception as e:
        logger.error(f"[MCP] Maps Error: {e}")
        return json.dumps({"error": str(e)})

if __name__ == "__main__":
    logger.info("Booting Core Data MCP Server via SSE on port 8001...")
    mcp.run(transport="sse")