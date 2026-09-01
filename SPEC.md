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
| `path_exists` | `path` | `kind` (`any`\|`file`\|`dir`), `stage` | a path is present |
| `path_absent` | `path` | `stage` | a path is NOT present |
| `path_moved` | `src`, `dst` | `stage` | src is gone and dst is present -- a move, not a copy |
| `file_contains` | `path`, `text` | `count`, `stage` | a file exists and contains a literal string |
| `frontmatter_equals` | `path`, `key`, `value` | `stage` | a markdown file's YAML frontmatter field equals a value |
| `glob_count` | `pattern`, `count` | `root`, `stage` | a glob matches exactly N paths |

All six filesystem checkers accept `stage` and resolve through the same
pushed/worktree boundary. A field that is accepted and then ignored would be
the silent pass this grammar exists to prevent, so the wiring test pins this.

## Resolution stage

**DEC-002, status `locked`.** Stephen ratified it on 2026-08-31 as part of
the approved prove-it brief. `DEFAULT_STAGE` in `proveit/grammar.py` is the
single implementation point for the default.

A filesystem claim asks about a path at a `stage`:

| stage | resolves from |
|---|---|
| `pushed` (default) | the path's content at `merge-base HEAD @{upstream}` |
| `worktree` | the path on local disk |

`pushed` is the default because the headline failure this tool exists to
catch is a lying write receipt -- `status: ok` and a sha for a commit that
never reached the remote (tickets H21, M51; incident 20.54). Against that
failure a working-tree read proves nothing: the file IS on disk with
exactly the claimed bytes, every claim passes, and the run is green for
work nobody else can see. `stage: worktree` asks that weaker question on
purpose.

Every checker reads through one seam, `checkers.content_at(path, stage)`,
so the default lives in one place rather than being re-decided per checker.

### Why merge-base and not the tracking ref itself

The question is how much of **this line of work** landed, not what else is
sitting on the remote. So a clone that is behind resolves at the
merge-base, and content pushed by someone else reads as not landed. That
is a false alarm, not a false pass -- the safe direction -- and the
verdict says `this clone is BEHIND it -- run git fetch/pull` so it cannot
be mistaken for the tool lying.

The tracking ref is a **local cache**. v1 makes no network calls (a brief
no-go), so a stale `origin/*` is the reader's `git fetch` to do.

### Two degradations, and the difference is the point

- **No enclosing git repo.** There is no landing to prove. The claim falls
  back to the working tree and every verdict says so
  (`no enclosing git repo -- fell back from pushed`). Sanctioned by
  DEC-002.
- **A repo, but nothing to compare against** -- no remote, no upstream, or
  no commits at all. There IS a landing to prove and it demonstrably has
  not happened. This does **not** fall back. The claim is unresolvable and
  fails loudly, naming `stage: worktree` as the deliberate way to ask the
  weaker question.

DEC-002 covers only the first case. The second is the builder's reading of
its logic -- degrading there would return green for precisely the failure
the decision was taken to catch. **It is also prove-it's own situation**
(R-001: this repo has no remote), so the tool cannot yet be pointed at
itself at the default stage.

### Known gap

An invalid `stage` value is not a parse error. The parser type-checks
fields but does not validate enumerated values, so `stage: banana` parses
and then fails at check time with `unknown stage 'banana'`. This matches
how `kind` on `path_exists` already behaves. Making enum values a loud
parse error is an M001 change and was left alone.

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

## Runner and CLI

`prove-it verify FILE` (or `python -m proveit verify FILE`) parses the whole
claims file before running anything. Parse/read failures exit 2, a disproved
claim exits 1, and all claims proved exits 0. Human output carries one evidence
line per claim; `--json` emits one document with the same exit code and the
checker detail objects intact. A missing checker is a hard failed verdict even
though the grammar/checker equality test should make it unreachable.

`FILE` may be `-` to read claims from standard input.

## Claim details

`text` in `file_contains` is a **literal substring**, not a regex. A
claim is supposed to be trivially checkable by a human reading it; a
regex is a second thing to get wrong.

`count` on `file_contains` is an exact occurrence count when present.
Omitted means "at least one".

`path_moved` deliberately checks both ends. A copy that left the source
in place is a different outcome from a move, and an agent that claims a
move should be held to one.
