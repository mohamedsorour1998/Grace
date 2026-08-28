import textwrap

import pytest

from grace.rules.pack import InvalidRulePack, load_pack


def test_loads_medicaid_ny():
    pack = load_pack("medicaid", "NY")
    assert pack.program == "medicaid"
    assert pack.certification_period_months == 12
    assert pack.window_opens_days_before_end == 60
    assert {d.doc_id for d in pack.required_documents} == {
        "proof_of_income",
        "proof_of_residency",
    }


def test_loads_snap_ny_with_shorter_cert_period():
    pack = load_pack("snap", "NY")
    assert pack.certification_period_months == 6
    assert pack.income_change_immaterial_pct == 10.0


def test_missing_pack_raises_rather_than_defaulting():
    with pytest.raises(InvalidRulePack):
        load_pack("wic", "NY")


@pytest.mark.parametrize(
    "program",
    ["../../../tmp/evil", "../packs/medicaid", "/etc/passwd", "", "medicaid/../snap"],
)
def test_pack_path_cannot_escape_the_pack_directory(program: str):
    """program/state reach load_pack from case records and, in Plan 2, a Gateway
    payload. An attacker-placed YAML outside PACKS_DIR could forge a deadline or
    declare no required documents, making the document gate vacuous."""
    with pytest.raises(InvalidRulePack):
        load_pack(program, "NY")


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("program", None, "must be a non-empty string"),
        ("program", '""', "must be a non-empty string"),
        ("state", None, "must be a non-empty string"),
        ("version", None, "must be a non-empty string"),
        ("version", "2026.10", "must be a non-empty string"),
        ("certification_period_months", None, "must be an int"),
        ("grace_period_days_after_end", "-10", "must be >= 0"),
        ("grace_period_days_after_end", "twelve", "must be an int"),
        ("grace_period_days_after_end", None, "must be an int"),
        ("window_opens_days_before_end", "true", "must be an int"),
        ("income_change_immaterial_pct", ".nan", "must be finite"),
        ("income_change_immaterial_pct", ".inf", "must be finite"),
        ("income_change_immaterial_pct", "-1.0", "must be >= 0"),
        ("required_documents", "[]", "non-empty list"),
        ("required_documents", None, "non-empty list"),
    ],
)
def test_malformed_pack_raises_one_exception_type(tmp_path, monkeypatch, key, value, expected):
    """Every failure mode raises InvalidRulePack so callers fail closed on one
    `except` rather than guessing between KeyError, TypeError, and ValueError.

    `value=None` means omit the key entirely.
    """
    import grace.rules.pack as pack_module

    fields = {
        "program": "medicaid",
        "state": "NY",
        "version": '"2026.1"',
        "certification_period_months": "12",
        "window_opens_days_before_end": "60",
        "grace_period_days_after_end": "90",
        "income_change_immaterial_pct": "5.0",
        "required_documents": "\n  - id: proof_of_income\n    max_age_days: 60",
    }
    if value is None:
        del fields[key]
    else:
        fields[key] = value
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").write_text(body)
    with pytest.raises(InvalidRulePack, match=expected):
        load_pack("medicaid", "NY")


@pytest.mark.parametrize("body", ["", "- a\n- b\n", "just a string\n"])
def test_non_mapping_pack_is_rejected(tmp_path, monkeypatch, body):
    """An empty or list-shaped YAML must not crash with TypeError."""
    import grace.rules.pack as pack_module

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").write_text(body)
    with pytest.raises(InvalidRulePack, match="must be a mapping"):
        load_pack("medicaid", "NY")


def test_unparseable_yaml_is_rejected(tmp_path, monkeypatch):
    import grace.rules.pack as pack_module

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").write_text("program: [unclosed\n")
    with pytest.raises(InvalidRulePack, match="Could not read"):
        load_pack("medicaid", "NY")


def test_directory_named_like_a_pack_is_rejected(tmp_path, monkeypatch):
    """A directory passes an `exists()` check but not `is_file()`."""
    import grace.rules.pack as pack_module

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").mkdir()
    with pytest.raises(InvalidRulePack, match="No rule pack"):
        load_pack("medicaid", "NY")


def test_mislabelled_pack_is_rejected(tmp_path, monkeypatch):
    """A pack whose own fields disagree with its filename would attribute one
    program's thresholds to another."""
    import grace.rules.pack as pack_module

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").write_text(
        textwrap.dedent(
            """
            program: snap
            state: NY
            version: "2026.1"
            certification_period_months: 6
            window_opens_days_before_end: 30
            grace_period_days_after_end: 30
            income_change_immaterial_pct: 10.0
            required_documents:
              - id: proof_of_income
                max_age_days: 30
            """
        )
    )
    with pytest.raises(InvalidRulePack, match="declares snap/NY"):
        load_pack("medicaid", "NY")


def test_duplicate_required_documents_are_rejected(tmp_path, monkeypatch):
    """Duplicate ids would let the stricter max_age_days be silently discarded."""
    import grace.rules.pack as pack_module

    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    (tmp_path / "medicaid-ny.yaml").write_text(
        textwrap.dedent(
            """
            program: medicaid
            state: NY
            version: "2026.1"
            certification_period_months: 12
            window_opens_days_before_end: 60
            grace_period_days_after_end: 90
            income_change_immaterial_pct: 5.0
            required_documents:
              - id: proof_of_income
                max_age_days: 60
              - id: proof_of_income
                max_age_days: 9999
            """
        )
    )
    with pytest.raises(InvalidRulePack, match="duplicate"):
        load_pack("medicaid", "NY")


def test_case_insensitive_lookup_returns_the_same_pack():
    assert load_pack("MEDICAID", "ny") == load_pack("medicaid", "NY")
