"""
Gemini Tool Implementations (Optimized Voice API Gateway)
=========================================================
Eliminates synchronous LLM chaining for reads. The Voice Agent connects directly to the 
MCP microservice over ultra-fast localhost SSE using a persistent connection.
"""

import json
import contextlib
from loguru import logger
from pydantic import BaseModel, Field
from pipecat.services.llm_service import FunctionCallParams
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from google import genai
from google.genai import types as genai_types
import auth
from prompts import TIER_3_SPECIALIST_PROMPT

genai_client = genai.Client()

# ==========================================
# CLAUDE'S PERSISTENT SESSION OPTIMIZATION
# ==========================================
_mcp_session: ClientSession | None = None
_exit_stack: contextlib.AsyncExitStack | None = None

async def get_mcp_session() -> ClientSession:
    """Return a cached MCP session, initializing it on first call."""
    global _mcp_session, _exit_stack
    if _mcp_session is None:
        logger.info("Initializing persistent MCP SSE Connection...")
        _exit_stack = contextlib.AsyncExitStack()
        try:
            streams = await _exit_stack.enter_async_context(sse_client("http://127.0.0.1:8001/sse"))
            _mcp_session = await _exit_stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await _mcp_session.initialize()
            logger.info("Persistent MCP session established over localhost")
        except Exception as e:
            logger.error(f"Failed to initialize MCP session: {e}")
            await _exit_stack.aclose()
            _exit_stack = None
            _mcp_session = None
            raise
    return _mcp_session

