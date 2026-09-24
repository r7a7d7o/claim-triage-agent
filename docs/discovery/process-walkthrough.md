# The process walkthrough

> **SIMULATED.** This file is the template a persona's process is written into. The illustration at
> the end is written to show the shape of an entry, not to record a session; a session's walkthrough
> lives in its record.

Fill one per session, from the persona's own words, while the persona talks — the walkthrough of the
last case it closed, not of the process as it is supposed to work. Copy the sections into the
session's record under [session-record.md](session-record.md); the table columns and the section order
are what make two sessions comparable later, which is the only reason this shape is fixed.

**Ends when** every field below has an entry and every wait, handoff and duplicated value is named.
An empty field is written down as "nobody could say" rather than left blank, because a gap the persona
could not fill is a finding about the work.

## The case being walked

- **Claim walked:** the identifying reference of the last real claim the persona worked, not a
  hypothetical.
- **What it was:** hull (*havária*) or liability (*PZP*); single vehicle, two vehicles, theft, other.
- **How it started:** the channel the submission arrived on, and what arrived with it.
- **How it ended:** paid, declined, part-paid, withdrawn, still open.

## The steps

One row per step, in the order the case took them. Actors are roles, never names. "In" is the system or
the artefact the step happens in — including "by hand", "on the phone", "in e-mail", which are systems
in this table like any other. "Waits until" is what the actor is waiting for before the next step can
start; where there is no wait, write "nothing". "Takes" is how long the step itself costs.

|#|Actor|Does what|In|Produces|Waits until|Takes|
|---|---|---|---|---|---|---|
|1| | | | | | |

## The handoffs

|From|To|Travels|Arrives as|Is lost|
|---|---|---|---|---|
| | | | | |

**Is lost** is the column that matters: what the first actor knew and the second cannot see from what
arrived — the phone call, the reason for the estimate, the document that was seen and not attached.

## Where the same value is written twice

|Value|Written in|By|Trusted from|
|---|---|---|---|
| | | | |

A value in this table twice is a value two systems can disagree about, and the "trusted from" column is
the answer the persona gives when asked which one wins — which is not always the same answer.

## Exceptions met on this walk

The four the bank opens with, plus any this case met. What the actor did is the entry; whether that is
what *should* happen is not asked here and is left for the critique.

|Exception|Met on this case|What the actor did|
|---|---|---|
|A mandatory document is missing| | |
|A plate (*EČV*) or VIN does not match the policy| | |
|A field cannot be read| | |
|The same claim was submitted twice| | |

## Workarounds on this walk

One entry per workaround the bank's workaround questions elicited, in the actor's words: what it is,
where it lives, who else knows it, and what breaks when whoever holds it is away.

## The check before moving on

What the actor verifies before letting the claim leave their hands — the tacit quality gate. It is
usually the same three or four questions, and it is usually the part of the work an automated pipeline
has no place for.

## Volumes

Claims the role handles in a day, the week's shape (which day is the heavy one), the seasonal peak,
and how the queue is measured.

## What the persona could not answer

Questions it did not own, values it did not have, and anything it said it would have to check with
somebody else — each written down as an open item for a real stakeholder.

## An illustration, not session material

One row of the steps table and one handoff, written to show the shape and nothing else:

|#|Actor|Does what|In|Produces|Waits until|Takes|
|---|---|---|---|---|---|---|
|3|likvidátor|reads the incident date off the notification and looks up which poistné podmienky edition was in force that day|the conditions, by edition|the edition the rest of the claim is checked against|nothing|2 minutes|

| From | To | Travels | Arrives as | Is lost |
|---|---|---|---|---|
| back-office specialist | likvidátor | the submission as registered, with its documents attached | the claim in the queue, with the attachment names as the register holds them | the covering e-mail said the driver had already admitted fault; nothing in the register says it |
