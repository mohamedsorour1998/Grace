# Grace — architecture

One diagram of the whole system, and then the three claims it exists to support.

Everything below is deployed and running in `us-east-1` unless a note says otherwise. Mermaid renders
natively on GitHub, so this file is the architecture diagram; `docs/deployed-verification.md` is the
evidence that it behaves as drawn.

---

## The system

```mermaid
flowchart TB
    subgraph trigger["Unattended — nobody opens an app to do this"]
        EB["EventBridge<br/><code>grace-daily-sweep</code><br/>cron 09:00 UTC"]
        SFN["Step Functions <code>grace-sweep</code><br/>Map over 12 households<br/>maxConcurrency 3<br/>Retry 2x · Catch → escalate"]
        LAM["Lambda <code>grace-invoke-case</code><br/>one household per invocation<br/>total_max_attempts 1"]
    end

    subgraph runtime["AgentCore Runtime — grace_grace-oTyyvo8stE"]
        direction TB
        GATE{{"<b>The authority gate</b><br/><code>authority.py</code> — pure Python<br/>no model, no I/O<br/>act · or · escalate"}}
        INTAKE["intake<br/>Nova 2 Lite"]
        DOCS["documents<br/>Nova 2 Lite"]
        SWARM["<b>eligibility swarm</b> — ambiguous cases only<br/>advocate Nova 2 Lite → verifier Nova Pro → referee Nova Micro<br/><i>three different models on purpose</i>"]
        DECIDE["decide<br/>Nova Pro<br/>the only node with action tools"]
    end

    subgraph state["Durable state"]
        DDB[("DynamoDB <code>grace-cases</code><br/>ledger · escalation queue · decisions<br/>PITR enabled")]
        MEM[("AgentCore Memory<br/>per-household facts<br/>365-day expiry")]
    end

    subgraph human["The human half"]
        DASH["Next.js dashboard on Amplify SSR<br/>sweep · queue · one case's audit trail"]
        COG["Cognito <code>grace-caseworkers</code><br/>admin-create only · custom:role"]
        CW(["caseworker"])
    end

    ALARM["CloudWatch alarm<br/><code>escalations &lt; 3</code>"]
    SMS["SMS channel<br/><i>sandboxed — transcript is the working path</i>"]

    EB --> SFN --> LAM --> INTAKE
    INTAKE --> DOCS
    DOCS -->|"ambiguous"| SWARM
    DOCS -.->|"clean case — skips the swarm"| DECIDE
    SWARM --> DECIDE
    DECIDE --> GATE
    GATE -->|"act — renewal filed"| DDB
    GATE -->|"escalate — a human decides"| DDB
    DECIDE -.-> SMS
    DECIDE <--> MEM
    DDB --> ALARM

    CW --> COG
    COG --> DASH
    DASH -->|"reads, server-side"| DDB
    DASH -->|"approve → re-invoke, gate re-runs"| LAM

    classDef gate fill:#B4530A,stroke:#7a3806,color:#fff
    classDef store fill:#E4E1D8,stroke:#b8b3a5,color:#1C1F23
    classDef human fill:#2F6F4E,stroke:#1f4a34,color:#fff
    class GATE gate
    class DDB,MEM store
    class CW,COG,DASH human
```

---

## What the diagram is claiming

### 1. The gate is the only thing that can permit an action

`authority.py` is pure Python — no model, no `boto3`, no `strands` import, no file or network I/O — and
a test greps for violations. It maps case facts to `act` or `escalate`, and `steering.py` adapts it
into a `SteeringHandler` that runs **before every state-changing tool call**.

Three layers enforce the boundary, strongest first:

1. **Capability absence.** `submit_renewal` is not in the tool list for nodes that must not file.
   `intake` and `documents` receive read tools only. There is nothing to disobey.
2. **Identity from the session.** Every household-scoped read tool takes **no arguments** — the case is
   bound at construction. A prompt injection cannot redirect Grace to another family because there is
   no parameter to poison.
3. **The deterministic gate.** Pure Python, exhaustively table-tested, and it fails closed: any error
   during verification escalates.

**Verified on the deployed system:** four consecutive sweeps returned 9 acted / 3 escalated, and
`renewal_submitted` appears in DynamoDB for exactly `c-001`–`c-009` and for none of
`c-010`/`c-011`/`c-012`.

### 2. Deliberation is real, and it only runs when it is needed

The dotted line from `documents` straight to `decide` is the conditional edge: nine of twelve
households never pay for the swarm. The three that do get an advocate arguing the family qualifies, a
verifier checking each claim adversarially, and a referee deciding whether it is genuinely ambiguous —
on **three different models**, because two instances of one model agreeing proves nothing and nothing
should referee its own argument.

The referee's conclusion is **appended** to the caseworker's brief, never substituted for the gate's
verdict. On a real run the referee concluded *CLEAR* for `c-012` and the case escalated anyway, because
`evaluate()` said `source_conflict`. That is the design working, not a bug.

### 3. A human's approval is an input to the gate, never a bypass

The dashboard's dotted line to the gate is labelled *never resumes a paused graph*, and that is the
sharpest thing in this diagram.

Resuming a paused agent with any truthy response **approves the blocked tool** — measured against the
real executor, `"needs review"` resumed a graph and filed a renewal for a household missing a required
document. So the deployed path has no resume at all. A caseworker's approval instead becomes a durable
`DECISION#` row, and Grace is re-invoked so the gate re-evaluates the **case facts**. Approving `c-010`
files nothing, because the document is still missing.

---

## What is deliberately not here

| Not built | Why |
|---|---|
| **AgentCore Gateway** | The largest remaining chunk and the most common deploy-day failure. The `target___tool` prefix bug that would let a gateway tool bypass the gate stays fixed and tested in `steering.py` regardless, so adding Gateway later cannot silently reopen it. |
| **Real SMS delivery** | The account is sandboxed: `MaxLimit: 1`, zero origination numbers, and Egypt sender-ID registration needs a letter of authorization, company registration, and a tax card. `TranscriptChannel` is the always-works path and the demo never depends on SMS. |
| **CloudWatch trace correlation** | Every ledger row carries a `trace_id` key, but its value is `NULL` in the deployed runtime: Runtime injects the OTEL environment variables without installing an in-process tracer provider, so zero spans exist. Fixing it would require `aws-opentelemetry-distro`, which is forbidden here. `NULL` is honest, and the DynamoDB escalation queue is the demo's evidence instead. |

**Three AgentCore surfaces are in use** — Runtime, Memory, and the deploy harness. Cognito adds
Identity as a fourth when Plan 3 ships. Never five.

---

## The observability claim worth making

The alarm is on **escalation count below three**, not on error rate.

The failure this system exists to prevent is *acting when it should have escalated*. That produces no
error, no throttle, and no latency spike — it looks exactly like success. Standard `SystemErrors`,
`Throttles`, and p99 latency alarms are worth having as hygiene, and **not one of them would have
caught any of the defects found while building this.** So the alarm watches the invariant instead: the
fixture set is twelve households, three of which must escalate, and a sweep that escalates fewer is a
gate that got looser.

Verified on real data: after a sweep, `Grace/EscalatedCases` published `Sum=3.0` and the alarm went
`OK`.
