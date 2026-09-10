# Grace — grand-prize spec

**Written 2026-09-10 (Wednesday). Deadline 2026-09-14 17:00 PT = 2026-09-15 03:00 Cairo.**
Five working days. This is a spec, not an implementation plan: it says *what* and *why* and what
"done" looks like, and the implementation plan derives tasks from it.

The single prize this targets is the **$10,000 Grand Prize** — one winner across all ~8,954
participants and three tracks. The scoring is five equally-weighted criteria (Technical
Implementation, Design, Potential Impact, Creativity & Originality, Presentation), 1–5 each, plus
**up to 0.6 bonus for builder.aws posts (0.2 each, max three)**. Final range 1–5.6. Tie-breaks go to
Technical Implementation first.

---

## 1. Where Grace actually stands

I read every deliverable as a judge would, cloned AWS's own Strands workshop to see what it treats as
canonical, audited the code against the claims, and read the competing builder.aws posts.

**Genuinely strong, and rarer than it looks in this field:**

- Deployed on AgentCore Runtime (v5), **running unattended on a daily schedule since 2026-09-03**,
  with both invariants verified from DynamoDB after every run. Most competitors are local prototypes
  or deployed-once.
- A real, Cognito-gated, server-rendered caseworker dashboard with intake — an end-to-end product,
  not a CLI.
- A three-model adversarial swarm (advocate / verifier / referee on three *different* Nova models),
  routed by a deterministic predicate so nine of twelve households never pay for it.
- Evidence discipline nobody else shows: sabotage-tested guards, invariants checked from the store
  rather than the log line, defects documented with the measurement that found them.
- The largest, most concrete harm figure in the field: **~17 million Americans lost coverage they
  still qualified for.**

**Weak, and a judge will find it:**

| Finding | Severity | Why it matters |
|---|---|---|
| **AgentCore Memory is claimed "Shipped" and used by nothing.** `build_session_manager` has zero callers; the resource is never read or written. | **Critical — integrity** | Contradicts the project's own "four surfaces, never five" honesty claim. Technical Implementation *and* trust. |
| **Agents-as-tools is claimed as the third multi-agent pattern and does not exist.** No `@tool` wraps an `Agent`. The `outreach` and `judge` model roles are defined and never called. | **Critical — integrity** | Same. The README's models table lists roles that are not in the code. |
| The multilingual-outreach claim — first sentence of the README — is **never demonstrated**. The only household that gets messaged (`c-010`) is English. Eight non-English households never receive a message. | High — Presentation, Impact | The demo shows an English SMS. The claim is decorative. |
| Rule packs cite **no regulation**. Two YAML files, one state, bare parameters. | High — Originality ("genuine understanding of the problem space") | A competitor validated against 42,388 real records. Twelve synthetic households with uncited rules reads as toy. |
| Architecture diagram never labels **Strands Agents** and does not show the agent loop. The FAQ names both as required diagram content. | Medium — Presentation | "Name Strands Agents explicitly — it's one of the first things reviewed." |
| Builder ID recorded as `@sorour`; Devpost wants **the email**. | Medium — submission hygiene | `mohamedsorour1998@gmail.com` |
| "people" throughout; the judges are American and the event was American. | Medium — Impact | "17 million Americans" is accurate (Medicaid is US-only) and lands harder. |
| **No Devpost text description exists** as an artifact. The README is 500 lines. | High — Presentation | Judges "may judge based solely on the text description, images, and video." |
| **No video. No blog post published.** | **Critical — hard requirement / 12% of max score** | The video is a pass/fail gate. Three posts = 0.6 on a 5-point scale. |
| No Skills, no `LLMSteeringHandler`, no `strands-agents-evals` — three of the workshop's seven "production capabilities". | Low–Medium — Technical | Deferred with reasons; only LLM steering is worth reconsidering (below). |

**The competitive position, stated plainly.** The escalation-boundary idea is the *dominant* pattern
in this hackathon's builder.aws posts — Governor Agent, Authority Cut, Countersign, CareLoop, Standby,
ProofPack, BeforeBell, VibeGate, "the interrupt is the product", "keeping legal deadline math out of
the LLM". **StillOn** is in the same track and the same domain (SNAP/Medicaid caseload, wakes a
caseworker only for judgment). So "we have a deterministic gate" is not a differentiator; it is the
entry fee. What differentiates Grace is: the scale and specificity of the harm, that it is *running*
rather than demonstrated, the completeness of the product, and the verification discipline. **Every
deliverable must lead with those, not with the gate.**

---

## 2. Priorities

