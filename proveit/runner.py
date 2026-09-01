"""Run validated claims and preserve every verdict as evidence."""

from dataclasses import dataclass

from .checkers import CHECKERS, Verdict, command_policy
from .parse import Claim, ParseError, parse_claims


@dataclass(frozen=True)
class ClaimResult:
    claim: Claim
    verdict: Verdict

    def as_detail(self) -> dict:
        return {
            "index": self.claim.index,
            "id": self.claim.get("id"),
            "type": self.claim.type,
            "label": self.claim.label(),
            "status": self.verdict.status,
            "ok": self.verdict.ok,
            "evidence": self.verdict.evidence,
            "detail": self.verdict.detail,
        }


@dataclass(frozen=True)
class RunResult:
    results: list[ClaimResult]
    errors: list[ParseError]

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 0 if all(item.verdict.ok for item in self.results) else 1

    def as_detail(self, source: str = "claims") -> dict:
        return {
            "ok": self.exit_code == 0,
            "exit_code": self.exit_code,
            "errors": [error.render(source) for error in self.errors],
            "claims": [item.as_detail() for item in self.results],
        }


def verify_text(text: str, *, allowed_commands=()) -> RunResult:
    claims, errors = parse_claims(text)
    if errors:
        return RunResult([], errors)

    results = []
    with command_policy(allowed_commands):
        for claim in claims:
            checker = CHECKERS.get(claim.type)
            if checker is None:
                # Grammar/checker equality is pinned by tests, but a runtime hard
                # failure is still safer than silently skipping after packaging
                # or version skew.
                verdict = Verdict(
                    False,
                    f"no checker is installed for known type {claim.type!r}",
                    {"type": claim.type, "error": "checker_missing"},
                )
            else:
                verdict = checker(claim)
            results.append(ClaimResult(claim, verdict))
    return RunResult(results, [])
