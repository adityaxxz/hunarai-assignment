# Attendance for 1,000 people across 100 locations, without apps

*Assignment item 3.*


## The position I am answering from

The question puts me in the HR seat rather than the vendor seat, so I have written this
as the person who owns the problem and has to live with whatever gets built. That
changes what matters. I care less about the elegance of the system and more about
whether it still works in month six, whether my regional managers trust it enough to act
on it, and whether it creates arguments with the workforce that I then have to resolve.

Everything below is what I would put in place, in what order, and what I would expect to
land on my desk each morning once it is running.

## The assumptions I am working from

The question leaves a fair amount open, so before designing anything I had to decide
what "no smartphones, but everything else exists except apps" actually means. Here is
the reading I went with.

Feature phones exist and most workers have one, which means voice calls and SMS both
work, but there is no GPS, no camera-based check-in and nothing installed on the
handset. The public telephone network exists, so I can make outbound calls, run a
toll-free inbound number, read caller ID, and use landlines where sites have them.
Server-side software exists too. I am reading "no apps" as removing the mobile app
layer rather than the internet as a whole, so my team still has a web dashboard to look
at. Finally, I am assuming a minority of workers have no phone at all, because in any
Indian frontline workforce that is true and a design that quietly ignores those people
falls apart on the first site visit.

If the stricter reading is intended, where there is no dashboard either and I have no
screen at all, then the reporting surface changes to a daily voice digest and a printed
exception sheet for each region. The collection design I describe below stays the same.
Only the way the results are delivered would need reworking.

There is one further assumption that carries more weight than any of the others, and I
want to flag it explicitly rather than bury it: **I am assuming every location has one
person who could reliably tell me who turned up today.** The whole design leans on that.
If a customer's locations genuinely have no such person, whether because the sites are
unstaffed by anyone senior or because the work is solitary, then the cheap half of this
design does not apply and I would fall back to individual verification calls for those
sites, at roughly ten times the call volume. This is the first thing I would check in
discovery, before committing to anything else, because it is the assumption most likely
to be wrong and the most expensive one to discover late.

## The operation I designed against

Rather than answer this in the abstract, I picked a concrete operation, because generic
attendance designs tend to fall over as soon as they meet a real shift pattern.

The operation is a diagnostic lab network. There are 100 collection centres spread
across a state, and roughly 1,000 staff working in them: phlebotomists, front-desk
coordinators, and riders who move samples to the processing lab. There are two shifts,
and the first one opens at 06:30 because patients come in fasting before work. Each
centre has a senior staff member who acts as the supervisor, and that person is the one
who would know, without having to check anything, who has turned up today.

Attendance matters here for payroll, which is the obvious reason, but it matters more
urgently for service delivery. If a centre opens without a phlebotomist, it turns
patients away within the hour, and those patients generally do not come back. That gives
the whole system a hard time constraint: knowing about an absence at 06:45 is useful,
and knowing about it at the end of the month is not.

The specific details here are invented for the purpose of the exercise, but the shape of
the problem is one that a distributed Indian services business would recognise.

## The core decision: collect the fact where it already exists

The obvious design is to have an outbound voice agent call every worker at the start of
every shift and ask whether they are at work. That design does work, and I rejected it
because it costs roughly ten times what it needs to and it puts an unnecessary phone
call into a thousand people's mornings every single day.

The reasoning is that attendance is not really 1,000 independent facts that have to be
discovered one at a time. It is 100 local facts, and each of them is already known with
complete certainty by someone who is physically standing in the room. The job of the
system is to collect that knowledge cheaply and reliably, not to reconstruct it from
scratch a thousand times a day.

That leads to a design with two collection signals, where the cheap one carries almost
all of the load and the expensive one only handles what the cheap one missed.

### Signal one: a missed call from the worker, at no cost to them