Ordered by expected score impact per hour, with the hard requirements first because nothing else
matters if they are missing.

### P0 — hard requirements and integrity (must land)

**P0.1 — The video.** Not started. It is the one pass/fail artifact. Record **one** ~4:30 take from
`docs/demo-video-handout.md`; the blog's <3:00 cut is the same recording with the opening statistics
slide and the "honest about what does not work" section trimmed. Record **after** the code freeze
(§4) so it shows the final system. Public on YouTube. *Owner: human. Nothing in the repo produces it.*

**P0.2 — Resolve the Memory overclaim.** Two acceptable outcomes, in preference order:

- **(a) Wire it.** `GraphBuilder.set_session_manager` exists (verified against the installed SDK —
  `memory.py`'s docstring claiming "no legal place inside the graph" is wrong). Build the session
  manager in `entrypoint.py`, pass it into `build_case_graph`, set `GRACE_MEMORY_ID` in
  `agentcore.json`'s `envVars`, redeploy. **Before committing to this shape, verify what a
  Graph-level `AgentCoreMemorySessionManager` actually writes and retrieves** — it is designed for
  `Agent`, and the multi-agent persistence path may store graph state rather than the conversational
  events the memory strategies extract facts from. If it stores nothing the strategies can use, use
  the direct `bedrock_agentcore` memory client instead: write one fact per household after each
  outcome (`"2026-10-01: escalated — missing proof_of_residency; family messaged (es)"`), read the
  last N facts into `decide`'s task text as **advisory** context. Hard rule 5 applies: recall may
  make Grace more cautious, never satisfy a gate condition — and `evaluate()` never sees it.
- **(b) Downgrade the claim.** If (a) is not verified on the deployed system by **Friday EOD**, change
  every "Shipped" to "Provisioned, not yet consulted by the agent" and "four surfaces" to "three, with
  Memory provisioned." Remove the `decide ↔ Memory` edge from the diagram. This is the honest floor
  and costs thirty minutes.

Acceptance for (a): a deployed sweep still returns 9/3 with both invariants; the memory resource shows
events/records for at least the three escalated households after the sweep; `docs/deployed-verification.md`
records the retrieval read back, not the write returning.

**P0.3 — Resolve the agents-as-tools overclaim.** Implement it — this is the cheapest real feature
in this spec and it is exactly the workshop's Module 6 pattern:

- An `outreach_drafter` agent on `nova("outreach")`, wrapped with `@tool def draft_family_message(document_id, deadline, language) -> str`,
  given to `decide` alongside the read tools. It writes a short, warm message in the household's
  language asking for the one document by the deadline. `callback_handler=None`, returns `str(response)`.
- `decide`'s prompt changes from "write a message" to "call `draft_family_message`, then pass its
  output to `send_family_message`."
- **The drafter receives no household identity** (it cannot — `read_case` no longer returns any) and
  takes *content* arguments only, never a case id. It gets **no action tools** and **no gate**; the
  gate on `decide` still governs `send_family_message`, so the drafter cannot cause a send.
- Delete or use the `judge` role (see P2.3); a defined-and-unused role is the same shape of overclaim.

Acceptance: `c-010`'s `family_message_sent` row on the deployed system carries a body the drafter
wrote; the README's "three patterns" sentence is true; `models.py` has no role nothing calls.

**P0.4 — Builder ID.** Replace `@sorour` in `README.md` (two places), `docs/builder-blog-post.md`
(`Team:` line), and `CLAUDE.md` with `mohamedsorour1998@gmail.com`, and say it is the email the
Devpost form expects.

**P0.5 — Devpost text description.** A new file `docs/devpost-description.md`, ~350–450 words,
pasted into the submission form verbatim. Leads with the problem in two sentences, names **Strands
Agents SDK** and **Amazon Bedrock AgentCore** in the first paragraph, states who it is for, what it
does end to end, what is deployed, and what is deliberately not claimed. No code, no headers deeper
than one level. Appendix A of this spec is a draft to start from.

### P1 — score levers (high value, low risk)

**P1.1 — Three builder.aws posts, not one.** 0.6 bonus points is **12% of the maximum score** and
the cheapest points available. The existing draft is post 1. Posts 2 and 3 are already written —
they are sections of the same draft and of `docs/deployed-verification.md`, restructured:

| Post | Title (must contain "Agents for Humans") | Source material |
|---|---|---|
| 1 | *Agents for Humans: 17 million Americans lost health coverage to paperwork* | `docs/builder-blog-post.md` as is, with §P1.2's wording |
| 2 | *Agents for Humans: the defect that was invisible because nothing failed* | The "deployed is not written" story — runtime v2 vs v3, the frozen EventBridge list, `verify_deployed`. Already drafted as a section of post 1; lift it out and expand with the measurements. |
| 3 | *Agents for Humans: a test you never watched fail is a sentence that agrees with you* | The sabotage methodology — the approval trap that would have expired on 2026-10-01, the `decisions.length === 0` page bug, the vacuous no-drift test the fixer caught. Nothing else in the field describes this discipline. |

Each ~1,200–1,800 words, its own cover image, public before the deadline, "Agents for Humans" in the
title. Post 1 keeps the AWS finalist format (App Category / My Vision / Why This Matters / How I Built
This / Demo / What I Learned). Posts 2–3 are build-journey posts and need no video.

**P1.2 — "Americans".** In every headline figure: "more than 25 million Americans lost Medicaid
coverage", "about 17 million Americans lost health insurance they still qualified for". Medicaid is a
US programme, so this is accurate. Keep the sources' own wording ("people", "enrollees") inside the
citation footnotes. Files: `README.md`, `docs/builder-blog-post.md`, `docs/demo-video-handout.md`,
`docs/devpost-description.md`, the title slide.

**P1.3 — Demonstrate the multilingual claim.** Change `c-010`'s `language` from `"en"` to `"es"` in
`fixtures/households.yaml`, update the live `RECORD#v1` row's `language` attribute for `c-010` (a
targeted `UpdateItem`; `seed_cases.py` will not overwrite), run one sweep, and confirm the new
`family_message_sent` row's body is Spanish. Then the case page a judge opens — and the one in the
video — shows Grace writing to the family in their language. Check nothing in `tests/` pins `c-010`'s
language (my grep found nothing). If Nova Pro writes English anyway, the drafter from P0.3 is the fix:
its prompt is single-purpose and language is an explicit argument.

