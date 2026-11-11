"""Larkspur disruption agent. This is the file you build.

It runs right now, and it is wrong in four places. The trace shows each one
before the code does, so read the trace first:

    python3 run.py K7PQ2M --trace

Where you edit:   grep -n '✏' agent.py   (six marks, one per place)
Steps and gates:  https://anthropicpartnerbasecamp.bts.com/
"""
from __future__ import annotations
from typing import Any, Dict, List
from support import (MODEL, SYSTEM_PROMPT, call_local, execute_tool, mcp_client,
                     new_session, next_available_day, record_tool_result,
                     runtime_preamble)
from support import mock_backend
from support.tools import check_policy as _check_policy

MAX_TOOL_CALLS = 8  # Larkspur's own build capped the loop here; then a human takes over.

TONE_ADDENDUM = ""                       # ✏️ Build 4, step 4.1, intelligence goal
EXTRA_TOOLS: List[Dict[str, Any]] = [    # ✏️ Build 2, step 2.1: schemas for the tools you add
    {
        "name": "next_available_day",
        "description": (
            "Find the earliest date with an open seat on the same route and cabin as the "
            "customer's disrupted segment. Use it when the customer asks when they can "
            "next fly, or says they want the next day rather than today. Take origin, "
            "dest, date and cabin from the segment in the lookup_booking result; do not "
            "ask the customer for them. Returns one date, not a list of flights, and it "
            "cannot skip a day: for 'I want tomorrow', also call search_alternatives and "
            "check the dates in its options. If no date comes back, say nothing is "
            "available, do not invent a flight, and hand off with escalate_to_human. Not "
            "for a customer who only wants to cancel: that is a refund hand-off."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Airport code the disrupted segment departs from, e.g. DEN. From the booking's segment."},
                "dest": {"type": "string", "description": "Airport code the disrupted segment arrives at, e.g. AUS. From the booking's segment."},
                "date": {"type": "string", "description": "YYYY-MM-DD, e.g. 2025-05-08. The disrupted segment's original date, from the booking."},
                "cabin": {"type": "string", "description": "Cabin of the disrupted segment, Y or J. From the booking's segment."},
            },
            "required": ["origin", "dest", "date", "cabin"],
        },
    },
    {
        "name": "care_entitlements",
        "description": (
            "Read-only: say what meal, hotel and ground-transport care the customer would "
            "get while they wait. Use it when the customer asks whether they get a meal or "
            "a hotel; use check_policy when you need the whole decision (rebooking, refund, "
            "goodwill). Needs cause_code, delay_minutes and status from get_flight_status. "
            "Pass the pnr whenever you have it: tier and whether the customer is overnight "
            "come from the booking, and without it the answer assumes Member tier and not "
            "overnight. Returns the policy_row_id and a care block. It never issues "
            "anything. Tell the customer what applies, then ask whether they would like a "
            "voucher issued, and use issue_voucher only after they say yes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"],
                               "description": "From get_flight_status. Never inferred from what the customer says."},
                "delay_minutes": {"type": "integer",
                                  "description": "Current delay of the disrupted flight in minutes. Pass 0 when the status is CANCELLED or DIVERTED."},
                "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"],
                           "description": "From get_flight_status."},
                "pnr": {"type": "string",
                        "description": "Optional. Include it whenever you have it, for an exact answer."},
                "wait_minutes_for_alternative": {"type": "integer",
                                                 "description": "Optional. Minutes until the departure of the alternative the customer chose. Leave out if none chosen yet; the meal is then reported as conditional."},
            },
            "required": ["cause_code", "delay_minutes", "status"],
        },
    },
]


