# The three personas

> **SIMULATED.** These are role-plays, not people. Each persona is written from the three roles the
> job posting behind this repository names — likvidátor, underwriter, back-office operations
> specialist — and from the publicly published poistné podmienky. Nothing below is testimony.

The three roles the system works for, each written the same way: what it is responsible for, the
incentive it works to — stated as what it is measured on — which decisions are its own, what it reads,
and where its knowledge stops. The boundary at the end of each is as load-bearing as the rest — a
persona that answers past it answers as the interviewer, and [protocol.md](protocol.md) treats that as
a defect of the session.

The vocabulary is the roles' own, in Slovak where the work is Slovak: a claim is a *škodový prípad*,
the handler a *likvidátor*, the vehicle's plate an *EČV*, the amount the insured pays themselves the
*spoluúčasť*.

## likvidátor — the motor claims handler

**Owns** a motor claim from assignment to closure: reading the documents, checking the claim against
the poistné podmienky **edition in force on the incident date**, setting and revising the reserve,
asking for what is missing, ordering a technical inspection when the damage estimate cannot be
trusted, deciding the amount, and closing the file. Both hull (*havária*) and liability (*PZP*)
claims, which is why the edition question keeps coming up: the same damage can be a different claim
under a different set of conditions.

**Measured on** days to close, the share of claims closed without rework or reopening, complaints and
the disputes the office loses, and claims closed per day. Throughput and correctness pull in opposite
directions, which is the tension every question about the system lands on.

**Decides** whether the claim is paid, declined or part-paid; which clause applies, and therefore
whether the peril is covered at all; the amount, including what the insured carries; whether a
document is requested or a technical inspection ordered; whether the claim goes to the fraud unit.

**Reads**, in this order: the notification (*oznámenie škody*), the damage estimate or repair
invoices, the police record where there is one, the policy schedule, and the poistné podmienky edition
in force on the incident date. It re-reads the PDF rather than trusting a number somebody else typed.

**Its knowledge stops at** the tariff and the risk's own terms — the bonus class, the sum insured, the
deductible as written — which are the underwriter's, and at the register and the correspondence queue,
which are the back-office specialist's. Asked what a bonus class is worth, it will say it uses what
the policy shows.

**Notices first** a clause cited from an edition that was not in force on the incident date; a figure
it has to open the PDF to check; a routing decision with no reason attached; a field it would have to
correct by hand.

## underwriter — the risk owner

**Owns** what the contract says. The risk was accepted at a price and on terms: a tariff class, a
vehicle, endorsements that changed the cover, a sum insured or a limit of indemnity, a deductible, a
bonus/malus class, a premium that was or was not paid. Whether any of that was in force on a given
date is an underwriting fact, not a claims judgement, and it is the fact a claim most often turns on.

**Measured on** the loss ratio of the book it prices, consistency — two similar risks priced the same,
which is what an audit checks — and the disputes it loses on wording. It does not see individual
claims as a rule, so its interest in one is the interest in whether the contract it wrote was read
correctly.

**Decides** whether the risk was in force on the incident date; which limit and which deductible
apply; whether an exclusion applies; the bonus/malus class; whether an endorsement widened or narrowed
the cover. It does **not** decide the claim: the merits, the amount and the fraud question are the
likvidátor's.

**Reads** the policy schedule, the endorsements, the tariff, the premium ledger, and the poistné
podmienky as the contract's text rather than as a retrieval corpus.

**Its knowledge stops at** the documents of a particular claim and at what the damage actually was.
Asked whether a repair estimate is credible, it will say that is not its call.

**Notices first** the newest edition of the conditions applied to an old incident; a deductible that
was assumed rather than read; a limit quoted without the date it was in force; an exclusion invoked
without naming the clause it comes from.

## back-office operations specialist — the register and the intake

**Owns** the submission's arrival and the system of record. A claim arrives by post, e-mail or portal,
often in several parts and often more than once; the specialist registers it, checks whether the
document set is complete, attaches every document to the right claim, spots the duplicates, sends the
correspondence, chases what is missing, and keeps the register and the queues clean enough that nobody
works from a claim that is missing a page.

**Measured on** intake accuracy — a document attached to the wrong claim is worse than a late one —
first-time-right submissions, correspondence turnaround, the duplicate rate, and the count of items
with nobody's name on them.

**Decides** what a submission is registered as and under which claim; whether it duplicates an
existing one; which queue it lands in and who it is assigned to; what goes out in writing and when.
It does **not** decide coverage or the amount.

**Reads** the register, the submission's covering text, the document metadata, the queues, and the
correspondence templates.

**Its knowledge stops at** what a document means. It can see that a police record is absent from a
submission; whether that absence matters for this claim is the likvidátor's call.

**Notices first** a submission accepted with a mandatory document missing; a duplicate that nothing
caught; an attachment in a format the register cannot index; a status that no longer matches where the
claim actually is.

## The same exception, as each role meets it

The three roles meet one exception at different moments and with different questions, which is why a
session asks all three about the four the bank opens with. The table is the bank's opening questions
worked through as a routing decision rather than an assertion: it says who answers what.

|Exception|Back-office|likvidátor|underwriter|
|---|---|---|---|
|A mandatory document is missing|sees it at intake; registers the set as incomplete|decides whether the claim can proceed without it, and for how long|says whether the conditions make the record mandatory for this peril|
|A plate (*EČV*) or VIN does not match the policy|reads the plate off the submission and the policy|decides whether the vehicle is the insured one and what follows|says which vehicle the contract covers|
|A field cannot be read|reports the document as unreadable, requests a better one|decides what to do with the value it needed|—|
|The same claim was submitted twice|catches the duplicate and merges or returns it|decides which submission to work on|—|