**P1.4 — Cite the regulations in the rule packs.** Add a `sources:` block to each pack naming the
actual authority for each parameter, and render it in the README's rules section. The federal
Medicaid renewal rule is 42 CFR § 435.916 (12-month renewal period; the reconsideration period after
a procedural termination); SNAP recertification is 7 CFR § 273.14. New York's implementing regulation
is in 18 NYCRR. **Verify every citation against the actual regulation text before committing — a
wrong citation is worse than none**, and the parameters in the packs (60-day window, 90-day grace,
5% band) must each trace to a specific provision or be marked as the deployment's own policy choice.
This is the single cheapest answer to "genuine understanding of the problem space."

**P1.5 — The architecture diagram.** Two changes to `docs/architecture.md` and the regenerated PNG:
label the runtime subgraph **"Strands Agents SDK — Graph + Swarm + agents-as-tools"**, and add a small
inset showing one node's loop (model → tools → hooks/steering → response) so the FAQ's required
elements are all visibly present. Keep everything else.

**P1.6 — Re-lead every deliverable.** The README's first screen, the video's first 45 seconds, the
Devpost description's first paragraph, and post 1's opening all lead with **the harm and the running
system**, and reach the gate third. Currently the README reaches "escalation boundary" by line 70 and
the video by 2:15 — fine — but the *framing* is "what makes Grace different is the gate", which is
false in this field. Reframe: what makes Grace different is that it is a complete, deployed,
verified product for the largest procedural-loss event in US benefits history, and the gate is *how*
it earns the right to run unattended.

### P2 — differentiators (do if P0–P1 are done by Friday)

**P2.1 — Reflection loop, on Memory.** The README calls this "genuinely the originality
differentiator" and defers it because "it cannot be built before a deployed sweep exists to reflect
on." Seven sweeps now exist. If P0.2(a) lands, this is the natural use: after each outcome, write a
one-line lesson to the household's `/facts/` namespace; before the swarm runs, retrieve the last few
lessons across households and prepend them to the advocate's task as *"prior deliberations found: …
(advisory)"*. It may only ever add caution. Hard rule 5. Acceptance: a lesson written by one sweep is
visible in the next sweep's advocate input, and `evaluate()`'s verdict on every household is
unchanged.

**P2.2 — One more state.** Texas and Florida had the largest unwinding losses. Adding `medicaid-tx.yaml`
with cited parameters, `"TX"` in `web/lib/intake.ts`'s `STATES`, and one fixture household turns "New
York only" into "the two states where most of the 17 million lived." Only if P1.4's citation work
makes the second pack's sources easy to find; do not invent parameters.

