# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A support agent for disrupted Larkspur flights, with Larkspur's nine tools, a departures_in_window tool written by us, our own shorter check_policy in place of the given one, and fare_rules over MCP.
Does: It finds the booking, checks the flight's real status and cause, tells the customer which policy applies and what Larkspur covers, and hands a case it should not handle, like a group booking, to a person.
Number: Model cost per customer went from $0.0654 to $0.0223, averaged over 15 runs (our five tickets, three times each), with all 15 resolved both times.
Safety check: It never changes a group booking itself. It passes it to the Group Desk instead. We saw this in all three bench runs of G2HL9V.
Next: Teach the agent to notice an angry or abusive customer and answer calmly, or pass them to a person, instead of replying as if nothing happened.
Still broken: When the customer in R8KD3F is abusive, the agent replies with the same normal answer it gives everyone else. It does nothing about the tone, and it addresses the customer as "Mr./Ms." because it does not know which to use.
Lever: <cost | speed | intelligence>

## Priya asked

Costs:
Wrong:
Runs it:
Left out:
