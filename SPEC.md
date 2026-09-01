# Claim vocabulary

The vocabulary is **closed**. An unknown claim type, an unknown field, or
a missing required field is a parse error with exit code 2 -- never a
skipped claim. A verifier that silently ignores what it does not
understand reports green for work it never looked at, which is the exact
failure this tool exists to catch.

A claims file is either a bare YAML list of claims, or a mapping with a
top-level `claims:` key. No third shape is guessed at.

Every required string must contain at least one non-whitespace character.
An empty literal search or blank command would otherwise return a false green
while asserting nothing.

Every claim accepts two universal fields: `note` (free text for a human)
and `id` (the caller's own handle, echoed in output).

## Filesystem

| type | required | optional | asserts |
|---|---|---|---|
| `path_exists` | `path` | `kind` (`any`\|`file`\|`dir`), `stage` | a path is present |
| `path_absent` | `path` | `stage` | a path is NOT present |
| `path_moved` | `src`, `dst` | `stage` | src is gone, dst is present, and Git records that rename |
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
A local branch configured as an upstream is not a landing: the upstream must
resolve under `refs/remotes/`, or pushed-state resolution fails loudly.
In a detached-SHA checkout, one unambiguous non-symbolic remote-tracking ref
containing HEAD supplies that boundary. No match or multiple matches is
unresolvable rather than guessed, which keeps review/CI checkouts useful
without letting a local ref masquerade as publication.

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
| `command_exits` | `cmd` | `code` (0), `cwd` (`.`), `timeout` (60) | a caller-allowed executable runs and exits with the expected code |

`command_exits` is default-deny. Executable authority comes from the caller
(`prove-it verify ... --allow-command EXECUTABLE`), outside the claims file,
and cannot be widened by an agent-authored claim. The command string is split
into argv and executed with `shell=False`; redirection, pipelines and other
shell syntax are never interpreted. A future closeout adapter must preserve
that default-deny policy and choose its allowlist on the claimant host.
Relative allowlist entries are fixed against the caller's directory. A
relative claim executable is resolved against the claim's execution `cwd`,
compared as an absolute path, and then executed by that absolute path, so a
claim cannot redirect an allowed `./name` to a different same-named program.

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

`git_head_is.sha` must be a fixed hexadecimal object id or unambiguous
hexadecimal prefix. Symbolic revisions such as `HEAD`, `main`, or `HEAD~1`
are rejected because they move with the state they are supposed to prove.

`git_pushed` proves a remote-tracking ref belonging to the requested
`remote`. If `ref` is omitted, the current upstream must belong to that
remote; a local `refs/heads/*` ref or an upstream on another remote fails
with evidence instead of returning a false pass.

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

`file_contains` requires a regular file. A symlink's stored target name is
path metadata, not file content, so pushed and worktree symlinks fail with
evidence naming the link and target.

`kind: file` means a regular file, not merely an existing non-directory
entry. Worktree FIFOs, sockets, and devices are reported as special objects;
pushed Git gitlinks are reported the same way. They may satisfy an unqualified
`path_exists` claim, but never a regular-file claim.

`count` on `file_contains` is an exact occurrence count when present.
Omitted means "at least one".

`frontmatter_equals.value` is a finite YAML data tree: scalar values, lists,
and string-keyed mappings are supported. Recursive aliases, unsupported YAML
container types, and excessive nesting are refused as parse errors. The same
validation is applied to the observed frontmatter value before equality, so a
malformed recursive value produces an evidence-bearing failure, never a
traceback or recursive JSON document.

`path_moved` deliberately checks both ends **and** provenance. A copy that
left the source in place is not a move, but neither is a deletion beside an
unrelated pre-existing destination. Pushed claims require a matching Git
rename in the current destination's uninterrupted backward lineage; deleting
and recreating the destination breaks that lineage even when an older genuine
rename exists. Worktree claims accept a matching uncommitted Git rename or the
same lineage proof at HEAD. Outside one repository, endpoint state cannot
prove the transition, so the checker fails loudly instead of guessing.

Worktree absence and glob counts distinguish a missing entry from an
inspection failure. Permission and enumeration errors are unresolvable failed
verdicts; they can never satisfy `path_absent` or a zero-count glob.

Worktree existence is lexical: a dangling symlink is still a present
directory entry. It is reported as a symlink with its target and can never
satisfy `path_absent` merely because that target is missing.
Pushed resolution reads Git tree mode `120000` the same way, so a committed
symlink cannot satisfy a false `kind: file` claim merely because Git stores
its target text in a blob.

Repository discovery is alias-aware without following the claimed final
entry. A lexical path already under a repository stays inside that repository
even if an intermediate symlink points out; when no lexical repository exists,
a directory symlink or junction pointing into one is mapped back to its
physical repository-relative path. An alias therefore cannot trigger the
no-repo worktree fallback for an unpushed create, deletion, content edit,
frontmatter edit, or glob match.

If no enclosing lexical repository exists, a claimed non-symlink directory may
itself establish the repository root and resolves as relative path `""` at the
pushed commit. An enclosing lexical repository always wins over a nested Git
repository at the claimed path. A claimed final directory symlink remains a
lexical symlink unless it is explicitly the container of a glob claim.
