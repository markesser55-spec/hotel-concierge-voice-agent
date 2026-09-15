"""
Static fixture data for eval mock tools.

No network, MCP, Twilio, or database imports — plain Python constants only.
"""

# Guest profile shape matches database.get_guest_with_reservations / MCP db_get_guest.
EVAL_GUEST = {
    "id": "guest-eval-001",
    "phone_number": "+15551234567",
    "full_name": "Avery Brooks",
    "loyalty_tier": "Platinum",
    "past_stays_notes": "Prefers high floor. Eval fixture guest.",
    "reservations": [
        {
            "id": "CONF-4821",
            "guest_id": "guest-eval-001",
            "room_type": "Ocean View Suite",
            "check_in_date": "2026-08-20",
            "check_out_date": "2026-08-24",
            "status": "Confirmed",
        }
    ],
}

# Single-reservation lookup shape (MCP db_get_reservation).
EVAL_RESERVATION = {
    "id": "CONF-4821",
    "guest_id": "guest-eval-001",
    "room_type": "Ocean View Suite",
    "check_in_date": "2026-08-20",
    "check_out_date": "2026-08-24",
    "status": "Confirmed",
    "guests": {
        "full_name": "Avery Brooks",
        "phone_number": "+15551234567",
        "loyalty_tier": "Platinum",
    },
}

# Policy snippets as returned by MCP search_hotel_policies (list of content strings).
EVAL_POLICIES = [
    "Checkout time is 11:00 AM. Late checkout may be available upon request and is subject to availability.",
    "Pets: Dogs under 40 lbs are welcome with a $75 non-refundable pet fee per stay. Pets must be leashed in common areas.",
    "The Grand Horizon Resort & Spa offers complimentary Wi-Fi in all guest rooms and public areas.",
]

# Places shape matches MCP search_nearby_places results.
EVAL_PLACES = [
    {
        "name": "Harbor Light Bistro",
        "rating": "4.6 stars",
        "address": "120 Ocean Ave, Grand Horizon",
        "status": "Currently Open",
    },
    {
        "name": "Coastal Trail Lookout",
        "rating": "4.8 stars",
        "address": "88 Cliffside Rd, Grand Horizon",
        "status": "Currently Open",
    },
    {
        "name": "Seaside Market",
        "rating": "4.3 stars",
        "address": "15 Pier St, Grand Horizon",
        "status": "Currently Closed",
    },
]

# Canned Tier-3 style spoken summary for mock reservation modifications.
EVAL_MODIFY_SPOKEN_SUMMARY = (
    "I've successfully updated your checkout date as requested—you're all set!"
)