async def call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Invoke an MCP tool using the persistent session."""
    global _mcp_session, _exit_stack
    logger.info(f"🌐 [MCP Client] Forwarding request: '{tool_name}'...")
    try:
        session = await get_mcp_session()
        result = await session.call_tool(tool_name, arguments=arguments)
        return result.content[0].text
    except Exception as e:
        logger.error(f"[MCP Client Error] {e}")
        # If the connection drops, aggressively reset the session so next call reconnects
        if _exit_stack:
            await _exit_stack.aclose()
            _exit_stack = None
            _mcp_session = None
        return json.dumps({"error": str(e)})

class SpecialistDecision(BaseModel):
    """JSON schema for Tier 3 specialist output: action, ids, dates, and spoken summary."""

    action: str = Field(description="Must be 'lookup', 'modify', or 'error'")
    reservation_id: str = Field(description="The specific reservation ID, if applicable. Otherwise empty.")
    new_date: str = Field(description="The new checkout date strictly in YYYY-MM-DD, if applicable. Otherwise empty.")
    spoken_summary: str = Field(description="A highly concise, 1-2 sentence summary for the Voice Agent to speak aloud.")

def get_hotel_concierge_tools(ani: str):
    """Return Pipecat tool callables for one caller, with shared PIN auth and MCP-backed reads/writes.

    Args:
        ani: Caller phone number (used for SMS PIN and implicit guest lookup).

    Returns:
        List of async tools to register on the voice LLM for this session.
    """
    is_authenticated = False

    async def verify_auth_pin(params: FunctionCallParams, pin: str):
        nonlocal is_authenticated
        logger.info("Tool Execution: verify_auth_pin")
        success = await auth.check_verification_pin(ani, pin)
        if success:
            is_authenticated = True
            return await params.result_callback({
                "status": "success",
                "message": (
                    "PIN verified successfully. You MUST call lookup_guest_reservation again now to fetch the data. "
                    "If they want to modify, after summarizing the reservation ask for the new check-out date—"
                    "do NOT call route_to_reservation_specialist until they provide it."
                ),
            })
        else:
            return await params.result_callback({"status": "error", "message": "Incorrect PIN. Ask the user to try again."})

    async def lookup_guest_reservation(params: FunctionCallParams, reservation_id: str = ""):
        nonlocal is_authenticated
        logger.info(f"Tool Execution: lookup_guest_reservation. res_id='{reservation_id}'")
        
        if not is_authenticated:
            logger.info("Guest not authenticated. Triggering SMS PIN...")
            sent = await auth.send_verification_pin(ani)
            if sent:
                return await params.result_callback({
                    "status": "auth_required",
                    "message": "A 6-digit PIN has been sent. Follow prompt instructions to ask the user for it."
                })
            return await params.result_callback({"status": "error", "message": "Failed to send SMS PIN. Escalate to human."})

        # DIRECT READ: Fast MCP call returned directly to Tier 1 stream
        if reservation_id:
            data_str = await call_mcp_tool("db_get_reservation", {"reservation_id": reservation_id})
        else:
            data_str = await call_mcp_tool("db_get_guest", {"phone_number": ani})
            
        guest_data = json.loads(data_str)
        if not guest_data or "error" in guest_data:
            return await params.result_callback({
                "status": "error",
                "message": "No reservation found. Ask the guest for their reservation number, then call lookup_guest_reservation with that reservation_id.",
            })

        return await params.result_callback({
            "status": "success",
            "data": guest_data,
            "message": (
                "Summarize the reservation for the guest (confirmation, room, check-in, check-out). "
                "If they asked to modify dates, ask what they want the new check-out date to be—"
                "do NOT call route_to_reservation_specialist until they provide a concrete new date. "
                "Otherwise briefly ask if they need anything else."
            ),
        })

    async def route_to_reservation_specialist(params: FunctionCallParams, guest_request: str, reservation_id: str = ""):
        nonlocal is_authenticated
        
        if not is_authenticated:
            return await params.result_callback({
                "status": "error",
                "message": (
                    "User is not authenticated. Call lookup_guest_reservation first to send a PIN and load the reservation. "
                    "Do not retry this specialist tool until auth and lookup succeed and the guest has given a new check-out date."
                ),
            })

        logger.info("[Tier 3 Specialist] Waking up Gemini 2.5 Flash for secure mutation...")
        if reservation_id:
            data_str = await call_mcp_tool("db_get_reservation", {"reservation_id": reservation_id})
        else:
            data_str = await call_mcp_tool("db_get_guest", {"phone_number": ani})
            
        guest_data = json.loads(data_str)
        if not guest_data or "error" in guest_data:
            return await params.result_callback({
                "status": "error",
                "message": "No reservation found for this guest. Ask for a reservation number, look it up, then retry with reservation_id.",
            })

        tier_3_prompt = TIER_3_SPECIALIST_PROMPT.format(guest_data=json.dumps(guest_data), guest_request=guest_request)
        
        try:
            # TIER 3 DATABASE MUTATION: We use temperature=0.0 for deterministic writes
            response = await genai_client.aio.models.generate_content(
                model="gemini-2.5-flash", 
                contents=tier_3_prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SpecialistDecision,
                    temperature=0.0 
                )
            )
            decision = SpecialistDecision.model_validate_json(response.text)

            if decision.action == "modify" and decision.reservation_id and decision.new_date:
                logger.info(f"⚡ Executing MCP Database Write: {decision.reservation_id} -> {decision.new_date}")
                update_str = await call_mcp_tool("db_modify_reservation", {
                    "reservation_id": decision.reservation_id,
                    "new_date": decision.new_date
                })
                update_result = json.loads(update_str)
                if not update_result or "error" in update_result:
                     return await params.result_callback({"status": "error", "message": "The backend database failed to update."})

                return await params.result_callback({
                    "status": "success",
                    "message": (
                        f"Specialist completed the task. DO NOT read the database. "
                        f"Speak this exact summary: '{decision.spoken_summary}' "
                        f"Then briefly ask if they need anything else."
                    ),
                })

            return await params.result_callback({
                "status": "error",
                "message": (
                    f"Modification was not applied. Speak this to the guest: '{decision.spoken_summary}' "
                    f"If a new check-out date is missing, ask for it, then call route_to_reservation_specialist again."
                ),
            })

        except Exception as e:
            logger.error(f"[Tier 3 Specialist] Error: {e}")
            return await params.result_callback({"status": "error", "message": "Failed to modify reservation. Escalate."})

    async def search_hotel_policies(params: FunctionCallParams, question: str):
        # DIRECT READ: Sent straight back to Tier 1 stream
        logger.info(f"Tool Execution: search_hotel_policies for '{question}'")
        data_str = await call_mcp_tool("search_hotel_policies", {"question": question})
        policies = json.loads(data_str)
        if isinstance(policies, dict) and "error" in policies:
            return await params.result_callback({"status": "error", "message": policies["error"]})
        if not policies:
            return await params.result_callback({"status": "success", "message": "No relevant policies found. Offer to escalate."})
        return await params.result_callback({
            "status": "success",
            "relevant_policies": policies,
            "message": "Summarize for the guest, then briefly ask if they need anything else.",
        })

    async def search_nearby_places(params: FunctionCallParams, query: str):
        # DIRECT READ: Sent straight back to Tier 1 stream
        logger.info(f"Tool Execution: search_nearby_places for '{query}'")
        data_str = await call_mcp_tool("search_nearby_places", {"query": query})
        places = json.loads(data_str)
        if isinstance(places, dict) and "error" in places:
            return await params.result_callback({"status": "error", "message": places["error"]})
        if not places:
            return await params.result_callback({"status": "success", "message": "No places found matching that description nearby."})
        return await params.result_callback({
            "status": "success",
            "places": places,
            "message": "Summarize for the guest, then briefly ask if they need anything else.",
        })

    async def escalate_to_human(params: FunctionCallParams, reason: str, urgency: str):
        logger.info(f"Escalation Triggered! Reason: {reason} | Urgency: {urgency}")
        return await params.result_callback({"status": "success", "message": "Ticket created. Tell guest you are transferring them."})

    return [
        verify_auth_pin,
        lookup_guest_reservation,
        route_to_reservation_specialist,
        search_hotel_policies,
        search_nearby_places,
        escalate_to_human
    ]