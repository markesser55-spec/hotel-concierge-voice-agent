"""
Pydantic request models
=======================
Shared validation schemas for data that may originate from the LLM before it is
passed to `database.py` or tool functions (e.g. reservation changes).
"""

from datetime import date
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ENTERPRISE DATA MODELS (The Security Bouncer)
# Best Practice: Defense-in-Depth and Deterministic Validation.
# These schemas strictly validate LLM output before it touches our database.
# ---------------------------------------------------------------------------

class ModifyCheckoutRequest(BaseModel):
    """
    Schema for validating requests to modify a reservation's checkout date.
    Pydantic will automatically verify that 'new_check_out_date' is a valid calendar date
    before the Python function is allowed to run.
    """
    reservation_id: str = Field(
        ..., 
        description="The unique reservation identifier, e.g., CONF-9876"
    )
    
    # SECURITY FEATURE: Pydantic's 'date' type automatically rejects ANY string 
    # that isn't a valid YYYY-MM-DD date, killing SQL injections instantly!
    new_check_out_date: date = Field(
        ..., 
        description="The new checkout date in standard YYYY-MM-DD format."
    )