**P2.3 — `LLMSteeringHandler` as an advisory tone check on outreach.** Uses the `judge` role. Runs
`steer_after_model` on the drafter only, returning `Proceed`/`Guide` — it can never `Interrupt` and
never touches the gate. It closes one of the workshop's seven capabilities and gives the `judge` role
a reason to exist. Skip if P0.3 deletes the role instead.

**P2.4 — Cite state-specific CMS unwinding data.** CMS publishes monthly renewal outcomes by state
(ex parte rate, procedural termination rate). One sentence in the README and post 1 — *"New York's
procedural termination rate during the unwinding was X%"* — connects the rule pack to the number it
exists to reduce. Verify the figure against the CMS dataset; cite the table.

### Out of scope, with reasons

| Not doing | Why |
|---|---|
| CloudWatch traces / `aws-opentelemetry-distro` | Documented as not working, honestly. Five days out, a change to the telemetry provider on the deployed runtime risks the one thing that must not break. The README's stated reason for refusing the package is contestable but the risk calculus is not. |
| `strands-agents-evals` | Pulls `strands-agents-tools` and 25 packages Grace never imports. The pytest trajectory evals are real and read the ledger. Say so in one README sentence: *"trajectory evals are pytest functions reading the ledger; the `strands-agents-evals` package was not adopted because of its dependency footprint."* |
| Skills / `AgentSkills` | Grace's prompts are short and single-purpose; skills solve a problem it does not have. Keep the README's deferral sentence. |
| AgentCore Gateway | Correctly deferred with a written reason that survives scrutiny. |
| Real SMS | Sandboxed account; the transcript is the honest path and the README says so. |
| Rewriting the swarm, the gate, or the dashboard | They work and are verified. Every hour here is an hour not spent on the video. |

---

## 3. Judging-criteria map

| Criterion | What scores now | What this spec adds |
|---|---|---|
| **Technical Implementation** | Runtime deployed, Graph + Swarm, steering, hooks, live demo link | Memory *actually* used (P0.2), agents-as-tools *actually* present (P0.3), LLM steering (P2.3) — three of the workshop's capabilities move from "claimed/absent" to "real". Tie-break criterion. |
| **Design** | Complete product: intake → sweep → queue → decision → audit trail; polished sign-in | Multilingual outreach visible (P1.3); consistent headlines already fixed. |
| **Potential Impact** | 17M figure, credible domain model (navigator vs state), honest scope | "Americans" (P1.2), cited regulations (P1.4), state-specific CMS data (P2.4), a second state (P2.2). Answers the 42,388-records competitor. |
| **Creativity & Originality** | Three-model swarm, capability absence, sabotage discipline | Reflection loop (P2.1) — the README's own named differentiator. Re-leading away from the gate (P1.6), because the gate is not original in this field. |
| **Presentation** | Handout ready, figures sourced | The video (P0.1), Devpost description (P0.5), diagram labels (P1.5). |
| **Bonus** | 0 | Three posts = 0.6 (P1.1). |

---

## 4. Schedule

Code freeze **Friday 2026-09-12 EOD Cairo**. Nothing that changes the deployed runtime after that.

| Day | Do | Owner |
|---|---|---|
| **Wed 10** | P0.4 Builder ID · P1.2 Americans · P1.4 citations (verify!) · P1.5 diagram · P0.5 Devpost draft · P1.6 re-lead the README · start P0.3 drafter | Opus |
| **Thu 11** | P0.3 drafter landed + P1.3 Spanish `c-010` → redeploy v6 → one sweep, verify 9/3 + invariants + Spanish body · start P0.2(a) Memory | Opus |
| **Fri 12** | P0.2(a) Memory landed and verified on deployed **or** P0.2(b) downgrade by EOD · P2.1 reflection only if Memory verified by noon · P2.3 if time · **freeze** · run `verify_deployed`, full sweep, PII scan, update `deployed-verification.md` | Opus |
| **Sat 13** | Screenshots for every `<replace…>` marker · posts 2 and 3 written · all three posts **published** on builder.aws · Devpost description final | Opus writes, human publishes |
| **Sun 14** | **Record the video** from the handout · upload public to YouTube · cut the <3:00 version · embed in post 1 · fill the Devpost form | Human |
| **Mon 15** (deadline 03:00 Tue Cairo) | Buffer. Re-verify the live site and both invariants one last time. Submit **before** Monday 17:00 PT. Do not use the buffer for features. | Human |

