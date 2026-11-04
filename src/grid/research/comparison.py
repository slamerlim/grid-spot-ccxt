"""Fixed OHLC sensitivity studies; never select a trading policy from winning outcomes."""

from dataclasses import dataclass, replace
from decimal import Decimal

from grid.domain.numbers import DomainError, arithmetic, bounded_int
from grid.persistence import codec
from grid.portfolio.model import Cycle, Nav
from grid.research.artifacts import canonical, digest
from grid.research.history import Manifest, identity
from grid.research.portfolio_config import Settings
from grid.research.portfolio_replay import MAX_CYCLES
from grid.research.portfolio_replay import run as run_portfolio
from grid.research.replay import MODELS, Model, Replay
from grid.research.replay import run as run_allocation

# These are fill conventions, not a claimed ordering of their financial outcomes.
SCENARIOS = tuple(
    zip(
        ("conservative", "baseline", "optimistic"),
        MODELS,
        (
            "Require crossing beyond float32 uncertainty; skip both sides on ambiguous bars.",
            "Allow central-value touches; select BUYs when both pre-existing sides touch.",
            "Allow central-value touches; select SELLs when both pre-existing sides touch.",
        ),
        strict=True,
    )
)
MAX_RESULT_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class Summary:
    scenario: str
    model: Model
    starting_nav: Decimal
    observed_end_nav: Decimal | None
    observed_nav_return_fraction: Decimal | None
    evaluated_until_ms_exclusive: int
    phase: str
    final_cycle_return_fraction: Decimal | None
    exit_shortfall: Decimal | None


