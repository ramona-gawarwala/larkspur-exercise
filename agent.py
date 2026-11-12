"""Larkspur disruption agent. This is the file you build.

It runs right now, and it is wrong in four places. The trace shows each one
before the code does, so read the trace first:

    python3 run.py K7PQ2M --trace

Where you edit:   grep -n '✏' agent.py   (six marks, one per place)
Steps and gates:  https://anthropicpartnerbasecamp.bts.com/
"""
from __future__ import annotations
from pathlib import Path
import re
from datetime import timedelta, timezone
from typing import Any, Dict, List
from support import (MODEL, SYSTEM_PROMPT, call_local, execute_tool, mcp_client,
                     new_session, next_available_day, record_tool_result,
                     runtime_preamble)
from support import mock_backend
from support.tools import check_policy as _check_policy
from support.mock_backend import (DATA_HORIZON_END, DATA_HORIZON_START, FIXTURE_CLOCK,
                                  load_flights)

MAX_TOOL_CALLS = 8  # Larkspur's own build capped the loop here; then a human takes over.

# --- departures_in_window -----------------------------------------------------
# City, state and UTC offset (on the data's dates) for every airport in flights.csv.
# The data has none of these: every time in the file is local to its airport, and
# customers say "Phoenix" or "Arizona", not "PHX". Facts written by hand: check them.
AIRPORTS = {
    "ABQ": ("Albuquerque", "NM", -6), "AUS": ("Austin", "TX", -5),
    "BNA": ("Nashville", "TN", -5), "BOI": ("Boise", "ID", -6),
    "CLT": ("Charlotte", "NC", -4), "DCA": ("Washington", "DC", -4),
    "DEN": ("Denver", "CO", -6), "LAS": ("Las Vegas", "NV", -7),
    "LGA": ("New York", "NY", -4), "MCO": ("Orlando", "FL", -4),
    "MSP": ("Minneapolis", "MN", -5), "PHX": ("Phoenix", "AZ", -7),
    "RDU": ("Raleigh-Durham", "NC", -4), "SEA": ("Seattle", "WA", -7),
    "SMF": ("Sacramento", "CA", -7),
}
STATE_NAMES = {"NM": "new mexico", "TX": "texas", "TN": "tennessee", "ID": "idaho",
               "NC": "north carolina", "DC": "district of columbia", "CO": "colorado",
               "NV": "nevada", "NY": "new york", "FL": "florida", "MN": "minnesota",
               "AZ": "arizona", "WA": "washington", "CA": "california"}
MAX_DEPARTURES_RETURNED = 12
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _readings(place):
    """Every way `place` can be read -> the airports each reading means: an airport
    code, a city or a state. 'Washington' is a city (DCA) and a state (SEA), so it
    has two readings and the tool asks instead of guessing."""
    key = str(place).strip().lower()
    readings = {}
    for code, (city, state, _) in AIRPORTS.items():
        if key == code.lower():
            readings.setdefault("airport", []).append(code)
        if key == city.lower():
            readings.setdefault("city", []).append(code)
        if key in (state.lower(), STATE_NAMES[state]):
            readings.setdefault("state", []).append(code)
    return {kind: sorted(codes) for kind, codes in readings.items()}


def _resolve_airports(place):
    return sorted({code for codes in _readings(place).values() for code in codes})


def _label(code):
    city, state, _ = AIRPORTS[code]
    return "%s, %s (%s)" % (city, state, code)


