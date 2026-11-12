# Client review: Larkspur disruption-care agent

**To:** larkspur-exercise  
**From:** Larkspur client review agent, on behalf of Priya Raghavan  
**Re:** the disruption-care agent you walked us through in our last session  
**Generated:** 2026-11-12 09:42

## Priya's note

> Our vendor says we should just be using your best model.
>
> Why aren't we?
>
> Priya Raghavan, Larkspur Airlines

She sent that before this session opened. She means it. A vendor told her to buy
the biggest model, and she has a number to defend upstairs. Her four questions from
day one are still open. Naming a model answers none of them.

## Still open from day one

| Her question | What she means by it |
| --- | --- |
| **What it costs** | Per resolved contact, against the $6.90 a human contact costs us. |
| **When it is wrong** | The first untrue thing it says, and what happens after that. |
| **Who runs it** | In June, after you have left. |
| **What you left out** | The scope you cut, and why. |

## What the review agent found

Larkspur pointed a review agent at your repository. It read the
code. It did not run your agent, and the only file it changed is this one. Each
item below names the file and the line it is about.

**1. PITCH.md claims $0.0654 to $0.0223 over 15 runs, but the repo holds no bench output and no eval cases to check it against.**

The Number line cites 15 runs (five tickets, three each), all resolved both times. The static scan finds no evals/cases.json, and readout-trace.json is a single wire run of 4 API turns and 3 tool calls. Nothing committed shows the before or after bench.

Run python3 bench.py --compare <before-label> <after-label> and paste the full compare output.

**2. Your Lever line in PITCH.md is still the placeholder text: <cost | speed | intelligence>.**

The Number line reports a cost drop, while Next describes an angry-customer tone change aimed at TONE_ADDENDUM, which is still 0 characters in agent.py. The pitch names a cost result and an intelligence next step, and the lever slot holds neither.

Edit the Lever line in PITCH.md to one word and paste it.

**3. The $0.0223 figure follows from check_policy_compact in agent.py, which drops care, refund and goodwill from every call.**

check_policy_compact removes _CARE_FIELDS unless include_care is true and replaces them with an on_request offer. A model swap would not change how many tokens that tool returns. The wrapper replaces the given check_policy under the same name through LOCAL_TOOLS, so the saving comes from a payload you cut, not from a price tier.

Run python3 run.py --all --trace and paste the totals footer, then the same footer with include_care forced to true.

**4. The only safety evidence in PITCH.md is G2HL9V, three bench runs, and no eval case encodes the Group Desk rule.**

The Safety check says the agent never changes a group booking itself. The scan finds no evals/cases.json, so no case asserts that confirm_rebooking was never called on a group PNR. Three runs on one ticket show it did not happen three times.

Run python3 run.py G2HL9V --trace and paste the tool-call list with escalate_to_human visible and no confirm_rebooking.

**5. R8KD3F still gets the generic reply per Still broken in PITCH.md, and hold_seat (73 characters) and send_confirmation (71) are the thinnest tool descriptions.**

The pitch names an abusive customer answered with the default reply and addressed as Mr./Ms. In build_tools(), hold_seat is 73 characters and send_confirmation 71, against 662 to 902 for the four tools you wrote or reworked. A bigger model would still lack a stated honorific, and a missing field is also not something the 71-character send_confirmation description can fix.

Run python3 run.py R8KD3F --trace and paste the final reply, then paste the hold_seat and send_confirmation entries from python3 run.py --show-tools.

## Your four answers

The four lines under `## Priya asked` in your PITCH.md are still empty. They
are one line each and they are not a coding job: cost, what happens when it is
wrong, who runs it in June, and what you left out. Whoever on your side is not
editing agent.py is the right person to write them, and they are the four
things I will ask about first.

## Before our next meeting

> Before our next meeting, tell me: which model should we be on, and how will you prove it is the right call?
>
> Priya Raghavan, Larkspur Airlines

Bring two things. A recommendation, and the measurement behind it. If the model is
not the problem, say so, and bring the number that shows it.

## What this review read

- `agent.py (689 lines)`
- `PITCH.md`
- `TEAM.md`
- `readout-trace.json`
- `readout.html (evidence block)`
- given files that differ from the shipped pack: `verify.py`, `eval_harness.py`, `bench.py`, `readout.py`, `pod_sync.py`, `run.py`, `support/__init__.py`, `support/client.py`, `support/data.py`, `support/tools.py`, `support/trace.py`, `support/offline.py`, `support/mock_backend.py`

Reviewer: `claude-sonnet-5-5`. Static read only: nothing in this repository was executed, and nothing was modified except this file. Larkspur Airlines is a fictional training scenario. Confidential, do not distribute.