Every worker is registered in the system against their handset number. When they arrive
at the centre, they give a missed call to a toll-free number. The system reads the
caller ID, rejects the call before it connects, and records the person as present. The
worker is never charged, never speaks to anyone, and the whole interaction takes about
three seconds. It works on the cheapest handset available and over a 2G signal.

I chose this specifically because it is the lowest-friction action a feature phone
allows. Anything that requires a worker to navigate a menu, remember a numeric code, or
stay on the line for thirty seconds will see compliance decay within a couple of weeks.
That decay is worse than having no system at all, because a system with 60 percent
compliance still produces a confident-looking report, and nobody downstream knows which
40 percent to distrust.

### Signal two: a voice agent that calls the supervisor about the gap

Fifteen minutes after the shift starts, an outbound voice agent calls each centre's
supervisor. Importantly, it does not read out the whole roster. It only asks about the
people who have not checked in yet.

> "Good morning. Three people at Kothrud have not checked in: Anjali, Faizan and
> Suresh. Can you tell me who is here?"

On an ordinary morning that conversation takes about forty seconds. The supervisor's
answers are extracted into structured records of the form `{worker_id, present, source}`
rather than being stored as a transcript that somebody would then have to read and
interpret.

### Workers who have no phone

This group needs no special handling, which is one of the reasons I like this shape.
They never check in, so they always appear in the gap, and the supervisor confirms them
on the call by default. Where a centre has a landline, they can also check in from it,
with the site identified by the number and the person identified by saying their name.

## Why this needs an LLM, and not the IVR we could have built in 1998

The question specifies that LLMs exist, so it is worth being precise about where they
earn their place, because most of this design would work with a touch-tone menu.

The happy path genuinely does not need one. "Press 1 if everyone is here" would handle
the majority of mornings at most centres, and I would not argue for a language model on
the strength of the happy path alone.

The reason I want one is that the exceptions are the entire problem. The cases that make
attendance data unreliable today are not the mornings when ten out of ten people turned
up. They are the mornings when somebody swapped a shift last week and told nobody, or a
new joiner started this morning and is not on the roster yet, or two of the three
missing people are on approved leave that HR recorded and the site never saw, or the
supervisor is covering two centres today and wants to answer for both. A keypad menu
cannot take any of those answers. It forces the supervisor to pick the closest wrong
option, and the system records something confidently incorrect.

What a conversational agent gives me is the ability to accept an answer the system did
not anticipate and still produce a clean structured record from it. The supervisor says
"Faizan swapped with Suresh on Tuesday, so Suresh is here and Faizan is off", and the
extraction returns two records with the right people marked correctly. Nobody had to
teach the supervisor how the roster is modelled internally. That is the whole value, and
it is worth saying plainly that the value is in turning messy human speech into clean
data, not in the agent sounding lifelike.

The second place it earns its keep is language. Across 100 centres in one state I will
have supervisors who are comfortable in three or four different languages, and some who
mix two in a sentence. A menu would need building and maintaining per language. An agent
handles it as configuration.

The third is the verification pass. Having a second model review a completed call and
extract exactly the fields the payroll system needs, rather than trusting a first-pass
transcription, is what makes me willing to let this feed payroll at all. Without that
check I would keep a human in the loop for every record, and the cost advantage
disappears.

## Why calling only about the exceptions matters

The volume difference between the three possible approaches is substantial:

| Approach | Calls per day | Typical duration |
|---|---|---|
| Call every worker, both shifts | around 2,000 | 30 to 45 seconds |
| Call every supervisor, read the full roster | around 200 | around 3 minutes |
| Missed call, plus a supervisor call about the gap | around 200 | under 1 minute |

The saving that actually matters is duration rather than call count. If check-in
compliance sits at 90 percent, then on a typical morning a supervisor has two or three
names to confirm rather than ten, and the call drops from around three minutes to under
one. Across 200 calls a day that is the difference between roughly ten hours of
connected telephony time and under three.

