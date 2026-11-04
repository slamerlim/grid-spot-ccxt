"""Bounded chronological studies of declared spacing candidates, never live tuning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from grid.domain.numbers import DomainError, arithmetic, bounded_int, decimal, identifier
from grid.persistence import codec
from grid.research.artifacts import canonical, digest
from grid.research.comparison import manifests, number
from grid.research.history import MINUTE, Manifest, identity, validate
from grid.research.replay import MODELS, Model, Replay
from grid.research.replay import run as replay

MAX_RUNS = 128
MAX_BAR_STEPS = 2_000_000
MAX_RESULT_BYTES = 64 * 1024**2
REGIMES = ("up", "down", "sideways", "unclassified")
SELECTION = "maximum_training_mark_to_close_return_then_lexicographic_candidate_name"


@dataclass(frozen=True)
class Candidate:
    name: str
    spacing: Decimal

    def __post_init__(self) -> None:
        identifier(self.name)
        decimal(self.spacing, positive=True)


@dataclass(frozen=True)
class Scenario:
    name: str
    model: Model
    latency_bars: int
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal

    def __post_init__(self) -> None:
        identifier(self.name)
        if self.model not in MODELS:
            raise DomainError("unsupported study fill convention")
        bounded_int(self.latency_bars, 1, 60)
        for rate in (self.buy_fee_rate, self.sell_fee_rate):
            decimal(rate)
            if rate >= 1:
                raise DomainError("unsupported study fee rate")


@dataclass(frozen=True)
class Fold:
    name: str
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int
    train_regime: str
    test_regime: str

    def __post_init__(self) -> None:
        identifier(self.name)
        for value in (self.train_start_ms, self.train_end_ms, self.test_start_ms, self.test_end_ms):
            bounded_int(value, MINUTE, 253402300740000)
            if value % MINUTE:
                raise DomainError("study windows require exact UTC minutes")
        if (
            self.train_end_ms - self.train_start_ms < 2 * MINUTE
            or self.test_end_ms - self.test_start_ms < 2 * MINUTE
            or self.train_end_ms > self.test_start_ms
        ):
            raise DomainError("training must precede testing with at least two bars each")
        if self.train_regime not in REGIMES or self.test_regime not in REGIMES:
            raise DomainError("unsupported descriptive study regime")


@dataclass(frozen=True)
class Plan:
    candidates: tuple[Candidate, ...]
    scenarios: tuple[Scenario, ...]
    folds: tuple[Fold, ...]
    universe_evidence: str
    regime_evidence: str

    def __post_init__(self) -> None:
        for rows, maximum in ((self.candidates, 16), (self.scenarios, 8), (self.folds, 8)):
            bounded_int(len(rows), 1, maximum)
            if len({row.name for row in rows}) != len(rows):
                raise DomainError("duplicate study member name")
        if 2 * len(self.candidates) * len(self.scenarios) * len(self.folds) > MAX_RUNS:
            raise DomainError("study replay count cap exceeded")
        for evidence in (self.universe_evidence, self.regime_evidence):
            if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4096:
                raise DomainError("bounded study evidence declarations required")
        folds = sorted(self.folds, key=lambda f: f.test_start_ms)
        for previous, following in zip(folds, folds[1:], strict=False):
            if (
                previous.test_end_ms > following.test_start_ms
                or previous.train_end_ms > following.train_end_ms
            ):
                raise DomainError("study test windows overlap or training moves backward")

    def checked(self, spec: Replay) -> Plan:
        steps = 0
        for fold in self.folds:
            if fold.train_start_ms < spec.start_ms or fold.test_end_ms > spec.end_ms:
                raise DomainError("study fold lies outside configured source window")
            steps += (fold.train_end_ms - fold.train_start_ms) // MINUTE
            steps += (fold.test_end_ms - fold.test_start_ms) // MINUTE
        steps *= len(self.candidates) * len(self.scenarios) * len(spec.allocations)
        if steps > MAX_BAR_STEPS:
            raise DomainError("study aggregate bar step cap exceeded")
        return replace(
            self,
            candidates=tuple(sorted(self.candidates, key=lambda c: c.name)),
            scenarios=tuple(sorted(self.scenarios, key=lambda s: s.name)),
            folds=tuple(sorted(self.folds, key=lambda f: f.test_start_ms)),
        )


def effective(
    spec: Replay, candidate: Candidate, scenario: Scenario, start: int, end: int
) -> Replay:
    """Vary only declared assumptions; preserve funding, limits and fee settlement rules."""
    return replace(
        spec,
        start_ms=start,
        end_ms=end,
        model=scenario.model,
        latency_bars=scenario.latency_bars,
        allocations=tuple(
            replace(
                a,
                source=replace(
                    a.source,
                    offset=a.source.offset + (start - spec.start_ms) // MINUTE,
                    count=(end - start) // MINUTE,
                ),
                context=replace(
                    a.context,
                    policy=replace(a.context.policy, spacing=candidate.spacing),
                    buy_fee=replace(a.context.buy_fee, rate=scenario.buy_fee_rate),
                    sell_fee=replace(a.context.sell_fee, rate=scenario.sell_fee_rate),
                ),
            )
            for a in spec.allocations
        ),
    )


def unchanged(inputs: tuple[Manifest, ...]) -> None:
    for manifest in inputs:
        if identity(manifest.source.path.stat(follow_symlinks=False)) != manifest.file_identity:
            raise DomainError("history changed during study")


@arithmetic
def return_fraction(result: dict[str, object]) -> Decimal:
    initial = number(result.get("initial_allocated_nav"))
    if initial <= 0:
        raise DomainError("positive study baseline required")
    return number(result.get("final_nav_at_close")) / initial - 1


@arithmetic
def run(spec: Replay, plan: Plan) -> dict[str, object]:
    spec = replace(
        spec,
        allocations=tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope))),
        capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
    )
    plan = plan.checked(spec)
    # Check every effective configuration before any replay. No favorable subset on failure.
    for fold in plan.folds:
        for scenario in plan.scenarios:
            for candidate in plan.candidates:
                for start, end in (
                    (fold.train_start_ms, fold.train_end_ms),
                    (fold.test_start_ms, fold.test_end_ms),
                ):
                    effective(spec, candidate, scenario, start, end)
    inputs = tuple(validate(a.source) for a in spec.allocations)
    if any(
        not m.replay_eligible or m.first_ms != spec.start_ms or m.last_ms != spec.end_ms - MINUTE
        for m in inputs
    ):
        raise DomainError("study requires complete eligible base history at exact endpoints")
    retained_bytes = 0
    rows: list[dict[str, object]] = []
    for fold in plan.folds:
        for scenario in plan.scenarios:
            results: dict[str, dict[str, dict[str, object]]] = {}
            winner: str | None = None
            for phase, start, end in (
                ("training", fold.train_start_ms, fold.train_end_ms),
                ("testing", fold.test_start_ms, fold.test_end_ms),
            ):
                members: dict[str, dict[str, object]] = {}
                common: tuple[Manifest, ...] | None = None
                for candidate in plan.candidates:
                    unchanged(inputs)
                    result = replay(effective(spec, candidate, scenario, start, end))
                    observed = manifests(result)
                    if common is not None and observed != common:
                        raise DomainError("history changed between study candidates")
                    common = observed
                    unchanged(inputs)
                    return_fraction(result)  # Require meaningful, finite metrics for every case.
                    retained_bytes += len(canonical(result))
                    if retained_bytes > MAX_RESULT_BYTES:
                        raise DomainError("study result byte cap exceeded")
                    members[candidate.name] = result
                results[phase] = members
                if phase == "training":
                    # Freeze selection before reading any test result. Assumptions are not ranked.
                    winner = min(members, key=lambda name: (-return_fraction(members[name]), name))
            if winner is None:
                raise DomainError("study produced no training selection")
            rows.append(
                {
                    "fold": fold.name,
                    "scenario": scenario.name,
                    "selected_candidate": winner,
                    "selected_training_return_fraction": return_fraction(
                        results["training"][winner]
                    ),
                    "selected_test_return_fraction": return_fraction(results["testing"][winner]),
                    "training": results["training"],
                    "testing": results["testing"],
                }
            )
    unchanged(inputs)
    report: dict[str, object] = {
        "schema": "grid-walk-forward-study-v1",
        "input_config": spec,
        "plan": plan,
        "selection_rule": SELECTION,
        "config_sha256": digest((spec, plan, SELECTION)),
        "inputs": inputs,
        "valuation_basis": "allocated_mark_to_close_without_exit_costs",
        "assumptions": (
            "Fresh state per candidate/window; no warm-up, carry, compounding or stitched NAV.",
            "Spacing alone is selected; fees, latency and OHLC conventions are separate scenarios.",
            "Selection uses training total NAV, including unrealized inventory; ties use names.",
            "Every declared test case is retained, creating multiple-testing exposure.",
            "Regimes and universe evidence are caller declarations, not verified historical facts.",
            "Static complete histories may exclude delisted markets; survivorship unresolved.",
            "No delisting recovery, rule changes, portfolio exits or independent-lot model.",
            "A plan hash records inputs, not proof of preregistration before seeing test history.",
            "Finite sequential workload and output bounds are not CPU/RSS/IO quotas.",
            "No live policy export, profit guarantee or evidence of actual queue execution.",
        ),
        "fold_scenarios": tuple(rows),
    }
    report["result_sha256"] = digest(report)
    if len(canonical(report)) > MAX_RESULT_BYTES:
        raise DomainError("study result byte cap exceeded")
    return report
