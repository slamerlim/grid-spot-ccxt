"""Bounded chronological studies of declared spacing candidates, never live tuning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from grid.domain.numbers import DomainError, arithmetic, bounded_int, decimal, identifier
from grid.persistence import codec
from grid.portfolio.model import Nav
from grid.research.artifacts import canonical, digest
from grid.research.comparison import manifests, number
from grid.research.history import MINUTE, Manifest, identity, validate
from grid.research.lot_reference import CONVENTION, MAX_POCKETS
from grid.research.lot_reference import run as reference_replay
from grid.research.portfolio_config import Settings
from grid.research.portfolio_replay import MAX_CYCLES
from grid.research.portfolio_replay import checked as checked_portfolio
from grid.research.portfolio_replay import run as portfolio_replay
from grid.research.replay import MODELS, Model, Replay
from grid.research.replay import run as replay

MAX_RUNS = 128
MAX_BAR_STEPS = 2_000_000
MAX_RESULT_BYTES = 64 * 1024**2
REGIMES = ("up", "down", "sideways", "unclassified")
SELECTION = "maximum_training_mark_to_close_return_then_lexicographic_candidate_name"
PORTFOLIO_SELECTION = "maximum_training_window_end_net_nav_return_then_lexicographic_candidate_name"


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

    def checked(self, spec: Replay, *, policies: int = 1) -> Plan:
        bounded_int(policies, 1, 2)
        if 2 * policies * len(self.candidates) * len(self.scenarios) * len(self.folds) > MAX_RUNS:
            raise DomainError("study replay count cap exceeded")
        steps = 0
        for fold in self.folds:
            if fold.train_start_ms < spec.start_ms or fold.test_end_ms > spec.end_ms:
                raise DomainError("study fold lies outside configured source window")
            steps += (fold.train_end_ms - fold.train_start_ms) // MINUTE
            steps += (fold.test_end_ms - fold.test_start_ms) // MINUTE
        steps *= policies * len(self.candidates) * len(self.scenarios) * len(spec.allocations)
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
def score(result: dict[str, object], end: int, portfolio: bool) -> tuple[Decimal, Decimal | None]:
    if not portfolio:
        return number(result.get("initial_allocated_nav")), return_fraction(result)
    initial, value = result.get("initial_nav"), result.get("last_nav")
    if (
        result.get("schema") != "grid-portfolio-series-v1"
        or result.get("evaluation_horizon") != "history_end"
        or result.get("simulation_end_ms_exclusive") != end
        or not isinstance(initial, Nav)
        or "last_nav" not in result
        or value is not None
        and (not isinstance(value, Nav) or value.now_ms != end)
    ):
        raise DomainError("study requires complete fixed-horizon portfolio evidence")
    if initial.net <= 0:
        raise DomainError("positive study baseline required")
    return initial.net, value.net / initial.net - 1 if value is not None else None


@arithmetic
def run(
    spec: Replay,
    plan: Plan,
    settings: Settings | None = None,
    *,
    max_cycles: int = 1,
    policy: Literal["basket", "independent_lots"] = "basket",
) -> dict[str, object]:
    bounded_int(max_cycles, 1, MAX_CYCLES)
    if policy not in ("basket", "independent_lots"):
        raise DomainError("unknown study research policy")
    if policy == "independent_lots":
        if settings is not None or max_cycles != 1:
            raise DomainError("independent-lot studies have no portfolio cycle model")
        if sum(a.context.policy.max_lots for a in spec.allocations) > MAX_POCKETS:
            raise DomainError("independent-lot study pocket capacity exceeded")
    if settings is None and max_cycles != 1:
        raise DomainError("portfolio settings required for study cycles")
    spec = replace(
        spec,
        allocations=tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope))),
        capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
    )
    plan = plan.checked(spec)
    settings = None if settings is None else settings.checked(spec)
    # Check every effective configuration before any replay. No favorable subset on failure.
    for fold in plan.folds:
        for scenario in plan.scenarios:
            for candidate in plan.candidates:
                for start, end in (
                    (fold.train_start_ms, fold.train_end_ms),
                    (fold.test_start_ms, fold.test_end_ms),
                ):
                    configured = effective(spec, candidate, scenario, start, end)
                    if settings is not None:
                        checked_portfolio(configured, settings, max_cycles, through_end=True)
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
            unavailable: tuple[str, ...] = ()
            for phase, start, end in (
                ("training", fold.train_start_ms, fold.train_end_ms),
                ("testing", fold.test_start_ms, fold.test_end_ms),
            ):
                members: dict[str, dict[str, object]] = {}
                common: tuple[Manifest, ...] | None = None
                baseline: Decimal | None = None
                scores: dict[str, Decimal] = {}
                for candidate in plan.candidates:
                    unchanged(inputs)
                    configured = effective(spec, candidate, scenario, start, end)
                    result = (
                        reference_replay(configured)
                        if policy == "independent_lots"
                        else replay(configured)
                        if settings is None
                        else portfolio_replay(
                            configured, settings, max_cycles=max_cycles, through_end=True
                        )
                    )
                    observed = manifests(result)
                    if common is not None and observed != common:
                        raise DomainError("history changed between study candidates")
                    common = observed
                    unchanged(inputs)
                    initial, metric = score(result, end, settings is not None)
                    if baseline is not None and initial != baseline:
                        raise DomainError("study candidate funding baseline changed")
                    baseline = initial
                    if metric is not None:
                        scores[candidate.name] = metric
                    retained_bytes += len(canonical(result))
                    if retained_bytes > MAX_RESULT_BYTES:
                        raise DomainError("study result byte cap exceeded")
                    members[candidate.name] = result
                results[phase] = members
                if phase == "training":
                    # Freeze selection before reading any test result. Assumptions are not ranked.
                    if settings is None:
                        winner = min(
                            members, key=lambda name: (-return_fraction(members[name]), name)
                        )
                    else:
                        unavailable = tuple(name for name in members if name not in scores)
                        winner = (
                            None
                            if unavailable
                            else min(scores, key=lambda name: (-scores[name], name))
                        )
            rows.append(
                {
                    "fold": fold.name,
                    "scenario": scenario.name,
                    "selected_candidate": winner,
                    "selected_training_return_fraction": score(
                        results["training"][winner], fold.train_end_ms, settings is not None
                    )[1]
                    if winner is not None
                    else None,
                    "selected_test_return_fraction": score(
                        results["testing"][winner], fold.test_end_ms, settings is not None
                    )[1]
                    if winner is not None
                    else None,
                    "training": results["training"],
                    "testing": results["testing"],
                }
            )
            if settings is not None:
                rows[-1]["selection_status"] = "unknown_training_nav" if unavailable else "selected"
                rows[-1]["selection_unavailable_candidates"] = unavailable
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
            "Fresh state per candidate/window; no warm-up, carry, compounding or stitched NAV."
            if settings is None
            else "Fresh state/funding per candidate/window; cycles carry within a window. "
            "No cross-window carry, compounding, warm-up or stitched NAV.",
            "Spacing alone is selected; fees, latency and OHLC conventions are separate scenarios.",
            "Selection uses training total NAV, including unrealized inventory; ties use names.",
            "Every declared test case is retained, creating multiple-testing exposure.",
            "Regimes and universe evidence are caller declarations, not verified historical facts.",
            "Static complete histories may exclude delisted markets; survivorship unresolved.",
            "Fixed-anchor independent-lot research; no portfolio exits, historical rule "
            "changes or delisting recovery. Production retains one aggregate SELL."
            if policy == "independent_lots"
            else "No delisting recovery, rule changes, portfolio exits or independent-lot model."
            if settings is None
            else "Static rules/FX/roster; portfolio cycles are continuous only within each "
            "fresh-funded window. No delisting recovery or independent-lot model.",
            "A plan hash records inputs, not proof of preregistration before seeing test history.",
            "Finite sequential workload and output bounds are not CPU/RSS/IO quotas.",
            "No live policy export, profit guarantee or evidence of actual queue execution.",
        ),
        "fold_scenarios": tuple(rows),
    }
    if policy == "independent_lots":
        report.update(
            schema="grid-independent-lot-walk-forward-study-v1",
            research_convention=CONVENTION,
            config_sha256=digest((spec, plan, SELECTION, CONVENTION)),
        )
    if settings is not None:
        report.update(
            schema="grid-portfolio-walk-forward-study-v1",
            selection_rule=PORTFOLIO_SELECTION,
            config_sha256=digest((spec, plan, settings, max_cycles, PORTFOLIO_SELECTION)),
            valuation_basis="owned_portfolio_controller_window_end_net_nav",
            portfolio_settings=settings,
            portfolio_cycles=max_cycles,
            portfolio_study_basis=(
                "Every candidate is valued at the same window end, even after completing "
                "its allowed cycles. Retained ownership continues to be reconciled/marked; "
                "no forced EOF liquidation or additional cycles. Unknown training NAV "
                "withholds selection for the entire fold/scenario, never discards a losing "
                "or uncertain candidate. All held-out results remain visible. Training "
                "selection is frozen before testing; an unknown selected test NAV stays null."
            ),
        )
    report["result_sha256"] = digest(report)
    if len(canonical(report)) > MAX_RESULT_BYTES:
        raise DomainError("study result byte cap exceeded")
    return report


def study_rows(report: dict[str, object]) -> tuple[dict[str, object], ...]:
    rows = report.get("fold_scenarios")
    if not isinstance(rows, tuple) or any(not isinstance(row, dict) for row in rows):
        raise DomainError("study comparison rows missing")
    return rows


def study_members(row: dict[str, object], phase: str, plan: Plan) -> dict[str, dict[str, object]]:
    members = row.get(phase)
    if (
        not isinstance(members, dict)
        or set(members) != {c.name for c in plan.candidates}
        or any(not isinstance(value, dict) for value in members.values())
    ):
        raise DomainError("study comparison candidates missing")
    return members


@arithmetic
def compare_lots(spec: Replay, plan: Plan) -> dict[str, object]:
    """Two predetermined policies, each selected using only its own training NAV."""
    # Count both full studies before opening history or starting either policy.
    plan = plan.checked(spec, policies=2)
    if sum(a.context.policy.max_lots for a in spec.allocations) > MAX_POCKETS:
        raise DomainError("independent-lot study pocket capacity exceeded")
    basket = run(spec, plan)
    retained = len(canonical(basket))
    if retained > MAX_RESULT_BYTES:
        raise DomainError("lot study comparison result byte cap exceeded")
    reference = run(spec, plan, policy="independent_lots")
    if retained + len(canonical(reference)) > MAX_RESULT_BYTES:
        raise DomainError("lot study comparison result byte cap exceeded")
    inputs = manifests(basket)
    if inputs != manifests(reference):
        raise DomainError("history changed between study policies")
    if basket["input_config"] != reference["input_config"] or basket["plan"] != reference["plan"]:
        raise DomainError("study comparison configuration changed")
    basket_rows, reference_rows = study_rows(basket), study_rows(reference)
    expected = tuple((f, s) for f in plan.folds for s in plan.scenarios)
    if len(basket_rows) != len(reference_rows) or len(basket_rows) != len(expected):
        raise DomainError("study comparison cases missing")
    comparisons: list[dict[str, object]] = []
    for (fold, scenario), first, second in zip(expected, basket_rows, reference_rows, strict=True):
        if any(
            (r.get("fold"), r.get("scenario")) != (fold.name, scenario.name)
            for r in (first, second)
        ):
            raise DomainError("study comparison cases differ")
        matched: dict[str, dict[str, Decimal]] = {}
        for phase in ("training", "testing"):
            a, b = study_members(first, phase, plan), study_members(second, phase, plan)
            differences: dict[str, Decimal] = {}
            for candidate in plan.candidates:
                x, y = a[candidate.name], b[candidate.name]
                if x.get("config") != y.get("config") or manifests(x) != manifests(y):
                    raise DomainError("study comparison member inputs differ")
                if number(x.get("initial_allocated_nav")) != number(y.get("initial_allocated_nav")):
                    raise DomainError("study comparison funding baseline changed")
                differences[candidate.name] = return_fraction(y) - return_fraction(x)
            matched[phase] = differences
        for row in (first, second):
            winner = row.get("selected_candidate")
            training = study_members(row, "training", plan)
            if (
                not isinstance(winner, str)
                or winner not in training
                or winner
                != min(training, key=lambda name: (-return_fraction(training[name]), name))
            ):
                raise DomainError("study comparison training selection differs")
            testing = study_members(row, "testing", plan)
            if row.get("selected_training_return_fraction") != return_fraction(
                training[winner]
            ) or row.get("selected_test_return_fraction") != return_fraction(testing[winner]):
                raise DomainError("study comparison selected metrics differ")
        comparisons.append(
            {
                "fold": fold.name,
                "scenario": scenario.name,
                "basket_selected_candidate": first["selected_candidate"],
                "reference_selected_candidate": second["selected_candidate"],
                "basket_selected_test_return_fraction": first["selected_test_return_fraction"],
                "reference_selected_test_return_fraction": second["selected_test_return_fraction"],
                "reference_minus_basket_selected_test_return_fraction": number(
                    second["selected_test_return_fraction"]
                )
                - number(first["selected_test_return_fraction"]),
                "matched_candidate_return_differences": matched,
            }
        )
    unchanged(inputs)
    report: dict[str, object] = {
        "schema": "grid-lot-walk-forward-comparison-v1",
        "input_config": basket["input_config"],
        "plan": plan,
        "inputs": inputs,
        "config_sha256": digest(
            (basket["input_config"], plan, SELECTION, CONVENTION, "paired_studies")
        ),
        "valuation_basis": "allocated_mark_to_close_without_exit_costs",
        "basket_single_sell": basket,
        "independent_lots": reference,
        "comparisons": tuple(comparisons),
        "interpretation": (
            "Each predetermined policy selects spacing from its own training NAV; "
            "held-out outcomes never choose policy or spacing. Same candidates, scenarios, "
            "windows, funds and source manifests. Every outcome is retained, with fresh "
            "funding per candidate/window. Policies execute sequentially with independent "
            "selection; no cross-policy feedback, portfolio exit, stitched returns, live "
            "export or profitability claim. Differences are modeled research observations."
        ),
    }
    report["result_sha256"] = digest(report)
    if len(canonical(report)) > MAX_RESULT_BYTES:
        raise DomainError("lot study comparison result byte cap exceeded")
    return report
