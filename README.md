# prove-it

Hold a completion claim against real state before believing it.

An agent says "done". `prove-it` asks it to say *what would be true if it
were done*, in a small closed grammar, and then checks each of those
statements against actual disk and git.

```yaml
# claims.yaml
- type: file_contains
  path: proveit/checkers.py
  text: "def check_git_pushed"
- type: git_pushed
  repo: .
  note: the write receipt said ok -- did it actually land?
```

```
$ prove-it verify claims.yaml
$ prove-it verify claims.yaml --json
```

Exit `0` every claim proved, `1` a claim failed, `2` the claims file was
malformed.

The versioned negative example is stable: its target deliberately omits a
sentinel that must remain absent, so adding a real checker cannot silently
reverse the example from FAIL to PASS.

```
$ prove-it verify examples/e3-false-claim.yaml
FAIL stable-negative-example -- looked for 'PROVE_IT_E3_SENTINEL_MUST_STAY_ABSENT' in the working tree -- file exists, 1 line, text not present
```

## "Done" means landed

A filesystem claim checks the file **as it exists at the last commit that
reached the remote**, not as it sits on your disk. The failure this tool
was built for is a write receipt that returns `ok` for a commit that never
got pushed -- and against that, a working-tree check passes every time,
because the file really is on disk with really the right bytes.

```yaml
- type: file_contains
  path: notes.md
  text: shipped
  stage: worktree   # opt in to the weaker question, on purpose
```

Outside a git repo the claim falls back to the working tree and says so.
Inside a repo with nothing to compare against -- no remote, no upstream,
no commits -- it refuses to answer rather than pass. `SPEC.md` has the
full rules; the call itself is DEC-002, locked 2026-08-31.

## Why

Completion claims are trusted at face value today. Nothing compares them
to reality. This is the layer that does.

See `SPEC.md` for the claim vocabulary, and the project record at
vault-house `20_projects/20.71-prove-it/` for purpose and decisions.

## Status

M001 (grammar + parser), M002 (all ten checkers), and M003 (runner + CLI) are
complete and tested. All six filesystem checkers resolve through the DEC-002
pushed-state layer; the process and Git checkers return evidence rather than
booleans. The CLI emits human evidence or one JSON document and preserves the
0/1/2 success/failure/malformed contract.

## Develop

```
python -m pip install -e .
python -m pytest tests/ -q
```
