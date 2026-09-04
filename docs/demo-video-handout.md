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

**One thing to know before you demo the approval.** `c-010` has **already been approved once** — Task 8
did it during verification, and the duplicate guard is deliberate, so a second attempt returns
`409 already_decided` rather than re-running Grace. You have two honest options:

- **Recommended: demo the approval on `c-011` or `c-012`**, which are still undecided. Say plainly that
  Grace re-checks and decides for itself; if it *does* file, that is a legitimate outcome — the gate
  escalated on an ambiguity a human resolved, and the reason is recorded either way.
- **Or show `c-010`'s existing decision** on its case page. The outcome row already reads *"Grace
  re-checked and did not file. missing_document: proof_of_residency is not on file"* — which is the
  stronger claim, just not filmed live.

Do not stage a fake approval to make the demo cleaner. The whole entry rests on claims being real.

---

## Shot list

### 0:00–0:45 · The problem, and who it is for

*No screen needed — a title slide is fine.*

> During Medicaid unwinding, **most disenrollments were procedural**. Not people who stopped
> qualifying — people who missed a letter, a deadline, or one document. They lost coverage to
> paperwork.
>
> This is for the **caseworker** carrying hundreds of those files, and for the **family** who never
> finds out they were dropped until a pharmacy turns them away.
>
> It matters because the failure is silent. Nobody gets an error. A renewal simply does not happen,
> and a family loses health coverage they were entitled to.

`<replace this text by a screenshot of the title slide — "Grace: an agent that keeps families from losing benefits over paperwork">`

### 0:45–1:15 · What Grace does

> Grace watches every household's renewal clock. It files the renewals that are unambiguous, chases
> the one missing document by text, and wakes a human **only** when eligibility is genuinely in doubt.
>
> On twelve households it handles nine alone and escalates three — each with a typed reason.

`<replace this text by a screenshot of the architecture diagram from docs/architecture.png>`

### 1:15–2:15 · The dashboard — the sweep and the queue

*Screen recording, live.*

Open **`https://grace.rosettacloud.app`**.

> This is the caseworker's view. Twelve households, **nine handled alone, three waiting on a human.**
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

`<replace this text by a screenshot of the /case/c-010 page showing the typed reason and the ledger>`

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
| 12 households, 9 act, 3 escalate | `evaluate()` over `fixtures/households.yaml` at `today=2026-10-01` |
| 9 acted / 3 escalated deployed | the `grace-sweep` Step Functions execution output |
| `renewal_submitted` for exactly `c-001`–`c-009` | a full DynamoDB scan; the invariant, not the row count |
| 715 Python tests, 157 vitest | `pytest` and `vitest run` |
| 23 trajectory evals | `pytest evals/ --co -q` — they cost real Bedrock to run |
| approving `c-010` files nothing | its decision + outcome rows, and zero `renewal_submitted` rows |
| four AgentCore surfaces | Runtime, Memory, Identity, harness — Gateway is deferred |

**Never say five surfaces.** It is four, and Gateway's absence is stated in the README with its reason.

## Things not to claim

- Do **not** say traces or Transaction Search work. Zero spans exist in the account.
- Do **not** imply SMS is delivered. The channel is a transcript; the account has no origination number.
- Do **not** describe the household data as real. All twelve are synthetic; phone numbers use the
  reserved `+1555` range.
- Do **not** call the deliberation swarm a general feature. It runs **only** on ambiguous cases — the
  nine clean households never pay for it.
