# The discovery protocol

> **SIMULATED.** Every persona in this directory is a role-play written for this repository, not a
> real stakeholder. The sessions run under this protocol are staged, and every artefact one produces
> carries this stamp. The repository is unaffiliated with any insurer, and nothing here is a record of
> a real interview.

How a working session is run, what each step of one produces, and what happens to the critique at the
end. The material a session is run with sits beside this file: [personas.md](personas.md) (the three
roles), [interview-guide.md](interview-guide.md) (the arc of the conversation),
[process-walkthrough.md](process-walkthrough.md) (today's work, written down),
[question-bank.md](question-bank.md) (the exceptions and the workarounds), and
[session-record.md](session-record.md) (what a finished session looks like).

## Why a session exists

The unit of work is a **session**: one persona, one thing in front of it, and the persona saying what
that thing gets wrong. Two artefacts are the point of it — the **critique** (what the persona would
change, and the reason it gives) and the **specification delta** (the change that critique makes to a
named document, and the ticket that change becomes). A session without a critique has not happened; a
critique item with neither a delta nor a stated wontfix has been dropped rather than decided.

A session can run before any of the system exists. The thing in front of the persona is then a
specification, a schema, a walkthrough on paper or a prototype, and the walkthrough of today's work is
what the session has instead of a demo. The first recorded session (v0.3, ticket 23) critiques a
running increment. The protocol is committed now, with nothing built to demo, because that is when the
next increment's requirements are still cheap to change — and because the sessions are themselves an
artefact the process was specified to produce.

## The shape of a session

Six steps, in order. Each ends on the artefact it produces.

1. **Frame it.** Name the persona, the date, the increment and **the version being critiqued** — a
   tag, a commit, or "the specification, nothing built". Name what is in scope and what is out of
   scope for this session, so a persona is never asked about a decision it does not own.
   *Ends when:* the record's header carries all four fields — persona, date, increment, version — and
   the two scope lines.

2. **Walk today's work.** The persona says how the work is actually done now: the journey of one
   claim, who touches it, where it waits, which systems it is re-typed between. The interviewer fills
   [process-walkthrough.md](process-walkthrough.md) from the persona's own words as they come.
   *Ends when:* the walkthrough's case, steps, handoffs, duplicated values, checks and volumes have an
   entry; its exception and workaround sections are step 3's to fill.

3. **Press on the exceptions and the workarounds.** Work [question-bank.md](question-bank.md) — its
   exceptions, its workarounds, and the tacit rules and authority it asks after — and fill the
   walkthrough's exception and workaround sections from what the persona answers.
   *Ends when:* every exception question has an answer or an explicit "that does not happen here",
   and every workaround named is in the walkthrough.

4. **Show the thing.** Put the specification, the prototype or the run in front of the persona and let
   it react to each part in turn. Each role reads it differently — the likvidátor reads the clause and
   the amount, the underwriter the in-force date and the limit, the back-office specialist the
   attachment and the register — and each reads it in that order.
   *Ends when:* the transcript holds what was shown and the persona's reaction to each part, in the
   persona's own words.

5. **Take the critique.** Ask directly: what does this get wrong, and why does that matter to you?
   *Ends when:* the critique note lists every item with the reason the persona gave, and names the
   part of the thing the item is about.

6. **Translate it.** Turn each critique item into a change to a named document — a specification, a
   schema, a ticket, this protocol — and then into either a ticket in the next increment or an
   explicit **wontfix** with its reason.
   *Ends when:* every item in the critique note has a delta and a destination, and nothing is left as
   "noted".

## The record

One file per session, `docs/discovery/sessions/NN-<persona>.md`; the directory is created by the
first session. [session-record.md](session-record.md) is the template and the field-by-field
description of it. A record that leaves a section empty says why in that section rather than deleting
it, because which section is empty is itself a finding about the session.

## Rules of recording

- **The four header fields.** Persona, date, increment, version critiqued. A record without the
  version cannot be re-read later: a critique of a thing that has since changed is not a critique of
  what stands there now.
- **Persona turns carry the stamp.** The interviewer writes its own turns plainly; every turn the
  persona takes is prefixed `SIMULATED`. The file's header stamp is not enough for a document that
  mixes the two voices, and a reader skimming a transcript should never be able to read a persona's
  turn as testimony.
- **The persona answers inside its role.** A question the persona does not own is answered "that is
  not mine — ask the underwriter" and recorded that way. A persona that opines past its role is the
  failure this file exists to prevent.
- **The persona speaks from its role's knowledge.** It knows the work, the rules as it was taught
  them, the poistné podmienky as documents, and the systems it uses. It does not know the codebase,
  this repository's tickets, or what a later increment plans — a persona reasoning from the design is
  the interviewer talking to itself.
- **Quotes are the persona's words**, tidied for grammar and for nothing else.
- **Open items stay open.** Where the persona cannot decide — whether a real likvidátor would accept a
  threshold, what a real register actually holds — the item is written down as open for a real
  stakeholder rather than resolved by invention.
- **The session may leave the script.** A persona that names something the guide and the bank do not
  ask about is followed, because that is where the unmodelled work is. The guide is the floor of the
  conversation, not its ceiling.

## What this is not

- **Not real stakeholder engagement.** The personas are synthetic, grounded in the three roles named
  in the public job posting this repository answers and in the publicly published poistné podmienky.
  Every file they touch is stamped `SIMULATED`.
- **Not a substitute for the session it models.** The residual risk is stated rather than denied: a
  real likvidátor will raise exceptions these personas do not. The question bank is therefore a floor
  and never a closed list, and each session's open items are the queue handed to the first real
  session.
