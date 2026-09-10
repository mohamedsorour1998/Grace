"""EVERY NUMBER IN A RULE PACK TRACES TO SOMETHING.

The packs encode a benefits programme's clocks: how long a certification lasts,
when the renewal window opens, how late is still savable, what income movement
is immaterial. Those numbers decide whether a family keeps coverage, and an
uncited number is indistinguishable from an invented one.

`authority: "policy choice"` is a valid and honest answer — many of these
parameters are choices a deployment makes within a range the regulation allows.
What is not valid is silence.
"""

from __future__ import annotations

import pytest

from grace.rules.pack import InvalidRulePack, load_pack

PACKS = [("medicaid", "NY"), ("snap", "NY")]

# The numeric parameters that decide a verdict. A source entry is required for
# each. Listed here rather than derived from the dataclass so that adding a
# parameter without citing it is a failure rather than an automatic pass.
CITED_PARAMETERS = frozenset({
    "certification_period_months",
    "window_opens_days_before_end",
    "grace_period_days_after_end",
    "income_change_immaterial_pct",
})


@pytest.mark.parametrize("program,state", PACKS)
def test_every_deciding_parameter_carries_a_source(program, state):
    pack = load_pack(program, state)
    cited = {s.parameter for s in pack.sources}
    missing = CITED_PARAMETERS - cited
    assert not missing, f"{program}-{state} has uncited parameters: {sorted(missing)}"


@pytest.mark.parametrize("program,state", PACKS)
def test_every_required_document_carries_a_source(program, state):
    pack = load_pack(program, state)
    cited = {s.parameter for s in pack.sources}
    for required in pack.required_documents:
        key = f"required_documents.{required.doc_id}"
        assert key in cited, f"{program}-{state}: {required.doc_id} has no source"


@pytest.mark.parametrize("program,state", PACKS)
def test_no_source_is_empty_or_a_placeholder(program, state):
    """A citation field filled with 'TBD' is worse than an absent one: it looks
    like diligence from a distance."""
    pack = load_pack(program, state)
    assert pack.sources, f"{program}-{state} cites nothing at all"
    for source in pack.sources:
        assert source.parameter.strip(), source
        assert source.authority.strip(), source
        for placeholder in ("tbd", "todo", "xxx", "fixme", "?"):
            assert placeholder not in source.authority.lower(), source


@pytest.mark.parametrize("program,state", PACKS)
def test_a_cited_parameter_actually_exists_in_the_pack(program, state):
    """A source for a parameter the pack does not have is a citation of
    nothing — the same defect as a docstring describing code that moved."""
    pack = load_pack(program, state)
    document_ids = {r.doc_id for r in pack.required_documents}
    for source in pack.sources:
        if source.parameter.startswith("required_documents."):
            assert source.parameter.split(".", 1)[1] in document_ids, source
        else:
            assert hasattr(pack, source.parameter), (
                f"{source.parameter} is cited but is not a field on RulePack"
            )


def test_a_malformed_sources_block_is_refused(tmp_path, monkeypatch):
    """`load_pack` raises `InvalidRulePack` and nothing else — Plan 1 Task 1's
    single-exception contract. A sources block that is a string, or whose entry
    is missing `authority`, must fail closed like every other malformed field
    rather than loading a pack with silent gaps."""
    import grace.rules.pack as pack_module

    real = (pack_module.PACKS_DIR / "medicaid-ny.yaml").read_text()
    # Cut the whole real `sources:` block off rather than string-replacing its
    # first line. A replace would leave the original entries dangling under a
    # scalar, which YAML refuses to parse at all — so the test would pass on a
    # *parser* error and never reach the branch it is written to prove. Same
    # vacuity as a test whose loop body never runs.
    head = real.split("sources:", 1)[0]
    assert head != real, "the real pack has no `sources:` block to remove"

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    target = tmp_path / "medicaid-ny.yaml"

    malformed = {
        "a string where a list belongs": "sources: 'not a list'\n",
        # These two are what make the list-type guard load-bearing rather than
        # decorative. A *string* still fails closed without it — iterating it
        # yields characters, which are refused as non-mappings. A number and a
        # null are not iterable at all, so without the guard `for entry in ...`
        # raises TypeError, which escapes `load_pack` and breaks the one thing
        # its callers rely on: that InvalidRulePack is the only exception type.
        "a number where a list belongs": "sources: 12\n",
        "an explicit null where a list belongs": "sources:\n",
        "an entry that is not a mapping": "sources:\n  - 'just a string'\n",
        "an entry missing authority": (
            "sources:\n  - parameter: certification_period_months\n"
        ),
        "an entry whose authority is empty": (
            "sources:\n  - parameter: certification_period_months\n"
            "    authority: ''\n"
        ),
        "an entry whose note is not a string": (
            "sources:\n  - parameter: certification_period_months\n"
            "    authority: '42 CFR 435.916(a)(1)'\n    note: 12\n"
        ),
        # Two entries for one parameter means one of them does not govern, and
        # which one wins depends on order — an auditor reading the pack would
        # see a citation that decides nothing.
        "two entries for one parameter": (
            "sources:\n  - parameter: certification_period_months\n"
            "    authority: '42 CFR 435.916(a)(1)'\n"
            "  - parameter: certification_period_months\n"
            "    authority: 'policy choice'\n"
        ),
    }
    for label, block in malformed.items():
        target.write_text(head + block)
        # The pack must be otherwise valid, or the raise proves nothing about
        # `sources` — every other field is copied verbatim from the real file.
        with pytest.raises(InvalidRulePack, match="source"):
            load_pack("medicaid", "NY")
            pytest.fail(f"{label} loaded without raising")

    # And the control: the same head with a well-formed block still loads, so
    # the five refusals above are attributable to the malformation rather than
    # to the truncation that produced them.
    target.write_text(
        head
        + "sources:\n  - parameter: certification_period_months\n"
        + "    authority: '42 CFR 435.916(a)(1)'\n    note: 'renewed once every 12 months'\n"
    )
    control = load_pack("medicaid", "NY")
    assert [s.parameter for s in control.sources] == ["certification_period_months"]
