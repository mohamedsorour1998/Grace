# Lessons: the gate, the case store, and the tools (Plan 1, Tasks 1–5)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### What Task 1 established — follow these

**`load_pack` raises `InvalidRulePack` and nothing else.** One exception type for missing,
unreadable, malformed, mislabelled, and out-of-range packs, so a caller fails closed with a
single `except InvalidRulePack`. Task 4's `check_window` must wrap it; Task 3's `evaluate` must
not rely on `pack is None` alone, because a *partially* corrupt pack never reaches that check.
This also covers a non-string `program`/`state` now — see Task 2 below.

**Rule-pack input is untrusted.** `program`/`state` reach `load_pack` from case records and, in
Plan 2, from a Gateway payload. Path containment, a non-empty `required_documents`, and finite
numeric thresholds are all enforced there — an empty document list would make
`missing_document` unreachable, and a `NaN` threshold disables the income check silently because
every comparison against `NaN` is `False`. Do not relax these.

**`overdue` and `in_grace` are both actionable.** Grace *does* file a late renewal inside the
grace period — that is the procedural save it exists to make. Only `not_open` and `closed`
escalate on window grounds. The two are distinguished for the caseworker briefing, not for the
gate.

**Pin the date.** Every test module uses `TODAY = date(2026, 10, 1)`. A `date.today()` anywhere in
the sweep degrades the demo **from 2026-10-16**, when `c-002`'s `proof_of_income` goes stale — not
from 2026-10-31 when its SNAP window closes, which is 15 days later and was the number this file
carried for five plans. By 2026-10-30 the split is **6/6**, not 8/4. `tests/test_demo_dates.py` pins
every fixture's degradation date so this cannot drift again. Task 6's CLI takes `--today` defaulting
to the pinned value.

### What Task 2 established — follow these

**`reported_income_cents`/`reported_size` are `int | None`, and `None` means "not reported."**
Not the household's on-file value, and never `0` — `0` is a real income a family can report
(a genuine loss of all income is the single most eligibility-relevant case Grace will see), so
it cannot double as an absence marker. **Task 3's `evaluate` must treat `None` as "no income or
size check applies" and short-circuit before comparing** — do not compute a percentage change
or an inequality against `None`. See the note at the top of Task 3 in the plan.

**`LedgerEntry.detail` is an immutable, type-checked mapping, not a plain `dict`.** Values are
restricted to JSON-safe scalars (`str | int | float | bool | None`) and the mapping itself is
frozen at construction. A caller holding a `ledger()` result cannot rewrite an audit entry —
Task 8's evals read the ledger as ground truth, so a mutable `detail` would let something
retroactively change what the eval sees. `LedgerEntry.at` must be a timezone-aware `datetime`;
a naive one raises at construction, before it can end up unsortable next to an aware one.

**Fixture and fail-closed loader conventions carry forward.** `load_fixture_cases` raises
`InvalidFixtureData` (parallel to `InvalidRulePack`) for a non-string field or a malformed
`source_conflicts`, rather than silently coercing. Quote every YAML scalar in new fixtures —
an unquoted `no`/`yes`/`on`/`off` parses as a boolean, and an unquoted phone number as an int.

### What Task 3 established — follow these

**Never select "the" document for a `doc_id` by record order.** A household can have multiple
copies of the same document on file (a re-submission leaving the old one in place). Selecting
by position — including a naive `{d.doc_id: d for d in documents}` dict comprehension, which
is last-wins by *order*, not by which copy is actually newest — makes the verdict depend on how
the record happened to load. This was a real, confirmed bug: two documents with an identical
`received` date and different `expires` produced opposite verdicts purely from tuple order.
Selection must be a deterministic function of the data (see `_most_recent` in
`grace/authority.py`), and a duplicate may only make a verdict *stricter*, never looser.

**Every failing condition gets its own reason — never `elif` between independent checks.** A
document can be both stale-by-age and past its own `expires` at the same time; both facts must
reach the caseworker brief. If you add a new condition to the gate, ask whether it can co-occur
with an existing one and use `if`, not `elif`, unless the conditions are genuinely mutually
exclusive.

