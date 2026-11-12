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

MAX_TOOL_CALLS = 8  # Larkspur's own build capped the loop here; then a human takes over.

TONE_ADDENDUM = ""                       # ✏️ Build 4, step 4.1, intelligence goal
EXTRA_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "cause_in_plain_words",
        "description": (
            "Turn an OpsFeed cause code into one short, customer-friendly sentence "
            "explaining why a flight was disrupted (for example, weather or air traffic "
            "control). Call it after get_flight_status has returned a cause_code, and "
            "pass that code through unchanged. Do not call it before you have the "
            "status, and do not guess a cause from memory. Returns a plain-language "
            "explanation you can quote to the customer; it does not say what Larkspur "
            "owes them, which is check_policy's job."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cause_code": {
                    "type": "string",
                    "enum": ["WX", "ATC", "MX", "CREW", "SEC"],
                    "description": (
                        "The cause_code exactly as returned by get_flight_status: WX weather, "
                        "ATC air traffic control, MX maintenance, CREW crew, SEC security."
                    ),
                },
            },
            "required": ["cause_code"],
        },
    },
    {
        "name": "what_automation_will_not_do",
        "description": (
            "Handbook v14.3, section 7: what the chat assistant is not allowed to do. "
            "Call it whenever a customer asks you to do something that might be outside "
            "your remit, for example cancelling the whole trip, getting a refund, "
            "collecting a fare difference, approving a hotel, rebooking onto another "
            "airline, splitting or moving a large group, or anything about unaccompanied "
            "minors, partner-operated flights, pets in cabin, medical requests or lost "
            "baggage. Check it before you promise or attempt anything of that kind, and "
            "never answer a scope question from memory. Takes no arguments. Returns the "
            "list of things people handle, what you can do instead (explain, hold a seat "
            "for 15 minutes, hand over with a summary), and the rule that nothing is "
            "rebooked until the customer presses Confirm. It does not hand the customer "
            "over itself; use escalate_to_human for that. Do not call it for ordinary "
            "rebooking, flight status or policy questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
CAUSE_SENTENCES = {
    "WX": "Bad weather affected flying at the airport, and flights could not operate safely.",
    "ATC": "Air traffic control restricted the number of flights allowed to take off and land.",
    "MX": "The aircraft needed a maintenance check before it could safely fly.",
    "CREW": "The crew needed to operate the flight was not available in time.",
    "SEC": "A security issue at the airport meant the flight could not operate as planned.",
}


def cause_in_plain_words(cause_code: str) -> Dict[str, Any]:
    """One customer-friendly sentence for an OpsFeed cause code."""
    sentence = CAUSE_SENTENCES.get(str(cause_code).upper())
    if sentence is None:
        return {"error": "Unknown cause_code %s; expected one of %s"
                         % (cause_code, ", ".join(CAUSE_SENTENCES))}
    return {"cause_code": cause_code, "explanation": sentence}


def what_automation_will_not_do() -> Dict[str, Any]:
    """Handbook v14.3, section 7, as data."""
    return {
        "source": "Handbook v14.3, section 7: What chat automation will not do",
        "handled_by_people": [
            "refunds",
            "fare-difference collection",
            "hotel approval",
            "other-airline rebooking",
            "group bookings of 10 or more",
            "unaccompanied minors",
            "itineraries with partner-operated flights",
            "pets in cabin and medical requests on a new flight",
            "baggage tracing",
        ],
        "assistant_can_do": [
            "explain the options and the policy",
            "hold a seat for 15 minutes while the customer decides",
            "hand over to a person with a summary",
        ],
        "rebooking_rule": "Nothing is rebooked until the customer presses Confirm.",
    }


LOCAL_TOOLS: Dict[str, Any] = {                  # ✏️ Build 2, step 2.1: the functions behind them
    "cause_in_plain_words": cause_in_plain_words,
    "what_automation_will_not_do": what_automation_will_not_do,
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
