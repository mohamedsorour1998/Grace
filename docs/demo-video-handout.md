# Demo video handout — Grace

Everything needed to record the ≤5-minute submission video: the shot list, what to say, and the
commands to have ready. **Nothing in the script below is a claim this repository cannot back with
evidence** — each figure is annotated with where it came from, so if a number has drifted by the time
you record, re-measure it rather than reading this file aloud.

- **Hard limit: 5:00.** The script below runs ~4:30 spoken at a normal pace, leaving margin.
- **Required content:** a demonstration of the working project, plus a pitch covering
  (1) the problem, (2) who it is for, (3) why it matters.
- **Upload:** YouTube or Vimeo, **public** (not "unlisted" — the rules say public).
- Slides, screen recording, and voiceover are all fine. **You do not need to appear on camera.**

---

## Before you hit record

```bash
# 1. The dashboard needs a live session. Sign in first, in the browser you will record.
open https://grace.rosettacloud.app
#    caseworker-01  /  (the seeded password — see docs/dashboard-runbook.md)

# 2. Confirm the demo's headline claim is still true, so you are not narrating a stale number.
.venv/bin/python - <<'PY'
import boto3, json
d = boto3.client("dynamodb", region_name="us-east-1")
rows, key = [], None
while True:
    kw = {"TableName": "grace-cases"}
    if key: kw["ExclusiveStartKey"] = key
    r = d.scan(**kw); rows += r["Items"]; key = r.get("LastEvaluatedKey")
    if not key: break
filed = sorted({i["case_id"]["S"] for i in rows if i.get("kind",{}).get("S") == "renewal_submitted"})
esc   = sorted({i["case_id"]["S"] for i in rows if i["sk"]["S"].startswith("ESCALATION#")})
print(f"filed: {filed}")
print(f"escalated: {esc}")
print(f"9 filed / 3 escalated holds: {len(filed) == 9 and esc == ['c-010','c-011','c-012']}")
print(f"no escalating case was filed: {not (set(esc) & set(filed))}")
PY
```

**Any of the three escalated households can be approved on camera.** `c-010` has been approved twice
before, during verification — but a decision answers *one escalation*, not the case forever, and every
sweep since has raised a fresh one. So it is decidable again, and `c-010` is the one to use: it is the
household missing `proof_of_residency`, so Grace's refusal to file is guaranteed and you can say what
will happen before you click. `c-011` and `c-012` turn on judgement, and a filing there would be a
legitimate outcome — honest, but a weaker thing to narrate live.

Do not stage a fake approval to make the demo cleaner. The whole entry rests on claims being real.

**What the headline does when you approve, so it does not surprise you on camera.** `/` reads
`9 handled alone, 3 waiting on you.` before you decide anything. The moment you approve one, it becomes
`9 handled alone, 2 waiting on you, 1 you've answered.` and `/queue` drops to two households — the two
pages move together, by construction. That is worth narrating rather than hiding: *"the case leaves my
queue because I answered it, not because Grace filed anything — and tomorrow's sweep will raise it
again, because the document is still missing."*

If you want the untouched `9 / 3` headline back for a retake, run one sweep — it re-escalates whatever
was answered and the headline returns byte for byte:

```bash
aws stepfunctions start-execution --region us-east-1 \
  --state-machine-arn arn:aws:states:us-east-1:339712964409:stateMachine:grace-sweep \
  --input '{"today":"2026-10-01"}'
```

It takes about a minute and files nothing new — the nine clean households are already filed for this
period, so Grace records `renewal_already_filed` for each and files none of them again.

---

## Shot list

### 0:00–0:45 · The problem, and who it is for

*No screen needed — a title slide is fine. **Put the numbers on the slide**; they carry this section.*