**`evaluate` can still raise `ValueError` or `TypeError`, and that is correct — the caller must
catch broadly.** A structurally invalid `RulePack` (bypassing `load_pack`'s own validation via
direct construction) makes `renewal_window` raise, or a missing threshold raises a different
type from the same underlying cause. Converting either to a `GateResult` would be worse: a
`verification_error` result is indistinguishable from a normal escalation at the call site, so
a caller that logs and moves on would silently treat pack corruption as routine. **Task 5's
`steer_before_tool` and Task 6's sweep must wrap the `evaluate` call in `except Exception`, not
`except ValueError`** — catching only one exception type leaves the other to escape the
steering handler into the agent loop.

**`GateReason.detail` carries untrusted free text and is deliberately unescaped.**
`source_conflicts` is case-record text surfaced verbatim, including as a potential
prompt-injection vector into whatever agent eventually reads it (the caseworker briefer is
agents-as-tools, per the architecture). `authority.py` has no rendering context to know the
right escaping strategy — HTML for a UI, parameterization for DynamoDB, something else again
for a model prompt — so it does not escape at all. Whichever surface renders `detail` is
responsible for escaping it there. Do not add escaping logic to `authority.py`.

**Reason order is not a contract.** `GateResult.reasons` follows check order, which has already
changed once. Do not surface `reasons[0]` as "the" reason for a case (e.g. in a span attribute
or a briefing) without picking deliberately — compare on `.code`, or treat `reasons` as a set.

### What Task 4 established — follow these

**Read tools share `_most_recent` with the gate — never a second dict comprehension.**
`grace/tools/read.py`'s `list_documents` imports `_most_recent` from `grace.authority` directly,
rather than reimplementing document selection. `list_documents` is what a model reads before
deciding whether to call `submit_renewal`; `evaluate` is what actually permits it. If the two
selected different copies of the same `doc_id`, the model would reason from facts the gate does
not share, and the disagreement would be invisible — no error, tool says "current," gate says
"stale." A duplicated implementation can drift out of sync; an import cannot.

**A read tool that catches only `(InvalidRulePack, ValueError)` still fails open.** `load_pack`
can return a pack whose values are individually valid but whose date arithmetic overflows —
`grace_period_days_after_end: 999999999` loads cleanly, then `renewal_window` raises
`OverflowError`, a third exception type from the same underlying cause Task 3 already warned
about. `check_window` now catches `Exception` broadly, matching the discipline Task 3 already
mandates for `evaluate`'s callers. If you add a new read tool that calls `renewal_window`,
catch broadly there too — narrowing to "the exceptions I've seen so far" is how this bug
happened the first time.

**A `Channel`'s `send()` return value must be coerced to `str` before it reaches the ledger.**
`Channel` is a plain `Protocol`, not `@runtime_checkable`, so its `-> str` annotation is
enforced by nothing. Plan 2's real SNS implementation will naturally return a boto3 response
shape (a dict), which `LedgerEntry`'s scalar-only contract rejects — *after* the message has
already been sent. That is hard rule 6 inverted: the family was contacted and the audit trail
says nothing happened. `send_family_message` wraps the call in `str(...)` for exactly this
reason; any new action tool that logs a channel's return value must do the same.

**A no-argument tool spec is not enforced by rejection — it is enforced by absence.** Verified
against the real invocation path (`tool.stream()`, not a direct Python call): an injected
`{"case_id": "c-002"}` argument on a zero-argument tool is *silently discarded*, and the call
succeeds against the bound case. There is no error, no signal that an injection was attempted —
it looks identical to a normal call. CLAUDE.md's "no parameter to poison" phrasing is correct
about the outcome and imprecise about the mechanism: nothing validates and rejects an
injected argument, because the parameter simply does not exist for `strands` to bind it to.

**Closure-cell mutation is a real gap, and deliberately not defended against.** `case_id` and
`store` are ordinary Python closure variables; rewriting `read_case.__closure__[i].cell_contents`
redirects the tool (confirmed). This requires arbitrary code execution in the agent's own
process, which is a different threat class than prompt injection — an attacker who can rewrite
closure cells can equally reach `store` and `evaluate` directly, so no binding strategy in
`read.py` would close this. Do not attempt to harden the closure; the boundary that matters is
the process boundary, not the variable-binding strategy inside it.