I have deliberately not put a rupee figure on any of this. Telephony is priced per
contract, I do not have our rate card in front of me, and a number invented here would
look more authoritative than it has any right to. The ratios hold at whatever the rate
turns out to be, and getting the actual rate is a five-minute conversation with whoever
owns the telecom contract.

## Attendance systems get gamed, so verification has to be designed in

Any system built on self-reporting and supervisor confirmation has an obvious weak
point. A supervisor might cover for a friend who is running late, or a worker might hand
their phone to a colleague and ask them to make the missed call. Without GPS or
biometrics there is no way to make either of these impossible, so the realistic goal is
to make them detectable often enough that they stop being worth doing.

The first control is caller ID binding. A missed call only counts if it comes from the
handset registered to that worker, and changing the registered number requires an HR
action rather than being something the worker can do themselves.

The second is a random verification sample. Each day, roughly 5 percent of the people
who checked in by missed call receive a short outbound call that confirms who they are
and which centre they are working at today. This is what makes the borrowed-phone
approach unattractive, because the colleague holding the phone cannot answer on behalf
of somebody who did not come in. Five percent is low enough to be cheap and frequent
enough that a worker cannot assume they will not be called.

The third is looking at the data in aggregate. A centre reporting 100 percent attendance
for six consecutive weeks while comparable centres run at 92 percent is not necessarily
well run, and it is worth someone visiting. This is a signal to investigate rather than a
conclusion.

The most important rule across all three is that none of them should ever trigger an
automatic consequence. Every fraud signal produces a review item for a regional manager,
and never a payroll deduction. If I get this wrong, my attendance system turns into an
industrial relations dispute, and that is a far more expensive problem for me than the
small amount of fraud it was trying to prevent.

## Failure modes and how each one is handled

| Failure | How the system responds |
|---|---|
| Worker's phone is off or out of credit | They fall into the supervisor gap and get confirmed on the call |
| Supervisor does not answer | Two retries at intervals, then escalation to the regional manager's queue |
| Supervisor is themselves absent | A fallback contact configured per centre in advance, rather than improvised on the day |
| Network outage at a centre | The centre is marked unresolved, not absent |
| Worker changes handset | Shows up as the same person repeatedly in the gap, routed to HR to re-register the number |
| Agent mishears a name | Read-back confirmation on ambiguous names, and the supervisor call remains reviewable |
| Telephony provider outage | Falls back to a supervisor SMS with a structured reply format. Degraded, but still functional |

The distinction between "absent" and "unresolved" runs through the entire design, and I
think it is the single most important modelling decision in it. If the system reports
somebody as absent when the truth is that it could not reach anyone at that centre, then
my managers will start overriding it within a week. Once they are in the habit of
overriding it, they stop reading it, and at that point the system has failed regardless
of how accurate it is on the days when everything works.

## What actually lands on my desk each morning

This is the part I care about most as the person running it, because a system that
produces a thousand rows a day and expects someone to read them has not saved anybody
any work.

By around 07:15 I want one screen with three things on it. First, the centres that are
short-staffed right now, ranked by how badly, because that is the only thing on the page
that needs action within the hour. Second, the unresolved centres, meaning the ones where
neither signal reached anybody, since those are not attendance problems but system
problems and they need a different response. Third, a small queue of exceptions that need
a human decision: a new joiner not on the roster, a worker whose number appears to have
changed, a verification call that did not match what the supervisor said.

Everything else, which on a normal day is 95 percent of the workforce, does not appear
on the screen at all. It goes straight into the attendance record. The design principle
is that the system should only ever ask me about things it could not resolve on its own,
and the number of those should fall week on week as the roster data gets cleaner.

Once a month I want one more view, which is the override report: where were people
overriding the system, and which centres or managers account for most of it. That is how
I find out whether the design is wrong before somebody tells me in a meeting.

## Alternatives I considered and why I did not choose them

