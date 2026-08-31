"""The claim vocabulary.

CLOSED ON PURPOSE. An unknown claim type, an unknown field, or a missing
required field is a loud error -- never a skipped claim. A verifier that
silently ignores what it does not understand reports green for work it
never looked at, which is the exact failure this tool exists to catch.

The vocabulary is data, not code, so the parser, the CLI help and the
docs all read from one place and cannot drift apart.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

# Fields accepted on EVERY claim, carrying no verification meaning.
UNIVERSAL_FIELDS: dict[str, type] = {
    "note": str,   # free text for a human reading the claims file
    "id": str,     # caller's own handle for this claim, echoed in output
}


@dataclass(frozen=True)
class ClaimType:
    """One verifiable assertion shape."""

    name: str
    domain: str                      # fs | git | proc
    summary: str
    required: Mapping[str, type]
    optional: Mapping[str, Any] = field(default_factory=dict)

    @property
    def field_types(self) -> dict[str, type]:
        """Every accepted field mapped to its expected python type."""
        types = dict(UNIVERSAL_FIELDS)
        types.update(self.required)
        types.update({k: v[0] for k, v in self.optional.items()})
        return types

    @property
    def defaults(self) -> dict[str, Any]:
        return {k: v[1] for k, v in self.optional.items()}


def _t(name, domain, summary, required, optional=None) -> ClaimType:
    return ClaimType(name, domain, summary, required, optional or {})


# optional fields are {name: (type, default)}
_ALL = [
    _t("path_exists", "fs",
       "a path is present on disk",
       {"path": str},
       {"kind": (str, "any")}),                   # any | file | dir

    _t("path_absent", "fs",
       "a path is NOT present on disk",
       {"path": str}),

    _t("path_moved", "fs",
       "src is gone and dst is present -- a move, not a copy",
       {"src": str, "dst": str}),

    _t("file_contains", "fs",
       "a file exists and contains a literal string",
       {"path": str, "text": str},
       {"count": (int, None)}),                   # exact occurrence count

    _t("frontmatter_equals", "fs",
       "a markdown file's YAML frontmatter field equals a value",
       {"path": str, "key": str, "value": object}),

    _t("glob_count", "fs",
       "a glob matches exactly N paths",
       {"pattern": str, "count": int},
       {"root": (str, ".")}),

    _t("command_exits", "proc",
       "a command runs and exits with the expected code",
       {"cmd": str},
       {"code": (int, 0), "cwd": (str, "."), "timeout": (int, 60)}),

    _t("git_head_is", "git",
       "a repo's HEAD is a specific commit",
       {"repo": str, "sha": str}),

    _t("git_clean", "git",
       "a repo has no uncommitted changes to tracked files",
       {"repo": str},
       {"untracked": (bool, False)}),             # also require no untracked

    _t("git_pushed", "git",
       "HEAD is an ancestor of its remote ref -- the work actually LANDED",
       {"repo": str},
       {"ref": (str, None), "remote": (str, "origin")}),
]

CLAIM_TYPES: dict[str, ClaimType] = {t.name: t for t in _ALL}

KNOWN_TYPES: tuple[str, ...] = tuple(sorted(CLAIM_TYPES))