def _all_airports():
    return "; ".join(_label(code) for code in sorted(AIRPORTS))


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return "%02d:%02d%s" % (minutes // 60 % 24, minutes % 60, "+1d" if minutes >= 1440 else "")


def departures_in_window(origin, date, from_time=None, to_time=None, dest=None):
    """Flights still due to leave `origin` in a window, straight from flights.csv.
    Filters on ESTIMATED departure (scheduled + delay). Cancelled and diverted
    flights never go in `flights`; they come back in `cancelled`, so Claude can
    say "cancelled" instead of "I can't find it"."""
    origins = _resolve_airports(origin)
    if len(origins) != 1:
        options = "; ".join(_label(c) for c in origins) if origins else _all_airports()
        return {"error": "origin must be exactly one airport, got %r. Choose from: %s."
                         % (origin, options)}
    origin = origins[0]
    destinations = None
    if dest:
        readings = _readings(dest)
        if not readings:
            return {"error": "No airport, city or state matches %r. Leave dest out to see every "
                             "destination, or use one of: %s." % (dest, _all_airports())}
        if len({tuple(codes) for codes in readings.values()}) > 1:
            return {"clarify_needed": True, "place": str(dest),
                    "options": [{"means": "the %s of %s" % (kind, str(dest).title()),
                                 "airports": [_label(c) for c in codes]}
                                for kind, codes in readings.items()],
                    "note": "Ask the customer which one they mean. Do not pick one."}
        destinations = sorted({c for codes in readings.values() for c in codes})
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
        return {"error": "date must be YYYY-MM-DD, got %s" % date}
    for name, value in (("from_time", from_time), ("to_time", to_time)):
        if value and not _HHMM.match(str(value)):
            return {"error": "%s must be 24-hour HH:MM, got %s" % (name, value)}

    result = {"airport": origin, "date": date, "window_used": None, "as_of_local": None,
              "flights": [], "cancelled": [], "total_in_window": 0, "returned": 0, "note": None}
    if not DATA_HORIZON_START <= date <= DATA_HORIZON_END:
        result["note"] = ("No flight data for %s: the data covers %s to %s only."
                          % (date, DATA_HORIZON_START, DATA_HORIZON_END))
        return result

    now = FIXTURE_CLOCK.astimezone(timezone(timedelta(hours=AIRPORTS[origin][2])))
    now_date, now_minutes = now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute
    result["as_of_local"] = now.strftime("%Y-%m-%d %H:%M") + " local at " + origin
    low = _minutes(from_time) if from_time else 0
    high = _minutes(to_time) if to_time else 1439
    requested = "%s to %s" % (_hhmm(low), _hhmm(high))
    already_left = date < now_date
    if date == now_date:
        low = max(low, now_minutes)  # never start earlier than "now"
    if low > high:
        result["window_used"] = requested
        result["note"] = ("That window is over: it is %s at %s, so every flight in it has already "
                          "left or been cancelled." % (now.strftime("%H:%M"), origin))
        return result
    result["window_used"] = "%s to %s" % (_hhmm(low), _hhmm(high))
    if already_left:
        result["note"] = "That date is before the data's current time, so these flights have already left."

    flights, cancelled = [], []
    for row in load_flights():
        if row["origin"] != origin or row["date"] != date or (destinations and row["dest"] not in destinations):
            continue
        scheduled = _minutes(row["sched_dep_local"])
        if row["status"] in ("CANCELLED", "DIVERTED"):
            if low <= scheduled <= high:
                cancelled.append({"flight_no": row["flight_no"], "dest": row["dest"],
                                  "sched_dep": row["sched_dep_local"], "status": row["status"]})
            continue
        estimated = scheduled + row["delay_min"]
        if not already_left and low <= estimated <= high:
            # Only what Claude needs: zero and empty fields are left out (tokens add up).
            entry = {"flight_no": row["flight_no"], "dest": row["dest"],
                     "est_dep": _hhmm(estimated), "status": row["status"],
                     "seats_left_Y": row["seats_left_Y"]}
            if row["delay_min"]:
                entry.update(sched_dep=row["sched_dep_local"], delay_min=row["delay_min"],
                             cause_code=row["cause_code"])
            if row["seats_left_J"]:
                entry["seats_left_J"] = row["seats_left_J"]
            if row["remarks"]:
                entry["remark"] = row["remarks"]
            flights.append((estimated, entry))
    flights.sort(key=lambda pair: (pair[0], pair[1]["flight_no"]))
    result["flights"] = [f for _, f in flights][:MAX_DEPARTURES_RETURNED]
    result["cancelled"] = cancelled
    result["total_in_window"] = len(flights)
    result["returned"] = len(result["flights"])
    if not flights and not already_left:
        result["note"] = "No flight in that window is still due to leave."
    return result


# --- check_policy, compact by default ------------------------------------------
# The given check_policy returns the whole entitlements table, and Claude retells all
# of it whether or not the customer asked. This wrapper leaves out the care, refund and
# goodwill detail unless include_care is true, and says what it left out. Nothing is
# summarised or reworded: a field is passed through unchanged or it is not there.
# escalate, policy_row_id, rebooking, explainer and wording (the phrases never to say)
# are the rule guards and are always kept.
_CARE_FIELDS = ("care", "refund", "goodwill")


def _care_topics(full: Dict[str, Any]) -> List[str]:
    """What the customer could ask about, read from the policy row itself."""
    care = full.get("care") or {}
    refund = full.get("refund") or {}
    meal, hotel = care.get("meal") or {}, care.get("hotel") or {}
    topics = []
    if meal.get("amount_usd") or meal.get("conditional"):
        topics.append("meal")
    if hotel.get("eligible") or hotel.get("instead"):
        topics.append("hotel")
    if (care.get("ground") or {}).get("amount_usd"):
        topics.append("ground transport")
    if refund.get("eligible_on_request") is True or refund.get("non_refundable_gets"):
        topics.append("refund")
    if (full.get("goodwill") or {}).get("eligible"):
        topics.append("goodwill")
    return topics


def check_policy_compact(pnr, cause_code, delay_minutes, status,
                         wait_minutes_for_alternative=None, chosen_option_id=None,
                         include_care=False):
    args = {"pnr": pnr, "cause_code": cause_code, "delay_minutes": delay_minutes,
            "status": status}
    if wait_minutes_for_alternative is not None:
        args["wait_minutes_for_alternative"] = wait_minutes_for_alternative
    if chosen_option_id:
        args["chosen_option_id"] = chosen_option_id
    full = execute_tool("check_policy", args)
    if str(include_care).lower() == "true" or "policy_row_id" not in full:
        return full  # asked for the detail, or an error: pass it through untouched
    compact = {k: v for k, v in full.items() if k not in _CARE_FIELDS}
    topics = _care_topics(full)
    if topics:
        compact["on_request"] = {
            "topics": topics,
            "offer": ("Left out: %s. Offer to explain them; give no amounts or conditions "
                      "until asked, then call again with include_care=true."
                      % ", ".join(topics))}
    else:
        compact["on_request"] = {
            "topics": [],
            "offer": ("Nothing extra is owed: no meal, hotel, ground transport or goodwill "
                      "applies. A refund request follows the fare rules and is handled by "
                      "a person.")}
    return compact


TONE_ADDENDUM = (                        # ✏️ Build 4, step 4.1, intelligence goal
    "\n\nNever address the customer with a title such as Mr., Mrs., Ms. or Miss, and never "
    "guess one from their name: the booking does not record one. Use the name as it appears "
    "on the booking, or no name at all."
)
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
    {
        "name": "fare_rules",
        "description": (
            "Read-only: return Larkspur's written fare rules (Handbook v14.3) for one topic, "
            "so you can explain the general rule to the customer. Use it for questions about "
            "what a fare family allows (e.g. 'Can I change my Basic fare?', 'Is Main Plus "
            "refundable?'), about refund and credit rules when a flight is disrupted (e.g. "
            "'If I cancel, do I get cash or credit?', 'How long does a refund take?'), or "
            "about what this chat cannot do (e.g. 'Can you rebook me on another airline?', "
            "'Can you help with my lost bag?'). NOT for meal, hotel, ground transport or "
            "goodwill questions: those belong to care_entitlements or check_policy, which "
            "answer for this specific booking. The text is general, not about this booking; "
            "if it ever disagrees with a check_policy result, check_policy wins."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["fare_families", "disruption_rights", "chat_limits"],
                    "description": (
                        "fare_families: what Basic, Main, Main Plus and First allow (changes, "
                        "refunds, credits). disruption_rights: rebooking windows, refund vs "
                        "credit, refund timing, diversions. chat_limits: what the chat hands "
                        "to a person instead of doing itself."
                    ),
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "departures_in_window",
        "description": (
            "List flights still due to leave one airport in a time window on one date, for "
            "example 'what is still leaving Denver this afternoon?'. It needs no PNR: take the "
            "airport and date from the booking's segment. Do not use it for a booking's "
            "rebooking options (search_alternatives) or one named flight (get_flight_status). "
            "Returns flights by estimated departure with destination, status and seats left, "
            "plus window_used and a separate 'cancelled' list: tell the customer which window "
            "you covered, and say a flight is cancelled instead of saying you cannot find it. "
            "For a region such as 'the Southwest', leave dest out and name only destinations "
            "in the results. If it answers clarify_needed, ask the customer which place they "
            "mean; do not pick one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": (
                    "Airport code or city (DEN, Denver), from the booking's segment, not the "
                    "customer.")},
                "date": {"type": "string", "description": (
                    "YYYY-MM-DD, for example 2025-05-08, from the booking's segment. Never use "
                    "the 'Current time' line: it is the real date, and the data covers "
                    "2025-05-07 to 2025-05-09.")},
                "from_time": {"type": "string", "description": (
                    "Optional, 24-hour HH:MM local to the airport. For a named period set BOTH "
                    "from_time and to_time: this morning 05:00 to 11:59, this afternoon 12:00 "
                    "to 17:59, this evening 18:00 to 23:59. For 'still leaving' or 'from now' "
                    "leave both out: the tool starts at the data's current time.")},
                "to_time": {"type": "string", "description": (
                    "Optional, HH:MM. Set together with from_time (see from_time). Defaults to "
                    "23:59.")},
                "dest": {"type": "string", "description": (
                    "Optional. A code (PHX), city (Phoenix) or state (Arizona, AZ); a state "
                    "returns all its airports. A region is not a place: leave dest out.")},
            },
            "required": ["origin", "date"],
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


_FARE_RULES_FILE = Path(__file__).parent / "data" / "americas" / "fare_rules_excerpt.md"
# Topic -> handbook section heading. Section 6 (care while you wait) is left out on
# purpose: meal/hotel/goodwill belong to care_entitlements and check_policy.
_FARE_RULE_SECTIONS = {
    "fare_families": "### 4.",
    "disruption_rights": "### 5.",
    "chat_limits": "### 7.",
}


def fare_rules(topic):
    """Read-only: the handbook text for one fare-rules topic. Issues nothing."""
    heading = _FARE_RULE_SECTIONS.get(topic)
    if heading is None:
        return {"error": "Unknown topic %r. Use one of: %s"
                         % (topic, ", ".join(_FARE_RULE_SECTIONS))}
    try:
        text = _FARE_RULES_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": "Fare rules are unavailable: %s" % exc}
    start = text.find(heading)
    if start == -1:
        return {"error": "Section for %r not found in the handbook excerpt." % topic}
    end = text.find("\n### ", start + len(heading))
    section = text[start:] if end == -1 else text[start:end]
    return {
        "topic": topic,
        "source": "Larkspur Handbook v14.3 (fare rules excerpt)",
        "text": section.strip(),
        "note": ("General rule, not specific to this booking. If it disagrees with a "
                 "check_policy result, check_policy wins."),
    }


LOCAL_TOOLS: Dict[str, Any] = {          # ✏️ Build 2, step 2.1: the functions behind them
    "next_available_day": next_available_day,
    "care_entitlements": care_entitlements,
    "fare_rules": fare_rules,
    "departures_in_window": departures_in_window,
    "check_policy": check_policy_compact,  # same name as the given tool: runs in its place
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


def _system_blocks(clock: str) -> List[Dict[str, Any]]:
    """Static instructions first, with the cache breakpoint at their end: tools come
    before system in the cached prefix, so this one mark caches both, across
    conversations. The clock goes AFTER the mark, and run_agent reads it ONCE per
    conversation: it changes every second, and a new value on every turn would make
    each message-level cache entry miss (it is part of their prefix)."""
    return [
        {"type": "text", "text": SYSTEM_PROMPT + TONE_ADDENDUM,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": clock},
    ]


def _with_message_breakpoint(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A copy of `messages` with one cache breakpoint on the last block, so each turn
    reads the earlier turns from cache. Never marks the stored list: breakpoints left
    on old turns would pile up past the API's limit of four."""
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    blocks[-1] = dict(blocks[-1], cache_control={"type": "ephemeral"})
    return messages[:-1] + [{"role": last["role"], "content": blocks}]


def run_agent(pnr: str, last_name: str, message: str) -> str:            # ✏️ Build 1, step 1.2
    """Run the tool loop until Claude stops asking for tools. Return its final text."""
    client, tracer = new_session()
    tools = tool_list()
    clock = runtime_preamble().strip()
    messages = [
        {"role": "user", "content": f"PNR {pnr}, last name {last_name}. {message}"},
    ]

    response = client.messages.create(
        model=MODEL, max_tokens=4096, system=_system_blocks(clock),
        thinking={"type": "adaptive"}, tools=tools, messages=_with_message_breakpoint(messages),
    )

    turns = 1
    while response.stop_reason == "tool_use" and turns < MAX_TOOL_CALLS:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results(response)})
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=_system_blocks(clock),
            thinking={"type": "adaptive"}, tools=tools, messages=_with_message_breakpoint(messages),
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
                "Resolve what Larkspur owes this customer for a disruption: rebooking waiver, "
                "escalation triggers and, on request, refund path, meal/hotel/ground care and "
                "goodwill. Call it after get_flight_status, with pnr, cause_code, delay_minutes "
                "and status copied from that result (all four are required); fare_family, "
                "loyalty_tier and whether this is overnight are looked up from the booking, so "
                "never ask the customer for them. Do not use it for an ON_TIME flight (nothing "
                "is owed and there is no cause_code to send), before get_flight_status, to find "
                "flights (search_alternatives, departures_in_window) or to issue anything "
                "(issue_voucher). Every response carries a policy_row_id: cite it if you "
                "reference this decision again. By default the result leaves out the meal, "
                "hotel, ground, refund and goodwill details and lists them under on_request: "
                "offer them, and set include_care=true only when the customer asks."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string", "description": (
                        "The booking code from the customer's first message.")},
                    "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"],
                                   "description": "From get_flight_status."},
                    "delay_minutes": {"type": "integer", "description": (
                        "Minutes late, from get_flight_status. Send 0 for CANCELLED and "
                        "DIVERTED: the status decides and the delay is ignored.")},
                    "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"],
                               "description": "From get_flight_status."},
                    "wait_minutes_for_alternative": {"type": "integer", "description": (
                        "Optional. How many minutes the customer will wait for the flight they "
                        "chose. Send it only after they choose; it decides whether the meal "
                        "credit applies. Leave it out before that and the meal comes back as "
                        "conditional.")},
                    "chosen_option_id": {"type": "string", "description": (
                        "Optional. The option_id the customer chose from search_alternatives. "
                        "Send it only after they choose: it tells the policy whether the new "
                        "flight is on a later day, which decides hotel cover. Leave it out "
                        "before that.")},
                    "include_care": {"type": "boolean", "description": (
                        "Default false. True only after the customer asks about meals, hotel, "
                        "ground transport, refund or goodwill: returns those details.")},
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
