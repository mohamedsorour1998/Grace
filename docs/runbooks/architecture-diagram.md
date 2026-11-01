# Redrawing the architecture diagram in draw.io

A step-by-step guide to producing `docs/architecture.png` as a draw.io diagram with official AWS
icons. Written 2026-09-12, against the system as deployed at runtime **v9**.

**Why redraw it at all.** The Mermaid source in `docs/architecture.md` renders natively on GitHub and
is the authoritative version — a judge clicking the repo sees it without downloading anything. What
it cannot do is look like an AWS architecture diagram, and one of the five judging criteria is
Presentation. So this is a **second rendering of the same content**, not a replacement.

**The rule that matters more than the icons.** Every claim in the diagram must still be true. If you
simplify a box away, simplify the sentence in the README that describes it too. A diagram that
promises a component the code does not have is the one defect this project has spent seven plans
eliminating.

---

## 0. What you need

- [app.diagrams.net](https://app.diagrams.net) — free, runs in the browser, no account needed
- 40–60 minutes for the first pass
- The Mermaid source at `docs/architecture.md` open in another tab, as your reference

Save the editable source to `docs/architecture.drawio` and export the PNG to
`docs/architecture.png` (overwriting the current one). Commit both — the `.drawio` file is what
lets the next person edit it.

---

## 1. Turn on the AWS icon library

draw.io ships the official AWS sets; they are just off by default.

1. Open a **Blank Diagram**.
2. Bottom of the left shape panel → **`+ More Shapes…`**
3. In the dialog, find the **Networking** category and tick:
   - **AWS 2025** (the current icon set — use this one)
   - *optionally* **AWS 2017** if 2025 is missing a shape you want
4. **Apply.**

You now have `Search Shapes` working over the AWS set. Typing `lambda`, `dynamodb`, `eventbridge`
gives you the real icons.

**File → Page Setup → Paper Size → A3 Landscape.** This diagram has 20+ nodes and will not breathe
on A4.

---

## 2. The one honest substitution you must make

**There is no AgentCore icon in any AWS icon set.** AgentCore is newer than the published sets.

Do **not** invent one, and do not use a generic robot. Use the **Amazon Bedrock** icon and label the
box `Amazon Bedrock AgentCore Runtime`. Bedrock is the correct parent service, so this is accurate
rather than a fudge — and the label carries the precision.

Same for **AgentCore Memory**: Bedrock icon, labelled `AgentCore Memory`.

Two things that must **not** get an AWS icon, because they are not AWS products — they are this
project's own code, and that distinction is the whole point of the diagram:

| Component | Draw it as | Why |
|---|---|---|
| **The authority gate** (`authority.py`) | A **hexagon** or **diamond**, orange fill `#B4530A`, white text | Pure Python. No model, no I/O. Giving it a service icon would imply AWS enforces it; the claim is that *deterministic code* does. |
| **`authorize()`** (`lib/authorize.ts`) | Same hexagon shape, same orange | Pure TypeScript, the dashboard's mirror of the gate. |

Using one distinct shape and colour for both is deliberate: a judge scanning the diagram should see
at a glance that the two enforcement points are the same *kind* of thing.

---

## 3. Optional head start: import the Mermaid

You can let draw.io lay out the boxes, then swap in icons.

**Arrange → Insert → Advanced → Mermaid…**, paste the ```mermaid block from `docs/architecture.md`,
**Insert**.

You get every node and edge positioned roughly right. Then for each AWS service: drag the real icon
next to the box, copy the label across, delete the plain box, and reconnect. Faster than starting
blank for the 8 service nodes; the swarm and gate you will draw by hand anyway.

If the import looks tangled, delete it and build by hand from §4 — the layout below is designed to
be drawn in one pass.

---

## 4. Layout — five bands, top to bottom

Draw it as five horizontal bands. This is the shape that makes the story readable: **nobody opens an
app** at the top, **the agent** in the middle, **the human** at the bottom.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BAND 1 · Unattended — nobody opens an app to do this                         │
│   EventBridge  →  Step Functions  →  Lambda                                  │
└──────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ BAND 2 · Amazon Bedrock AgentCore Runtime — Strands Agents SDK               │
│                                                                              │
│   intake → documents ──┬─(ambiguous)→ [ SWARM ] ──┐                          │
│                        └─(clean, dotted)──────────┴→ decide → ⬡ GATE         │
│                                                       ↓                      │
│                                              draft_family_message            │
└──────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ BAND 3 · One Strands node's agentic loop — where the gate sits    [INSET]    │
│   model → tool selection → ⬡ SteeringHandler → tool executes → HookProvider  │
│                                    ↘ refused → a human decides               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ BAND 4 · Durable state          DynamoDB        AgentCore Memory             │
│                                     ↓                                        │
│                              CloudWatch alarm                                │
└──────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ BAND 5 · The human half — grace.rosettacloud.app                            │
│   caseworker → Cognito → Amplify SSR → ⬡ authorize() → lib/decide.ts        │
└──────────────────────────────────────────────────────────────────────────────┘
```

Use a **Container** (search `container`) or a rounded rectangle with no fill and a dashed border for
each band, and put its title in the top-left. Group each band (`⌘G`) once its contents are placed,
so you can move a whole band without disturbing it.

---

## 5. Every node, with its icon and its exact label

Copy these labels verbatim — each one is a claim the repository can back.

### Band 1 — Unattended

| Search for | Label (use `Shift+Enter` for line breaks) |
|---|---|
| `eventbridge` | **Amazon EventBridge**<br>`grace-daily-sweep`<br>cron 09:00 UTC |
| `step functions` | **AWS Step Functions**<br>`grace-sweep`<br>Map over the caseload · maxConcurrency 3<br>Retry ×2 · Catch → escalate |
| `lambda` | **AWS Lambda**<br>`grace-invoke-case`<br>one household per invocation<br>`total_max_attempts 1` |

Edges: `EventBridge → Step Functions → Lambda → intake`, plain solid arrows.

### Band 2 — the agent

Band title: **`Amazon Bedrock AgentCore Runtime — grace_grace-oTyyvo8stE`**
Subtitle inside the container, one size smaller:
**`Strands Agents SDK — Graph · Swarm · agents-as-tools · SteeringHandler · HookProvider`**

That subtitle is not decoration. The hackathon FAQ requires the diagram to show *"Strands Agents:
the core agent and its agentic loop"*, and a Devpost update says naming the SDK explicitly is one of
the first things reviewed.

| Node | Shape | Label |
|---|---|---|
| intake | Rounded rectangle | **intake**<br>Nova 2 Lite<br>*read tools only* |
| documents | Rounded rectangle | **documents**<br>Nova 2 Lite<br>*read tools only* |
| swarm | **Container** holding three small boxes | **eligibility swarm** — *ambiguous cases only*<br>advocate `Nova 2 Lite` → verifier `Nova Pro` → referee `Nova Micro`<br>*three different models on purpose* |
| decide | Rounded rectangle, slightly heavier border | **decide**<br>Nova Pro<br>*the only node with action tools* |
| drafter | Rounded rectangle, dashed border | **`draft_family_message`**<br>*agents-as-tools* · Nova 2 Lite<br>no tools · no store · no case id<br>writes in the family's language |
| **GATE** | **Hexagon**, fill `#B4530A`, white text | **The authority gate**<br>`authority.py` — pure Python<br>no model · no I/O<br>**act** · or · **escalate** |

Edges inside band 2:

- `intake → documents` — solid
- `documents → swarm` — solid, label **`ambiguous`**
- `documents → decide` — **dashed**, label **`clean case — skips the swarm`**
- `swarm → decide` — solid
- `decide → GATE` — solid
- `decide → drafter` — dashed, label **`needs words for a family`**

**Draw the dashed `documents → decide` edge with real emphasis.** It is the cheapest thing in the
diagram to overlook and one of the most interesting claims: nine of twelve households never pay for
the three-model swarm. Consider labelling it `9 of 12` if space allows.

### Band 3 — the agentic loop inset

Draw this small and to one side; it is an inset, not part of the main flow. Six plain boxes in a
ring, left to right then back:

`model (Amazon Nova)` → `tool selection` → ⬡ **`SteeringHandler` — the authority gate — Proceed · Guide · Interrupt`** → `tool executes (SequentialToolExecutor)` → `HookProvider — appends to the ledger` → back to `model`

Plus one branch off the hexagon: `→ refused → a human decides`.

Then one **dashed** arrow from `decide` (band 2) into this hexagon, labelled
**`every state-changing call`**. That arrow is what ties the inset to the system.

### Band 4 — durable state

| Search for | Label |
|---|---|
| `dynamodb` | **Amazon DynamoDB**<br>`grace-cases`<br>ledger · escalation queue · decisions<br>PITR enabled |
| `bedrock` | **AgentCore Memory**<br>`grace_household_memory`<br>per-household facts · 365-day expiry |
| `cloudwatch` | **Amazon CloudWatch alarm**<br>`escalations < 3`<br>*not error rate — see below* |

Edges:

- `GATE → DynamoDB` ×2: one labelled **`act — renewal filed`**, one **`escalate — a human decides`**
- `decide → Memory` — solid, label **`writes each outcome`**
- `Memory → swarm` — **dashed**, label **`prior cycles — advisory only`**
- `DynamoDB → CloudWatch alarm` — solid

**The `Memory → swarm` arrow must be dashed and must say "advisory".** A solid arrow would imply
recall can influence the verdict. It cannot: `evaluate()` has no parameter a lesson could occupy, and
a test asserts `authority.py` cannot even import the memory module.

### Band 5 — the human half

Band title: **`The human half — grace.rosettacloud.app`**

| Search for | Label |
|---|---|
| `user` (the AWS "User" shape) | **caseworker** |
| `cognito` | **Amazon Cognito**<br>`grace-caseworkers`<br>admin-create only · `custom:role`<br>managed login on `auth.rosettacloud.app` |
| `amplify` | **AWS Amplify** SSR `WEB_COMPUTE`<br>`requireSession` verifies **every** request<br>sweep · queue · one case's audit trail |
| — (hexagon, orange) | **`authorize()`** — pure TypeScript<br>is this case decidable, by this session? |
| — (rounded rect) | **`lib/decide.ts`**<br>writes the row, *then* re-invokes<br>`maxAttempts 1` |

Edges:

- `caseworker → Cognito` — label **`sign in`**
- `Cognito → Amplify` — label **`ID token → httpOnly cookie`**
- `Amplify → DynamoDB` — label **`reads, server-side only`**
- `Amplify → authorize()` — solid
- `authorize() → lib/decide.ts` — label **`permitted`**
- `lib/decide.ts → DynamoDB` — label **`1 · DECISION# row`**
- `lib/decide.ts → decide` — **thick** arrow, label
  **`2 · InvokeAgentRuntime — never resumes a paused graph`**

### One more node, off to the side

| Search for | Label |
|---|---|
| `sns` | **Family channel** (Amazon SNS)<br>*sandboxed — the transcript is the working path* |

Dashed arrows into it from `drafter` and `decide`. **Keep the "sandboxed" caveat in the label.** The
account has zero origination numbers; a diagram implying delivered SMS is a claim the demo cannot
back.

---

## 6. Style — four decisions, then stop fiddling

Grace's own palette, so the diagram matches the dashboard a judge will also see:

| Use | Hex |
|---|---|
| Gate / enforcement (hexagons) | fill `#B4530A`, stroke `#7a3806`, text `#FFFFFF` |
| Human half (band 5 nodes) | fill `#2F6F4E`, stroke `#1f4a34`, text `#FFFFFF` |
| Durable state | fill `#E4E1D8`, stroke `#b8b3a5`, text `#1C1F23` |
| Everything else | white fill, `#1C1F23` text, thin grey stroke |

To apply: select the shape → **Format panel → Style → Fill/Line**, or paste
`fillColor=#B4530A;strokeColor=#7a3806;fontColor=#FFFFFF;` into **Edit Style** (`⌘E`).

Three habits that make it read well:

- **Font 11–12pt** for labels, 14pt bold for band titles. Anything smaller vanishes in a video frame.
- **Right-angle edges** (`Format → Edge style → Orthogonal`) everywhere except the swarm's internal
  handoffs. Curved lines in a system diagram read as decorative.
- **Solid = it always happens. Dashed = it happens conditionally or advisorily.** Be strict about
  this; it is carrying real meaning here.

---

## 7. Export and commit

1. **File → Save As → Device** → `docs/architecture.drawio`
2. **File → Export as → PNG…**
   - **Zoom 200%** (so it stays legible in a video frame)
   - **Border Width 10**
   - **Transparent Background: off** (white — it will be viewed on GitHub's white page)
   - ✅ **Include a copy of my diagram** — this embeds the editable source *inside* the PNG, so the
     image and its source can never drift apart
3. Save over `docs/architecture.png`
4. Commit both files:

```bash
git add docs/architecture.drawio docs/architecture.png
git commit -m "docs: architecture diagram redrawn in draw.io with official AWS icons"
```

---

## 8. Before you ship it — nine checks

Walk this list against the finished image. Each one is either a hackathon requirement or a claim this
repository makes.

- [ ] **"Strands Agents SDK" appears in text**, not only implied by a Bedrock icon. *(FAQ requirement;
      a Devpost update calls it one of the first things reviewed.)*
- [ ] **The agentic loop is visible** — model → tools → gate → execute → hook → back. *(FAQ names it
      explicitly.)*
- [ ] **The user's entry point is shown** — the caseworker, signing in. *(FAQ requirement.)*
- [ ] **Every AWS service in the stack appears**: EventBridge, Step Functions, Lambda, Bedrock,
      DynamoDB, Cognito, Amplify, CloudWatch, SNS. *(FAQ requirement.)*
- [ ] **The output is shown** — a filed renewal, an escalation row, a message to the family.
- [ ] **The gate is not an AWS icon.** It is this project's own deterministic code, and the diagram
      should make that visually obvious.
- [ ] **`documents → decide` is dashed** and says the clean case skips the swarm.
- [ ] **`Memory → swarm` is dashed** and says *advisory*.
- [ ] **Nothing on the diagram is a component the code does not have.** Read every label once more
      against `docs/architecture.md`. If you simplified something away, check the README's prose does
      not still promise it.

**Four surfaces, never five.** If AgentCore Gateway appears anywhere on this diagram, delete it — it
is deliberately deferred, and the README says so with its reason.

---

## 9. If you would rather not redraw it

The Mermaid version is genuinely sufficient for submission: it renders on GitHub, it satisfies every
FAQ element, and `docs/architecture.md` is what the README links. The current PNG is stale (generated
before the drafter, Memory, and the loop inset were added) — so if you skip the redraw, the honest
minimum is to **delete `docs/architecture.png` and point the README at the Mermaid only**, rather
than shipping an image that shows an older system than the one running.

A stale diagram is worse than no diagram. It is the same defect class as a comment that stopped being
true.