> When pandemic-era continuous coverage ended in March 2023, states resumed annual Medicaid eligibility
> checks. Enrolment had reached a record **94 million**. Over the unwinding that followed, **more than
> 25 million Americans lost Medicaid coverage.**
>
> **About 69% of those losses were procedural.** Missing forms. Missed deadlines. One document that
> never arrived. Not people who stopped qualifying — people who still qualified and lost coverage
> anyway.
>
> That is roughly **17 million Americans** who lost health insurance they were entitled to, because of
> paperwork.
>
> This is for the **caseworker** holding hundreds of those files at once, and for the **family** who
> finds out at a pharmacy counter when a prescription is refused.
>
> It matters because the failure is silent. Nobody gets an error message. A renewal simply does not
> happen.

`<replace this text by a screenshot of the title slide — "Grace" with the three figures: 94M enrolled, 25M+ lost coverage, 69% procedural>`

**Sources, if anyone asks.** 94M peak enrolment and 25M+ losses:
[Medicaid.gov unwinding resources](https://www.medicaid.gov/resources-for-states/coronavirus-disease-2019-covid-19/archived-unwinding-and-returning-regular-operations-after-covid-19)
and [KFF](https://www.kff.org/medicaid/10-things-to-know-about-the-unwinding-of-the-medicaid-continuous-enrollment-provision/).
The 69% procedural share:
[JAMA Health Forum](https://jamanetwork.com/journals/jama-health-forum/fullarticle/2825467).
The ~17 million figure is 69% of 25 million — say "roughly" or "about", never a precise count.

### 0:45–1:15 · What Grace does

> Grace watches every household's renewal clock. It files the renewals that are unambiguous, chases
> the one missing document by text, and wakes a human **only** when eligibility is genuinely in doubt.
>
> On the twelve seeded households it handles nine alone and escalates three — each with a typed reason.

`<replace this text by a screenshot of the architecture diagram from docs/architecture.png>`

### 1:15–2:15 · The dashboard — the sweep and the queue

*Screen recording, live.*

Open **`https://grace.rosettacloud.app`**.

> This is the caseworker's view. The twelve seeded households — **nine handled alone, three waiting on a human.**
> Those numbers are read from the DynamoDB ledger, not from a log line.

`<replace this text by a screenshot of the / page showing "9 handled alone" and "3 waiting on you">`

Click through to **`/queue`**.

> The queue shows only the three that need a person, soonest deadline first. Notice what is *not*
> here: no names, no phone numbers, no addresses. The agent never receives them, so it cannot leak
> them.

`<replace this text by a screenshot of the /queue page showing c-012, c-010, c-011 with their typed reasons>`

### 2:15–3:15 · One household, and the escalation boundary

Open **`/case/c-010`**.

> Here is the whole audit trail for one household — every tool call, every result, in order. Grace
> escalated this one for a specific reason: `missing_document: proof_of_residency is not on file`.
> And it has already texted the family, so the caseworker knows not to ask twice.
>
> Note the documents panel, and note what it is careful to say. Grace holds no documents — the family
> sends those to the state, which is the system of record. What Grace has is a **status a caseworker
> asserted**, and the page says so: *"asserted by"*, an opaque id, a date, and *"Grace does not verify
> it independently."* An agent that quietly treated that as a verified fact is exactly how a family
> ends up told their renewal is fine when it is not.

`<replace this text by a screenshot of the /case/c-010 page showing the typed reason, the documents-sent-to-the-state panel with its provenance line, and the ledger>`

> And look at the message Grace sent this family. They read Spanish, so Grace wrote in Spanish. A
> separate agent drafts it — no tools, no access to the case, it never learns who the family is — and
> `decide` sends exactly what it returned. **The agent that writes the words cannot send them, and the
> agent that can send has no words of its own.**

`<replace this text by a screenshot of the c-010 ledger row showing the Spanish family_message_sent body>`

> This is the part that matters. Grace's defining property is an **escalation boundary** — it acts
> alone on the routine and *provably* escalates the rest. Three layers.
>
> First, **capability absence**: the tool that files a renewal is not in the agent's tool list at all
> for a case that has not passed verification. It cannot do the wrong thing, because the ability does
> not exist. That beats any instruction — there is nothing to disobey.
>
> Second, **identity comes from the session, never the conversation**: every household-scoped tool
> takes zero arguments. A prompt injection cannot point Grace at another family, because there is no
> parameter to poison.
>
> Third, a **deterministic gate** — pure Python, no model, no I/O. And if verification errors, it
> escalates. Fail closed.

`<replace this text by a screenshot of grace/authority.py, or a slide listing the three layers>`

### 3:15–4:10 · The approval — a human's yes is an input, not a bypass

Scroll to the decision form on an **undecided** case (`c-011` or `c-012`).

> A caseworker can approve or deny, with a note. Watch what approving actually does.
>
> It records the decision — against an opaque ID, never a name — and then **re-invokes Grace so the
> gate evaluates the case again from scratch.** It is deliberately not a "resume", because resuming a
> paused agent with any affirmative answer would just approve whatever it was blocked on.

Submit the approval. Then show the outcome the page now displays.

`<replace this text by a screenshot of the decision form filled in, before submitting>`

`<replace this text by a screenshot of the outcome Grace wrote after re-checking — for c-010 this reads "Grace re-checked and did not file. missing_document: proof_of_residency is not on file">`

> When this was done on `c-010` — the household missing a document — Grace re-checked and **filed
> nothing**, because the document is still missing. A human said yes and the gate still said no.
>
> That is the guarantee: a human's approval, or a reflection, can make Grace **more** cautious. It can
> never make it less.

### 4:10–4:40 · It is genuinely deployed, and honest about what does not work

> This runs on AWS on a schedule: EventBridge, Step Functions, Lambda, and AgentCore Runtime, with
> Memory for per-household facts. The last sweeps succeeded reporting nine acted and three escalated,
> and one of them fired on its own schedule rather than being triggered by hand.
>
> The repository is also honest about three things that do not work: CloudWatch trace correlation is
> unavailable because Runtime does not install an in-process tracer, so every ledger row carries a
> null trace ID; SMS is sandboxed, so the family channel writes a transcript instead; and one
> household name reached CloudWatch before it was fixed — fixed at the source, stripped from storage,
> and the historical log events age out with retention.

`<replace this text by a screenshot of the Step Functions execution showing 9 acted / 3 escalated>`

### 4:40–5:00 · Close

> Grace is not a chatbot. It is a backend process that removes a class of harm nobody intended, and it
> hands the genuinely hard calls to a person with the reasoning already assembled.

---

## Figures, and where each comes from

Re-measure before recording; do not read a stale number.

| Claim | Source |
|---|---|
| the twelve seeded households: 9 act, 3 escalate | `evaluate()` over `fixtures/households.yaml` at `today=2026-10-01`. **Say "the twelve seeded households"** — the claim must stay true if anyone submits a case through the intake form. |
| 9 acted / 3 escalated deployed | the `grace-sweep` Step Functions execution output |
| the sweep reads its caseload from the table | `ListCases` queries the `CASE_DIRECTORY` partition; the scheduled event carries only `{"today": "2026-10-01"}`. Started with that input, an execution returns 12 outcomes. **Do not say the schedule names the twelve** — it did until 2026-09-07, and that was the defect. |
| runtime version 8 | `get-agent-runtime --agent-runtime-id grace_grace-oTyyvo8stE`. v2 could not read `RECORD#v1` rows (a submitted household was invisible to the sweep), v4 added run-scoped classification and no-duplicate filing, v5 stopped an approval re-escalating itself, v6 added the outreach drafter, v7 fixed the model prefix the deployed policy actually grants, v8 added reflection. |
| `renewal_submitted` for exactly `c-001`–`c-009` | a full DynamoDB scan; the invariant, not the row count |
| 945 Python tests, 232 vitest across 11 files | `pytest` and `vitest run` — re-measure, these move every plan |
| the outreach is in the family's language | `c-010` reads Spanish and the `family_message_sent` body in its ledger is Spanish. **Say "in the family's own language" and then show it** — this is the one place that claim is checkable rather than asserted. |
| Memory is written and read back | Both halves verified. `list_events` on the household's actor shows one event per sweep; `recall_facts` then returns the extracted facts — three for `c-010`, one each for `c-011`/`c-012` at the time of writing. **Re-measure before recording**, and note extraction takes ~1–2 minutes after a sweep, so recall immediately after one may legitimately be empty. **"Grace remembers what each sweep concluded and reads it back next cycle" is now a true sentence** — it was not on 2026-09-10, and the earlier handout said so. |
| two rule-pack numbers are federally mandated | `42 CFR 435.916(a)(1)` for the 12-month cycle and `(a)(3)(iii)` for the 90-day reconsideration window. The other ten say `policy choice`. **Do not imply the regulations mandate all of them.** |
| 23 trajectory evals | `pytest evals/ --co -q` — they cost real Bedrock to run |
| approving `c-010` files nothing | its decision + outcome rows, and zero `renewal_submitted` rows |
| Grace files a renewal once per period | a sweep after the nine are filed writes **0** new `renewal_submitted` rows and **9** `renewal_already_filed` rows |
| "waiting on you" on `/` equals the `/queue` count | one field, `CaseSummary.awaitingDecision`. **They disagreed until 2026-09-09** — `/` said 3 and `/queue` said 2 after a decision. Do not describe them as separate counts. |
| four AgentCore surfaces | Runtime, Memory, Identity, harness — Gateway is deferred |

**Never say five surfaces.** It is four, and Gateway's absence is stated in the README with its reason.

## Things not to claim

- Do **not** say "documents on file", in the script or off the cuff. It implies Grace or the
  organisation running it holds the paperwork, and neither does — there is no upload and no S3 bucket
  anywhere in the system. **The family sends documents to the state**, whose eligibility system is the
  system of record and which decides. Grace's users are *navigators* — clinics, food banks, school
  family-support offices — and what a navigator knows is *"I helped this family send their paystub on
  the 20th"*: status, not custody. The interface says **documents sent to the state**, and each entry
  reads *sent 2026-09-20*. Say the same.
- Do **not** describe a document as verified. Grace takes a caseworker's word for it and says so on
  screen: *"Document status asserted by `2448a4e8-…` at intake on 2026-09-06. Grace tracks the deadline
  on it and does not verify it independently."* If you show a case page, that line is on it — read it
  out rather than talking over it. It is the difference between this and a demo that overclaims.
- Do **not** imply `submit_renewal` files with a state agency. It writes a ledger row; there is no
  state integration behind it, and `grace/cases/document_source.py` names the seam where one would
  attach — a stub that **raises**, because one returning an empty tuple would report every household as
  missing every document.
- Do **not** claim the daily schedule has been picking up submitted cases all along. It has fired
  unattended every day since the deploy and every run held both invariants — that part is true and
  worth saying. But until **2026-09-07** the schedule carried a hardcoded list of twelve case ids, so
  those runs swept a frozen caseload. The safe sentence is *"it has run unattended every day since
  deploy, and it now reads its caseload from the table rather than from the schedule."* If you want to
  claim a scheduled run swept a household added afterwards, check that a fire has happened since the
  fix and say the date — do not infer it.
- Do **not** claim Memory does more than it does. Say exactly what `README.md`'s Memory row says: Grace
  **records** what each sweep concluded, verified by reading the events back. Retrieval is wired and was
  not yet surfacing records. "Grace records what it concluded for the next cycle" is true; "Grace
  remembers and recalls" is not yet.
- Do **not** imply every rule-pack number comes from a regulation. Two of twelve are mandated and
  quoted; the rest say `policy choice`, and saying so *is* the point — an uncited number deciding a
  family's coverage is indistinguishable from an invented one.
- Do **not** say traces or Transaction Search work. Zero spans exist in the account.
- Do **not** imply SMS is delivered. The channel is a transcript; the account has no origination number.
- Do **not** describe the household data as real. All twelve are synthetic; phone numbers use the
  reserved `+1555` range.
- Do **not** call the deliberation swarm a general feature. It runs **only** on ambiguous cases — the
  nine clean households never pay for it.
