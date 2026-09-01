"""Parse and validate a claims file.

Collects EVERY error rather than raising on the first one. An agent that
wrote a claims file with four typos should learn about four typos in one
run -- a parser that stops at the first turns a single fix-up into four
round trips.
"""

from dataclasses import dataclass
from typing import Any

import yaml

from .grammar import CLAIM_TYPES, KNOWN_TYPES, ClaimType


@dataclass(frozen=True)
class Claim:
    """A single validated assertion, defaults already filled in."""

    index: int          # position in the claims file, 0-based
    type: str
    fields: dict[str, Any]

    @property
    def spec(self) -> ClaimType:
        return CLAIM_TYPES[self.type]

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self.fields[name]

    def label(self) -> str:
        """Short human handle used in output lines."""
        if self.fields.get("id"):
            return str(self.fields["id"])
        for key in ("path", "src", "pattern", "cmd", "repo"):
            if key in self.fields:
                return f"{self.type} {self.fields[key]}"
        return self.type


@dataclass(frozen=True)
class ParseError:
    """One reason a claims file was rejected."""

    message: str
    index: int | None = None    # None = whole-document problem

    def render(self, source: str = "claims") -> str:
        where = source if self.index is None else f"{source}[{self.index}]"
        return f"{where}: {self.message}"


def _type_name(t: type) -> str:
    return {str: "string", int: "integer", bool: "boolean"}.get(t, t.__name__)


def _validate_one(index: int, raw: Any) -> tuple[Claim | None, list[ParseError]]:
    errors: list[ParseError] = []

    if not isinstance(raw, dict):
        got = type(raw).__name__
        return None, [ParseError(f"claim must be a mapping, got {got}", index)]

    if "type" not in raw:
        return None, [ParseError(
            f"claim is missing 'type'. known types: {', '.join(KNOWN_TYPES)}",
            index)]

    name = raw["type"]
    if not isinstance(name, str):
        return None, [ParseError(
            f"claim field 'type' must be string, got {type(name).__name__}",
            index)]
    spec = CLAIM_TYPES.get(name)
    if spec is None:
        return None, [ParseError(
            f"unknown claim type {name!r}. known types: {', '.join(KNOWN_TYPES)}",
            index)]

    accepted = spec.field_types

    for missing in sorted(set(spec.required) - set(raw)):
        errors.append(ParseError(
            f"{name} missing required field {missing!r}", index))

    for extra in sorted(set(raw) - set(accepted) - {"type"}):
        errors.append(ParseError(
            f"{name} has unknown field {extra!r}. "
            f"accepted: {', '.join(sorted(accepted))}", index))

    for key, value in raw.items():
        if key == "type" or key not in accepted:
            continue
        expected = accepted[key]
        if expected is object:
            continue
        # bool is a subclass of int -- never let one satisfy the other
        mismatched = not isinstance(value, expected) or (
            expected is int and isinstance(value, bool))
        if mismatched:
            errors.append(ParseError(
                f"{name} field {key!r} must be {_type_name(expected)}, "
                f"got {type(value).__name__}", index))
        elif key in spec.required and expected is str and not value.strip():
            errors.append(ParseError(
                f"{name} field {key!r} must be a non-empty string", index))
        elif isinstance(value, str) and "\0" in value:
            errors.append(ParseError(
                f"{name} field {key!r} must not contain NUL", index))

    if errors:
        return None, errors

    fields = dict(spec.defaults)
    fields.update({k: v for k, v in raw.items() if k != "type"})
    return Claim(index=index, type=name, fields=fields), []


def parse_claims(text: str) -> tuple[list[Claim], list[ParseError]]:
    """Parse claims YAML into validated claims plus every error found.

    Accepts either a bare list of claims, or a mapping with a top-level
    ``claims:`` key. Anything else is rejected -- guessing at a third
    shape is how a malformed file gets silently half-read.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], [ParseError(f"not valid YAML: {exc}")]

    if doc is None:
        return [], [ParseError("claims file is empty")]

    if isinstance(doc, dict):
        if "claims" not in doc:
            return [], [ParseError(
                "mapping form must have a top-level 'claims' key")]
        doc = doc["claims"]

    if not isinstance(doc, list):
        return [], [ParseError(
            f"claims must be a list, got {type(doc).__name__}")]

    if not doc:
        return [], [ParseError("claims file declares no claims")]

    claims: list[Claim] = []
    errors: list[ParseError] = []
    for index, raw in enumerate(doc):
        claim, claim_errors = _validate_one(index, raw)
        if claim is not None:
            claims.append(claim)
        errors.extend(claim_errors)

    return claims, errors