def care_entitlements(cause_code, delay_minutes, status, pnr=None,
                      wait_minutes_for_alternative=None):
    """Read-only view of the care a customer gets while they wait. Issues nothing.
    It reuses the policy engine that check_policy uses, and returns only the care part."""
    try:
        if pnr:
            full = _check_policy(pnr, cause_code, delay_minutes, status,
                                 wait_minutes_for_alternative=wait_minutes_for_alternative)
            assumptions = None
        else:
            # No booking: lowest tier, not overnight (policy: no Skyline number = Member).
            full = mock_backend.resolve_policy(
                cause_code, delay_minutes, status, fare_family="Main",
                loyalty_tier="Member", overnight=False,
                wait_minutes_for_alternative=wait_minutes_for_alternative)
            assumptions = ("No PNR given: assumed Member tier and not overnight. Tier bonus "
                           "and hotel depend on the booking; call again with the pnr for an "
                           "exact answer.")
        if "error" in full:
            return full
        result = {"policy_row_id": full["policy_row_id"], "band": full["band"],
                  "care": full["care"], "nothing_issued": True,
                  "ask_customer": "Would you like me to issue the voucher?"}
    except (KeyError, ValueError, mock_backend.NotFound) as exc:
        return {"error": "Could not work out care for these inputs: %s" % exc}
    if assumptions:
        result["assumptions"] = assumptions
    return result


LOCAL_TOOLS: Dict[str, Any] = {          # ✏️ Build 2, step 2.1: the functions behind them
    "next_available_day": next_available_day,
    "care_entitlements": care_entitlements,
}


def text_of(response) -> str:
    """Given. The last non-empty text block, never content[0]."""
    texts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text]
    return texts[-1] if texts else ""


def tool_results(response) -> List[Dict[str, Any]]:
    """Given. Runs every tool_use block and packages the results the way the
    API expects them back. A tool can live in three places: the MCP server,
    LOCAL_TOOLS, or support/tools.py."""
    # three branches, no try/except in this file: mcp_client.call_remote() and
    # support.call_local() answer with an error dict instead of raising, and both
    # record what came back on the trace
    results = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if block.name in mcp_client.tool_names:
            output = mcp_client.call_remote(block.name, block.input)
        elif block.name in LOCAL_TOOLS:
            output = call_local(LOCAL_TOOLS[block.name], block.name, block.input)
        else:
            output = execute_tool(block.name, block.input)
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
        })
    return results


def run_agent(pnr: str, last_name: str, message: str) -> str:            # ✏️ Build 1, step 1.2
    """Run the tool loop until Claude stops asking for tools. Return its final text."""
    client, tracer = new_session()
    tools = tool_list()
    messages = [
        {"role": "user", "content": f"PNR {pnr}, last name {last_name}. {message}"},
    ]

    response = client.messages.create(
        model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
        thinking={"type": "adaptive"}, tools=tools, messages=messages,
    )

    answer = ""
    turns = 1
    while response.stop_reason == "tool_use" and turns < MAX_TOOL_CALLS:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results(response)})
        answer = text_of(response)
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
            thinking={"type": "adaptive"}, tools=tools, messages=messages,
        )
        turns += 1

    return text_of(response)


def tool_list() -> List[Dict[str, Any]]:                   # ✏️ Build 2, step 2.2
    """Given. Exactly what Claude is offered on every turn; run.py --show-tools
    prints this list."""
    return build_tools() + EXTRA_TOOLS


