"""M001 -- the parser must be loud about everything it does not understand."""

import pytest

from proveit.grammar import CLAIM_TYPES, KNOWN_TYPES
from proveit.parse import parse_claims


def errs(text):
    _, errors = parse_claims(text)
    return [e.message for e in errors]


class TestShape:
    def test_bare_list_form(self):
        claims, errors = parse_claims(
            "- type: path_exists\n  path: README.md\n")
        assert errors == []
        assert len(claims) == 1
        assert claims[0].type == "path_exists"

    def test_mapping_form(self):
        claims, errors = parse_claims(
            "claims:\n  - type: path_exists\n    path: README.md\n")
        assert errors == []
        assert len(claims) == 1

    def test_mapping_without_claims_key_rejected(self):
        assert "mapping form must have a top-level 'claims' key" in errs(
            "assertions:\n  - type: path_exists\n    path: x\n")

    def test_empty_file_rejected(self):
        assert errs("") == ["claims file is empty"]

    def test_empty_list_rejected(self):
        assert errs("[]") == ["claims file declares no claims"]

    def test_scalar_document_rejected(self):
        assert "claims must be a list, got str" in errs("just a string\n")

    def test_bad_yaml_reported_not_raised(self):
        messages = errs("- type: [unclosed\n")
        assert len(messages) == 1
        assert messages[0].startswith("not valid YAML")

    def test_non_mapping_claim_rejected(self):
        assert "claim must be a mapping, got str" in errs("- just-a-string\n")


class TestClosedVocabulary:
    def test_unknown_type_is_an_error(self):
        messages = errs("- type: file_smells_right\n  path: README.md\n")
        assert any("unknown claim type 'file_smells_right'" in m
                   for m in messages)

    def test_unknown_type_error_lists_known_types(self):
        message = errs("- type: nope\n")[0]
        for name in KNOWN_TYPES:
            assert name in message

    def test_missing_type_is_an_error(self):
        assert any("missing 'type'" in m for m in errs("- path: README.md\n"))

    def test_unknown_field_is_an_error(self):
        # the whole point: a typo'd field must not silently pass
        messages = errs("- type: path_exists\n  paht: README.md\n")
        assert any("unknown field 'paht'" in m for m in messages)

    def test_unknown_field_does_not_yield_a_claim(self):
        claims, errors = parse_claims(
            "- type: path_exists\n  path: a\n  extra: b\n")
        assert claims == []
        assert errors


class TestRequiredFields:
    def test_missing_required_field(self):
        messages = errs("- type: file_contains\n  path: README.md\n")
        assert "file_contains missing required field 'text'" in messages

    def test_every_missing_field_reported_at_once(self):
        messages = errs("- type: path_moved\n")
        assert "path_moved missing required field 'src'" in messages
        assert "path_moved missing required field 'dst'" in messages

    def test_universal_fields_always_accepted(self):
        claims, errors = parse_claims(
            "- type: path_exists\n  path: a\n  note: why\n  id: c1\n")
        assert errors == []
        assert claims[0].get("note") == "why"

    @pytest.mark.parametrize("body,field", [
        ("- type: file_contains\n  path: README.md\n  text: ''\n", "text"),
        ("- type: command_exits\n  cmd: '   '\n", "cmd"),
    ])
    def test_required_strings_must_not_be_blank(self, body, field):
        messages = errs(body)
        assert any(f"field '{field}' must be a non-empty string" in message
                   for message in messages)


class TestFieldTypes:
    def test_wrong_type_rejected(self):
        messages = errs("- type: glob_count\n  pattern: '*.py'\n  count: many\n")
        assert "glob_count field 'count' must be integer, got str" in messages

    def test_bool_does_not_satisfy_integer(self):
        # bool subclasses int in python; a claim of `count: true` is a mistake
        messages = errs("- type: glob_count\n  pattern: '*.py'\n  count: true\n")
        assert any("must be integer, got bool" in m for m in messages)

    def test_frontmatter_value_accepts_any_type(self):
        claims, errors = parse_claims(
            "- type: frontmatter_equals\n  path: a.md\n  key: status\n"
            "  value: 3\n")
        assert errors == []
        assert claims[0]["value"] == 3


class TestDefaults:
    def test_defaults_are_filled(self):
        claims, _ = parse_claims("- type: command_exits\n  cmd: 'true'\n")
        assert claims[0]["code"] == 0
        assert claims[0]["timeout"] == 60

    def test_explicit_value_beats_default(self):
        claims, _ = parse_claims(
            "- type: command_exits\n  cmd: 'false'\n  code: 1\n")
        assert claims[0]["code"] == 1


class TestErrorAccounting:
    def test_all_claims_reported_not_just_the_first(self):
        text = ("- type: nope\n"
                "- type: file_contains\n  path: a\n"
                "- type: path_exists\n  paht: b\n")
        _, errors = parse_claims(text)
        assert {e.index for e in errors} == {0, 1, 2}

    def test_good_claims_survive_alongside_bad_ones(self):
        text = ("- type: path_exists\n  path: good.md\n"
                "- type: nope\n")
        claims, errors = parse_claims(text)
        assert [c.index for c in claims] == [0]
        assert [e.index for e in errors] == [1]

    def test_render_names_the_position(self):
        _, errors = parse_claims("- type: nope\n")
        assert errors[0].render("claims.yaml").startswith("claims.yaml[0]:")

    def test_document_level_error_has_no_index(self):
        _, errors = parse_claims("")
        assert errors[0].render("claims.yaml") == "claims.yaml: claims file is empty"


class TestGrammarIntegrity:
    @pytest.mark.parametrize("name", KNOWN_TYPES)
    def test_every_type_has_a_summary_and_domain(self, name):
        spec = CLAIM_TYPES[name]
        assert spec.summary
        assert spec.domain in {"fs", "git", "proc"}

    @pytest.mark.parametrize("name", KNOWN_TYPES)
    def test_required_and_optional_never_overlap(self, name):
        spec = CLAIM_TYPES[name]
        assert not set(spec.required) & set(spec.optional)

    @pytest.mark.parametrize("name", KNOWN_TYPES)
    def test_no_type_shadows_a_universal_field(self, name):
        spec = CLAIM_TYPES[name]
        assert not set(spec.required) & {"note", "id"}