def number(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError("scenario engine returned an invalid financial metric")
    return value


@arithmetic
def summary(name: str, model: Model, result: dict[str, object], spec: Replay) -> Summary:
    if result.get("schema") == "grid-research-v1":
        initial = number(result.get("initial_allocated_nav"))
        nav: Decimal | None = number(result.get("final_nav_at_close"))
        end = spec.end_ms
        phase = "history_complete_no_forced_exit"
        final = shortfall = None
    elif result.get("schema") in ("grid-portfolio-research-v1", "grid-portfolio-series-v1"):
        series = result.get("schema") == "grid-portfolio-series-v1"
        cycle, value, until = (
            result.get("last_cycle" if series else "cycle"),
            result.get("last_nav"),
            result.get("simulation_end_ms_exclusive"),
        )
        if not isinstance(cycle, Cycle) or value is not None and not isinstance(value, Nav):
            raise DomainError("scenario engine returned an invalid portfolio outcome")
        if type(until) is not int or not spec.start_ms < until <= spec.end_ms:
            raise DomainError("scenario engine returned an invalid evaluation endpoint")
        initial, nav, end = cycle.baseline.net, value.net if value is not None else None, until
        if series:
            initial_nav = result.get("initial_nav")
            if not isinstance(initial_nav, Nav):
                raise DomainError("scenario engine returned an invalid series baseline")
            initial = initial_nav.net
        phase, final, shortfall = cycle.phase.value, cycle.final_return, cycle.exit_shortfall
    else:
        raise DomainError("unsupported scenario engine schema")
    if initial <= 0:
        raise DomainError("positive shared scenario baseline required")
    return Summary(
        name,
        model,
        initial,
        nav,
        nav / initial - 1 if nav is not None else None,
        end,
        phase,
        final,
        shortfall,
    )


def manifests(result: dict[str, object]) -> tuple[Manifest, ...]:
    rows = result.get("inputs")
    if (
        not isinstance(rows, tuple)
        or not rows
        or any(not isinstance(row, Manifest) for row in rows)
    ):
        raise DomainError("scenario input manifests missing")
    return rows


@arithmetic
def nav_range(rows: tuple[Summary, ...]) -> dict[str, object]:
    if len({r.starting_nav for r in rows}) != 1:
        raise DomainError("scenario baseline changed")
    reasons = []
    same_end = len({r.evaluated_until_ms_exclusive for r in rows}) == 1
    if not same_end:
        reasons.append("different_evaluation_endpoints")
    known = tuple(r.observed_end_nav for r in rows if r.observed_end_nav is not None)
    if len(known) != len(rows):
        reasons.append("unknown_ending_nav")
    available = not reasons
    low, high = (min(known), max(known)) if available else (None, None)
    return {
        "available": available,
        "reasons": tuple(reasons),
        "evaluated_until_ms_exclusive": rows[0].evaluated_until_ms_exclusive if same_end else None,
        "minimum_observed_nav": low,
        "maximum_observed_nav": high,
        "span": high - low if low is not None and high is not None else None,
        "interpretation": "Range of these modeled outcomes only; not a bound on actual returns.",
    }


@arithmetic
def run(
    spec: Replay, settings: Settings | None = None, *, max_cycles: int = 1
) -> dict[str, object]:
    bounded_int(max_cycles, 1, MAX_CYCLES)
    if settings is None and max_cycles != 1:
        raise DomainError("portfolio settings required for multiple cycles")
    spec = replace(
        spec,
        allocations=tuple(sorted(spec.allocations, key=lambda a: codec.dumps(a.scope))),
        capital=tuple(sorted(spec.capital, key=lambda p: (p.venue, p.account, p.currency))),
    )
    settings = None if settings is None else settings.checked(spec)
    members: list[dict[str, object]] = []
    summaries: list[Summary] = []
    inputs: tuple[Manifest, ...] | None = None
    retained_bytes = 0
    for name, model, convention in SCENARIOS:
        effective = replace(spec, model=model)
        result = (
            run_allocation(effective)
            if settings is None
            else run_portfolio(effective, settings, max_cycles=max_cycles)
        )
        observed = manifests(result)
        if inputs is not None and observed != inputs:
            raise DomainError("history changed between scenarios; discard comparison")
        inputs = observed
        retained_bytes += len(canonical(result))
        if retained_bytes > MAX_RESULT_BYTES:
            raise DomainError("scenario comparison result byte cap exceeded")
        summaries.append(summary(name, model, result, effective))
        members.append(
            {"scenario": name, "model": model, "fill_convention": convention, "result": result}
        )
    if inputs is None:
        raise DomainError("scenario suite produced no results")
    for manifest in inputs:
        if identity(manifest.source.path.stat(follow_symlinks=False)) != manifest.file_identity:
            raise DomainError("history changed during scenario comparison")
    report: dict[str, object] = {
        "schema": "grid-scenario-comparison-v1",
        "input_config": spec,
        "portfolio_settings": settings,
        "config_sha256": digest((spec, settings, SCENARIOS)),
        "inputs": inputs,
        "reporting_currency": spec.allocations[0].context.market.quote,
        "valuation_basis": "allocated_mark_to_close_without_exit_costs"
        if settings is None
        else "owned_portfolio_controller_net_nav",
        "assumptions": (
            "Three predetermined fill conventions; labels do not rank PnL or certify liquidity.",
            "Baseline BUY priority is a declared reference, not a calibrated estimate.",
            "Optimistic SELL priority favors settlement, not guaranteed profit or best future NAV.",
            "The configured model is recorded, then replaced by each suite member model.",
            "Same funding, fees, latency, constraints and inputs; no tuning from outcomes.",
            "At most one side per allocation/bar; new orders wait at least one later bar.",
            "Different endpoints or unknown valuations make the shared NAV range unavailable.",
            "Runs are sequential with fresh state; no live selection or parameter transfer.",
        ),
        "summaries": tuple(summaries),
        "observed_nav_range": nav_range(tuple(summaries)),
        "scenarios": tuple(members),
    }
    if max_cycles > 1:
        report["portfolio_cycles"] = max_cycles
        report["config_sha256"] = digest((spec, settings, SCENARIOS, max_cycles))
        report["cycle_summary_basis"] = (
            "Starting NAV and observed returns cover the original funds and entire series. "
            "Phase, final_cycle_return_fraction and exit_shortfall describe only the last "
            "started cycle; each retained member reports all cycle outcomes and completion."
        )
    report["result_sha256"] = digest(report)
    if len(canonical(report)) > MAX_RESULT_BYTES:
        raise DomainError("scenario comparison result byte cap exceeded")
    return report
