"""Parse and validate a claims file.

Collects EVERY error rather than raising on the first one. An agent that
wrote a claims file with four typos should learn about four typos in one
run -- a parser that stops at the first turns a single fix-up into four
round trips.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yaml

from .grammar import CLAIM_TYPES, KNOWN_TYPES, ClaimType


class UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that refuses an ambiguous mapping before it reaches checks."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found an unhashable mapping key: {exc}", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def safe_load_unique(text: str):
    """Load trusted YAML types while rejecting duplicate mapping keys."""
    return yaml.load(text, Loader=UniqueKeySafeLoader)


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


def yaml_tree_error(value: Any, *, max_depth: int = 100) -> str | None:
    """Return why a value is not a finite, comparison-safe YAML data tree."""
    scalar_types = (type(None), bool, int, float, str, date, datetime)
    active: set[int] = set()
    stack: list[tuple[str, Any, str, int]] = [("enter", value, "value", 0)]

    while stack:
        action, current, where, depth = stack.pop()
        if action == "exit":
            active.remove(id(current))
            continue
        if isinstance(current, scalar_types):
            continue
        if not isinstance(current, (list, dict)):
            return f"{where} has unsupported YAML type {type(current).__name__}"
        if depth > max_depth:
            return f"{where} exceeds maximum supported nesting depth {max_depth}"
        identity = id(current)
        if identity in active:
            return f"{where} contains a recursive YAML alias"
        active.add(identity)
        stack.append(("exit", current, where, depth))
        if isinstance(current, dict):
            for key, child in reversed(list(current.items())):
                if not isinstance(key, str):
                    return (f"{where} has unsupported non-string mapping key "
                            f"of type {type(key).__name__}")
                stack.append(("enter", child, f"{where}.{key}", depth + 1))
        else:
            for index in range(len(current) - 1, -1, -1):
                stack.append(("enter", current[index],
                              f"{where}[{index}]", depth + 1))
    return None


def yaml_values_equal(actual: Any, expected: Any) -> bool:
    """Compare validated YAML trees without Python's cross-type coercions."""
    pending = [(actual, expected)]
    while pending:
        left, right = pending.pop()
        if type(left) is not type(right):
            return False
        if isinstance(left, list):
            if len(left) != len(right):
                return False
            pending.extend(zip(left, right, strict=True))
        elif isinstance(left, dict):
            if left.keys() != right.keys():
                return False
            pending.extend((value, right[key])
                           for key, value in left.items())
        elif left != right:
            return False
    return True


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
            problem = yaml_tree_error(value)
            if problem:
                errors.append(ParseError(
                    f"{name} field {key!r} is not a supported YAML value: "
                    f"{problem}", index))
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
        doc = safe_load_unique(text)
    except yaml.YAMLError as exc:
        return [], [ParseError(f"not valid YAML: {exc}")]
    except RecursionError:
        return [], [ParseError("YAML nesting exceeds the parser limit")]

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