If Thursday's redeploy breaks 9/3, **revert to v5 the same day** and ship without P0.3 — the honest
README downgrade (P0.2b's sibling for agents-as-tools) takes twenty minutes. A broken demo loses more
than a missing feature.

---

## 5. Definition of done

- [ ] Video public on YouTube, ≤5:00, covers problem / who / why / working demo
- [ ] Devpost form complete: description, repo URL, video URL, live demo URL, Builder ID email
- [ ] Three posts public on builder.aws.com, each with "Agents for Humans" in the title
- [ ] No claim in `README.md`, `docs/architecture.md`, or post 1 that the code does not back — specifically Memory and agents-as-tools are each either real or downgraded
- [ ] `models.py` has no role nothing calls
- [ ] The deployed system reports 9/3 with both invariants after the last redeploy, and `docs/deployed-verification.md` records it with a date
- [ ] `c-010`'s outreach body on the live case page is not English
- [ ] Every rule-pack parameter traces to a cited provision or is marked as a policy choice
- [ ] `verify_deployed` reports no drift; all five gates green; working tree clean and pushed

---

## Appendix A — Devpost description draft

> When pandemic-era Medicaid protections ended in 2023, more than 25 million Americans lost coverage —
> and about 69% of them lost it to paperwork, not eligibility. Roughly 17 million people who still
> qualified lost health insurance because a letter went unanswered or one document never arrived.
>
> **Grace** is an AI agent, built with the **Strands Agents SDK** and deployed on **Amazon Bedrock
> AgentCore**, that keeps that from happening. It watches every household's renewal deadline against
> the encoded rules for its programme, files the renewals that are unambiguous, texts the family in
> their own language for the one missing document, and wakes a human caseworker only when eligibility
> is genuinely in doubt.
>
> **Who it's for.** The caseworkers at community clinics, food banks, and school family-support
> offices who track recertification windows for hundreds of households across programmes with
> different clocks. They enrol a household once; Grace watches the clocks from then on.
>
> **How it works.** A daily EventBridge schedule starts a Step Functions sweep that invokes Grace on
> AgentCore Runtime for each household. Inside, a Strands **Graph** runs intake and document checks,
> routes only ambiguous cases to a three-agent **Swarm** — an advocate, an adversarial verifier, and a
> referee on three different Amazon Nova models — and hands the decision to a node whose every
> state-changing tool passes through a deterministic authority gate implemented as a Strands
> steering handler. Privileged tools are simply absent for cases that have not passed verification;
> every household-scoped tool takes zero arguments, so a prompt injection has no parameter to poison.
> A hook appends every tool call to a DynamoDB ledger, which is the audit trail and the evidence.
>
> Caseworkers sign in through Cognito to a server-rendered dashboard: the sweep at a glance, the
> escalation queue, one household's full audit trail, and an approve/deny control. Approving does not
> bypass anything — it records the decision and re-runs Grace so the gate evaluates the facts again.
> On the household missing a document, a human said yes and Grace still filed nothing.
>
> **What's real.** It has run unattended every day since deploy. On twelve seeded synthetic
> households it handles nine alone and escalates three, each with a typed reason — confirmed from the
> ledger, not a log line. All data is synthetic; no household identity exists anywhere a model or a
> log can reach. The repository states what does not work: SMS is sandboxed, so the family channel
> writes a transcript; filing writes a ledger row, because a real state endpoint needs a data-sharing
> agreement rather than code.
>
> Live: grace.rosettacloud.app · Code: github.com/mohamedsorour1998/Grace (MIT)

*(Adjust "three different Nova models" and the surface list if P0.2/P0.3 change what is true.)*

## Appendix B — what I checked and how

- Cloned `aws-samples/sample-strands-agents-hands-on-workshop`; its seven modules are the capability
  checklist in §1's last row.
- `grep` for `build_session_manager`, `GRACE_MEMORY_ID`, `session_manager` across the request path:
  zero hits outside `grace/memory.py` and `infra/provision_memory.py`.
- `grep` for `@tool` wrapping `Agent(` in `grace/tools/`: none. `nova("outreach")` and
  `nova("judge")`: never called.
- `inspect.signature(Graph.__init__)` includes `session_manager`; `GraphBuilder.set_session_manager`
  exists.
- `evaluate()` over the twelve fixtures at the pinned date: only `c-010` triggers outreach; it is `en`.
- Read `README.md`, `docs/architecture.md`, `docs/builder-blog-post.md`, `docs/demo-video-handout.md`,
  `grace/graph.py`, `grace/memory.py`, `grace/models.py`, both rule packs, the fixtures.
- Read the titles and abstracts of ~120 builder.aws "Agents for Humans" posts for the competitive
  picture in §1.
