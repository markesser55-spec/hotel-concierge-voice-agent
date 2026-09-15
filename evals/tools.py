"""
Eval mock tool provider
=======================
Same tool names and Pipecat signatures as production tools.py, but backed only by
local fixtures. Structurally cannot reach MCP or SMS: this module must never import
auth, twilio, mcp, or production tools.py. Session starts unauthenticated; verify_auth_pin
is mocked to always succeed and unlocks lookup_guest_reservation.
"""

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from evals.fixtures import (
    EVAL_GUEST,
    EVAL_MODIFY_SPOKEN_SUMMARY,
    EVAL_PLACES,
    EVAL_POLICIES,
    EVAL_RESERVATION,
)


def get_eval_hotel_concierge_tools(ani: str):
    """Return side-effect-free concierge tools for evals; session starts unauthenticated.

    Args:
        ani: Caller phone number (accepted for ToolProvider parity; fixtures ignore it
            unless used to stamp phone_number on guest data).

    Returns:
        List of async tools with the same names/signatures as get_hotel_concierge_tools.
    """
    # Session starts unauthenticated; PIN must be verified before reservation data.
    is_authenticated = False

    async def verify_auth_pin(params: FunctionCallParams, pin: str):
        nonlocal is_authenticated
        logger.info("[eval] Tool Execution: verify_auth_pin (always succeeds)")
        is_authenticated = True
        return await params.result_callback({
            "status": "success",
            "message": (
                "PIN verified successfully. You MUST call lookup_guest_reservation again now to fetch the data. "
                "If they want to modify, after summarizing the reservation ask for the new check-out date—"
                "do NOT call route_to_reservation_specialist until they provide it."
            ),
        })

    async def lookup_guest_reservation(params: FunctionCallParams, reservation_id: str = ""):
        logger.info(f"[eval] Tool Execution: lookup_guest_reservation. res_id='{reservation_id}'")
        if not is_authenticated:
            return await params.result_callback({
                "status": "auth_required",
                "message": (
                    "Authentication required. Call verify_auth_pin with the guest's 6-digit code before "
                    "looking up reservation data."
                ),
            })
        if reservation_id:
            data = dict(EVAL_RESERVATION)
            if reservation_id != EVAL_RESERVATION["id"]:
                data = {**EVAL_RESERVATION, "id": reservation_id}
        else:
            data = {**EVAL_GUEST, "phone_number": ani or EVAL_GUEST["phone_number"]}
        return await params.result_callback({
            "status": "success",
            "data": data,
            "message": (
                "Summarize the reservation for the guest (confirmation, room, check-in, check-out). "
                "If they asked to modify dates, ask what they want the new check-out date to be—"
                "do NOT call route_to_reservation_specialist until they provide a concrete new date. "
                "Otherwise briefly ask if they need anything else."
            ),
        })

    async def route_to_reservation_specialist(
        params: FunctionCallParams, guest_request: str, reservation_id: str = ""
    ):
        logger.info(
            f"[eval] Tool Execution: route_to_reservation_specialist "
            f"(request={guest_request!r}, res_id={reservation_id!r})"
        )
        # No Tier-3 Gemini, no MCP write — canned success matching production message shape.
        return await params.result_callback({
            "status": "success",
            "message": (
                "Specialist completed the task. DO NOT read the database. "
                f"Speak this exact summary: '{EVAL_MODIFY_SPOKEN_SUMMARY}' "
                "Then briefly ask if they need anything else."
            ),
        })

    async def search_hotel_policies(params: FunctionCallParams, question: str):
        logger.info(f"[eval] Tool Execution: search_hotel_policies for {question!r}")
        return await params.result_callback({
            "status": "success",
            "relevant_policies": list(EVAL_POLICIES),
            "message": "Summarize for the guest, then briefly ask if they need anything else.",
        })

    async def search_nearby_places(params: FunctionCallParams, query: str):
        logger.info(f"[eval] Tool Execution: search_nearby_places for {query!r}")
        return await params.result_callback({
            "status": "success",
            "places": list(EVAL_PLACES),
            "message": "Summarize for the guest, then briefly ask if they need anything else.",
        })

    async def escalate_to_human(params: FunctionCallParams, reason: str, urgency: str):
        logger.info(f"[eval] Escalation Triggered! Reason: {reason} | Urgency: {urgency}")
        return await params.result_callback({
            "status": "success",
            "message": "Ticket created. Tell guest you are transferring them.",
        })

    return [
        verify_auth_pin,
        lookup_guest_reservation,
        route_to_reservation_specialist,
        search_hotel_policies,
        search_nearby_places,
        escalate_to_human,
    ]
