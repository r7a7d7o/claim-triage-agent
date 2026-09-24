# The session record

> **SIMULATED.** A record written into this template documents a staged session with a role-play
> persona, not a real stakeholder interview. Every part of it carries the stamp.

One file per session, at `docs/discovery/sessions/NN-<persona>.md`, numbering sessions in the order
they were run; the directory is created by the first one. The record is the session's only durable
artefact — the walkthrough, the transcript, the critique and the delta all live in it, because a
critique read apart from the walkthrough it came out of cannot be judged.

## The template

```markdown
# Session NN — <persona>, on <increment>

> **SIMULATED.** A staged session with a role-play persona. Not a real interview.

- Persona: <likvidátor | underwriter | back-office operations specialist>
- Date: <YYYY-MM-DD>
- Increment: <v0.X `name`>
- Version critiqued: <tag, commit, or "the specification, nothing built">
- In scope: <what this session was asked about>
- Out of scope: <what it was not, and which role owns it>

## What the persona was shown

## The process as the persona works it

## Transcript

## Critique

## Specification delta

## Tickets

## Open items
```

## What each part is for

|Part|Holds|Empty means|
|---|---|---|
|Header|the four fields every later reading depends on: persona, date, increment, version critiqued, plus the session's scope|the session is not a critique of anything in particular, and cannot be re-read|
|What the persona was shown|the specification, prototype or run put in front of it, and in what state|there was nothing to critique, so step 4 did not happen|
|The process as the persona works it|the filled [process-walkthrough.md](process-walkthrough.md) for this session|the critique has no ground under it|
|Transcript|the conversation, in order: the interviewer's turns plainly, the persona's marked `SIMULATED`|the critique cannot be checked against what produced it|
|Critique|each item, with the reason the persona gave for it and the part of the thing it is about|the session produced nothing, and that is recorded here|
|Specification delta|each critique item as a change to a named document — the specification, a schema, a ticket, [PROTOCOL.md](PROTOCOL.md)|critique items have been dropped rather than decided|
|Tickets|for each delta, the ticket in the next increment that carries it, or a **wontfix** with its reason|the delta will not be built and nothing says so|
|Open items|what the persona could not decide and what a real stakeholder has to settle|the session's unknowns are lost|

## The transcript's shape

The interviewer's turns are written plainly; the persona's carry the stamp, every turn, for as long as
the transcript runs:

```markdown
**Interviewer:** Which document do you read first?

**likvidátor (SIMULATED):** The notification, always — it is the only one the customer wrote
themselves. The estimate I read afterwards, and only as far as the parts list.
```

Anything the persona could not answer is written down as it said it — "that is not mine, ask the
underwriter" — rather than filled in by the interviewer, because the boundary of a role is one of the
things a session is for finding.

## A finished record

`docs/discovery/sessions/` is empty until the first session runs (v0.3, ticket 23). The session it
holds then is a critique of the triage increment, run under [PROTOCOL.md](PROTOCOL.md), and the
protocol's own rules — the four header fields, the stamped turns, a delta and a destination per
critique item — are what it is checked against.