**`_UNVERIFIABLE` was a courtesy string with nothing behind it, until Task 5.** Before Task 5,
no code outside `test_authority.py` called `evaluate` at all — a read tool returning
`_UNVERIFIABLE` did not, by itself, force anything. Task 5's `AuthorityGate` is what turns it
into enforcement: it evaluates independently on the same case before every action tool, so the
string is now a hint to the model, not the mechanism.

**Model-ID guards must scan the whole package, not a list of modules someone remembered.**
`pkgutil.walk_packages` over `grace/`, not a hardcoded tuple — confirmed a real Claude
inference-profile ID inlined into `read.py` passed all 157 tests before this fix, because the
non-Nova-vendor check ran against `models.py` only. Any new module (`grace/steering.py`,
`grace/ledger.py` from Task 5) is covered automatically because the test discovers modules from
disk, not because anyone remembered to add them.

### What Task 5 established — follow these

**A `SteeringHandler`'s exception is swallowed, not propagated — verify this before touching
`steer_before_tool` again.** `SteeringHandler.provide_tool_steering_guidance` (the SDK's own
dispatcher — read its source, do not take this on faith) wraps the call to `steer_before_tool`
in `except Exception: return`, logs at debug level, and leaves `cancel_tool` unset. Confirmed
empirically: a handler that raises produces `cancel_tool == False`, and the tool executes
*ungated*. This is why every fallible call inside `steer_before_tool` — including `evaluate`
itself, which can raise `ValueError`, `TypeError`, or `OverflowError` from a pack that loaded
cleanly — sits inside one `except Exception` that returns an `Interrupt`. A narrower `except`
here is not merely incomplete; it is silent fail-open on the one method whose entire purpose is
failing closed, and nothing in the agent loop will tell you it happened.

**`submit_renewal` and `send_family_message` are gated on different questions.** Filing needs a
fully clean `evaluate()` verdict. Outreach needs only that every reason is `missing_document` or
`stale_document` (`DOCUMENT_ONLY_CODES` in `grace/steering.py`) — a case that is *also* off on
income, size, or a source conflict must still escalate, because texting the family does not
resolve an eligibility question. If you add a new action tool, decide explicitly which of these
two questions it answers; do not assume "gated" means "needs the same verdict as filing."

**Two different `Interrupt` classes exist — do not confuse them.**
`strands.vended_plugins.steering.Interrupt` (what `steer_before_tool` returns; `type`/`reason`
only, no `.id`) is unrelated to `strands.interrupt.Interrupt` (the multi-agent resume type from
Appendix B.1, with `id`/`name`/`reason`/`response`, used to resume a paused `Graph`/`Swarm`).
They share a name and nothing else.

**The ledger is asymmetric between `Guide` and `Interrupt` — Task 8's evals must account for
this, not discover it.** On `Guide`, the SDK builds a synthetic error `ToolResult` and fires
`AfterToolCallEvent`, pairing `tool_call` with `tool_result` in the ledger. On `Interrupt`, the
SDK yields a `ToolInterruptEvent` and returns *before* the after-hook, so the ledger gets
`tool_call` with **no paired result**. An eval that reads an unpaired `tool_call` as "a tool ran
and was not logged" is backwards for an escalated case: it means the tool did not run. This is
SDK behavior, not a choice made here, and it is pinned in `tests/test_steering.py` rather than
worked around.

**`AuthorityGate._seen` is per-instance, in-memory, and does not survive a fresh process.** It
cannot drift from the ledger on the read path — both are driven off the same
`BeforeToolCallEvent`. **Task 6 tested the resumed-run case and it is fine in-process:**
`Graph._build_node_input` restores the node executor's `messages`, `state`, `_interrupt_state`,
and `_model_state` on resume but never touches the plugin registry, so the same `AuthorityGate`
instance is reused and `_seen` still holds every prior read (verified against a real graph
resume, and pinned by `test_the_gates_seen_set_survives_an_in_process_resume`). A resume in a
*new* process (Plan 2, via a session manager) would start with an empty `_seen` — still not
fail-open, since an empty `_seen` makes the gate stricter, but it would `Guide` once before
proceeding.