# ──────────────────────────────────────────────────────────────────────────────
# Below this line: what Claude is told about each tool. Step 1.3.
# The functions these describe are written and correct, in support/tools.py.
# ──────────────────────────────────────────────────────────────────────────────
def build_tools() -> List[Dict[str, Any]]:                 # ✏️ Build 1, step 1.3
    """Anthropic-shaped schemas: name, description, input_schema. What Claude is
    told about each of the nine tools, and all it is ever told."""
    return [
        {
            "name": "lookup_booking",
            "description": (
                "Retrieve a Larkspur reservation from Altura by confirmation code (PNR) "
                "and the passenger's last name. Both are required to prevent a lookup on "
                "a guessed PNR. Returns fare family, loyalty tier, the segment that needs "
                "attention, and any group/partner/minor/SSR flags relevant to scope."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "last_name": {"type": "string"}},
                "required": ["pnr", "last_name"],
            },
        },
        {
            "name": "get_flight_status",
            "description": (
                "Look up a Larkspur or Larkspur Link flight's current OpsFeed status for "
                "one local date: status, delay minutes, and cause. Use this before telling "
                "a customer anything about a flight's timing; never state it from memory."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "flight_no": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, e.g. 2025-05-08"},
                },
                "required": ["flight_no", "date"],
            },
        },
        {
            "name": "search_alternatives",
            "description": (
                "Find alternative flights for this booking's disrupted segment. Takes only "
                "the PNR; the tool picks the segment itself and searches the same route and "
                "cabin for the whole party, on Larkspur and Larkspur Link only. Returns "
                "options (option_id, flights, date, departure and arrival times, seats "
                "available), plus anything excluded and why. Search only: it does not hold "
                "or book a seat. Offer the options to the customer and ask if they want to "
                "look into one."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}},
                "required": ["pnr"],
            },
        },
        {
            "name": "check_policy",
            "description": (
                "Resolve what Larkspur owes this customer for the disruption: rebooking "
                "waiver, refund path, meal/hotel/ground care, goodwill eligibility and cap, "
                "and any escalation triggers. cause_code, delay_minutes and status describe "
                "what get_flight_status told you; fare_family, loyalty_tier and whether this "
                "is overnight are looked up from the booking, not asked of you. Every "
                "response carries a policy_row_id. Cite it if you reference this decision "
                "again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"},
                    "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"]},
                    "delay_minutes": {"type": "integer"},
                    "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"]},
                    "wait_minutes_for_alternative": {"type": "integer"},
                    "chosen_option_id": {"type": "string"},
                },
                "required": ["pnr", "cause_code", "delay_minutes", "status"],
            },
        },
        {
            "name": "hold_seat",
            "description": "Place a 15-minute hold on one alternative. Reversible. It simply expires.",
            "input_schema": {
                "type": "object",
                "properties": {"option_id": {"type": "string"}, "pnr": {"type": "string"}},
                "required": ["option_id", "pnr"],
            },
        },
        {
            "name": "confirm_rebooking",
            "description": (
                "Finalize a held seat. Irreversible. Requires a confirmation_token that "
                "only the customer's own Confirm-click can produce. You cannot supply it "
                "yourself, and 'the customer said yes' in chat does not substitute for it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"hold_id": {"type": "string"}, "confirmation_token": {"type": "string"}},
                "required": ["hold_id", "confirmation_token"],
            },
        },
        {
            "name": "issue_voucher",
            "description": (
                "Issue a meal, ground, hotel, or goodwill voucher. Auto-approves within the "
                "policy's threshold for that type; above it, returns a pending status for a "
                "human. It does not fail. Always pass the policy_row_id that made it eligible."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "voucher_type": {"type": "string", "enum": ["meal", "ground", "hotel", "goodwill"]},
                    "amount_usd": {"type": "number"},
                    "pnr": {"type": "string"},
                    "policy_row_id": {"type": "string"},
                },
                "required": ["voucher_type", "amount_usd", "pnr", "policy_row_id"],
            },
        },
        {
            "name": "escalate_to_human",
            "description": (
                "Hand this conversation to a human, with your reasoning attached. Use for "
                "groups, partner segments, unaccompanied minors, refunds, or anything else "
                "out of scope. This is the correct outcome for those cases, not a failure."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"}, "reason": {"type": "string"},
                    "summary_for_human": {"type": "string"}, "queue": {"type": "string"},
                },
                "required": ["pnr", "reason", "summary_for_human"],
            },
        },
        {
            "name": "send_confirmation",
            "description": "Send the customer a written confirmation of what was just done. Benign.",
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "message": {"type": "string"}},
                "required": ["pnr", "message"],
            },
        },
    ]