Biometric devices at every centre are accurate and hard to tamper with, and for a factory
with a single entrance they would be the right answer. Across 100 small distributed sites
the picture changes. There is a capital cost for every location, hardware that breaks and
takes days to replace when it does, a dependency on connectivity that these centres may
not reliably have, and a device that becomes useless the moment someone covers a shift at
a different centre. I rejected this on distribution rather than on accuracy.

A paper register with data entry afterwards is what most operations of this kind actually
do today. It fails on three counts. Payroll finds out about problems at the end of the
month, transcription introduces errors nobody catches, and a register is trivially filled
in on Friday afternoon from memory, which means the data is often fiction even when
nobody intended to deceive anyone.

SMS or USSD check-in is cheaper than voice and I gave it serious consideration. I rejected
it as the primary channel because it assumes literacy in a particular language and script,
it provides no evidence that the registered person was the one who sent the message, and
it cannot handle the follow-up cases that make attendance messy in practice. I kept it as
the degraded fallback for a telephony outage, where something imperfect is much better
than nothing.

It is worth being clear about where this design loses. If the operation were a single site
with one entrance and reliable power, a turnstile would beat all of this comfortably. The
voice-based approach earns its place specifically because the workforce is distributed
across a hundred locations, and that distribution is what makes hardware-based solutions
expensive and fragile.

## Compliance and the worker's experience of the system

Attendance data is employment data and it feeds payroll, so the standard here is not
casual. Under the DPDP Act the obligations that matter most are notice and purpose
limitation, meaning workers are told at onboarding what is being collected and why;
retention, meaning call recordings are kept for a defined window and then deleted, with
the attendance record itself retained separately according to statutory requirements; and
access, meaning a worker can find out what was recorded about their own attendance and
contest it if it is wrong.

Two design choices follow directly from that. Verification calls open by stating their
purpose in the first sentence, before asking anything, so nobody is answering questions
without knowing why. And the missed-call channel is deliberately built to collect a number
and a timestamp and nothing else, because that is the minimum needed to answer the
question being asked.

## How I would roll this out

In the first week I would run five centres only, and I would choose them deliberately to
include one with poor connectivity and one whose supervisor thinks the whole idea is a
waste of time. The system runs in shadow mode alongside the existing register, and the two
are compared every day. The point of this week is not to collect attendance data. It is to
produce a list of all the ways the real operation differs from the model I built: shift
swaps nobody documents, a centre where two people share one phone, a supervisor who is
genuinely never free at 06:45 because that is when the first patients arrive.

Over the following three weeks I would fix what the pilot exposed and expand to 25
centres, still with payroll integration switched off. The metric I would watch most
closely is supervisor call completion rate, because that is the leading indicator of
whether the design holds up under real conditions. If supervisors stop answering,
everything downstream degrades quietly.

By month three the system would be live across all 100 centres with payroll integration
connected, an exception queue with a response SLA, and a monthly review of override
patterns. That last part matters. If one centre or one manager is overriding the system
constantly, the review has to be capable of concluding that the design is wrong rather
than that the manager is difficult.

## How this maps onto Hunar's platform

I deliberately designed the above without reference to any particular vendor, because the
design should hold regardless of who supplies the telephony. That said, most of the
primitives it needs are things Hunar already runs.

Outbound agents that produce structured extraction against a defined result schema are
exactly what the supervisor roll-call needs, with the roster passed in as call variables
and the attendance coming back as data rather than as text somebody has to read.
Multilingual agents matter more in this use case than they do in hiring, for the reason
given above. Retry policies and calling guardrails cover the supervisor who does not
answer the first time. And the verification pass that reviews a completed call before
handing its data downstream is the same mechanism the random sampling relies on.

What is not there and would need building is the inbound missed-call number with caller-ID
recognition, the roster and exception-queue data model, the outlier detection, and the
payroll export. That is the forward-deployed half of the work, and in my view it is the
half that determines whether the deployment survives contact with 100 centres that each do
things slightly differently.
