# Claim vocabulary

The vocabulary is **closed**. An unknown claim type, an unknown field, or
a missing required field is a parse error with exit code 2 -- never a
skipped claim. A verifier that silently ignores what it does not
understand reports green for work it never looked at, which is the exact
failure this tool exists to catch.

A claims file is either a bare YAML list of claims, or a mapping with a
top-level `claims:` key. No third shape is guessed at.

Every claim accepts two universal fields: `note` (free text for a human)
and `id` (the caller's own handle, echoed in output).

## Filesystem

| type | required | optional | asserts |
|---|---|---|---|
| `path_exists` | `path` | `kind` (`any`\|`file`\|`dir`) | a path is present on disk |
| `path_absent` | `path` | | a path is NOT present |
| `path_moved` | `src`, `dst` | | src is gone and dst is present -- a move, not a copy |
| `file_contains` | `path`, `text` | `count` | a file exists and contains a literal string |
| `frontmatter_equals` | `path`, `key`, `value` | | a markdown file's YAML frontmatter field equals a value |
| `glob_count` | `pattern`, `count` | `root` | a glob matches exactly N paths |

## Process

| type | required | optional | asserts |
|---|---|---|---|
| `command_exits` | `cmd` | `code` (0), `cwd` (`.`), `timeout` (60) | a command runs and exits with the expected code |

## Git

| type | required | optional | asserts |
|---|---|---|---|
| `git_head_is` | `repo`, `sha` | | a repo's HEAD is a specific commit |
| `git_clean` | `repo` | `untracked` (false) | no uncommitted changes to tracked files |
| `git_pushed` | `repo` | `ref`, `remote` (`origin`) | HEAD is an ancestor of its remote ref |

`git_pushed` is the one that catches a lying write receipt: a tool can
return `status: ok` with a commit sha for a commit that never reached the
remote. Checking the working tree proves nothing about that; checking
ancestry against the remote ref does.

## Design notes

`text` in `file_contains` is a **literal substring**, not a regex. A
claim is supposed to be trivially checkable by a human reading it; a
regex is a second thing to get wrong.

`count` on `file_contains` is an exact occurrence count when present.
Omitted means "at least one".

`path_moved` deliberately checks both ends. A copy that left the source
in place is a different outcome from a move, and an agent that claims a
move should be held to one.
