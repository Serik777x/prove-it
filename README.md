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
```

Exit `0` every claim proved, `1` a claim failed, `2` the claims file was
malformed.

## Why

Completion claims are trusted at face value today. Nothing compares them
to reality. This is the layer that does.

See `SPEC.md` for the claim vocabulary, and the project record at
vault-house `20_projects/20.71-prove-it/` for purpose and decisions.

## Status

Early. M001 (grammar + parser) is complete and tested. M002 (checkers) is
in progress. See the project state file for exact position.

## Develop

```
python -m pytest tests/ -q
```
