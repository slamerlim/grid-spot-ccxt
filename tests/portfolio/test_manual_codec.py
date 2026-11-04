import json
from dataclasses import replace
from decimal import Decimal as D

import pytest

from grid.domain.numbers import DomainError
from grid.operations.synthetic import fixture
from grid.persistence import codec
from grid.portfolio.model import Cycle, CyclePhase, ExitApproval, ExitSlot, Nav


def legacy_cycle(*, triggered: bool) -> Cycle:
    case = fixture(1, 1)
    cycle = Cycle(
        "legacy",
        1,
        CyclePhase.ACTIVE,
        case.portfolio,
        Nav(10000, 1, "legacy-nav", D(1000), (), (), ()),
        D(0),
        None,
        None,
        None,
        10000,
        (),
        (),
        (),
        "baseline",
        "tester",
    )
    if triggered:
        cycle = replace(
            cycle,
            phase=CyclePhase.REQUESTED,
            trigger=Nav(10001, 2, "legacy-trigger", D(1100), (), (), ()),
            trigger_baseline=D(1000),
            started_exit_ms=10001,
            last_evaluation_ms=10001,
            slots=(ExitSlot(case.scopes[0], D(100), 0, None, None, "cancel and reconcile"),),
        )
    return cycle


@pytest.mark.parametrize("triggered", [False, True])
def test_existing_cycle_encoding_is_byte_compatible(triggered: bool) -> None:
    cycle = legacy_cycle(triggered=triggered)
    # Captured with the unmodified a7d2017 implementation before adding manual exits.
    expected = {
        # pragma: allowlist nextline secret
        True: "7def4187fcd4c9b4db6b61499c1bf0de8b6a8bcbe11e08cab7ebddd5f8a222b0",
        # pragma: allowlist nextline secret
        False: "1985abc2dd30be9c682e5e319fb398df8af0eeeaa111b46e44f5fe8c9075937e",
    }
    assert codec.digest(cycle) == expected[triggered]
    encoded = codec.dumps(cycle)
    assert "manual_exit" not in encoded
    assert codec.loads(encoded, Cycle) == cycle
    assert cycle.exit_cause == ("nav_target" if triggered else None)


def test_manual_loss_does_not_weaken_threshold_proof_or_clear_exit_latch() -> None:
    cycle = legacy_cycle(triggered=True)
    assert cycle.trigger is not None
    loss = replace(cycle.trigger, net=D(900))
    with pytest.raises(DomainError, match="threshold"):
        replace(cycle, trigger=loss)
    manual = replace(
        cycle,
        trigger=loss,
        manual_exit=ExitApproval(cycle.id, codec.digest(cycle.policy)),
    )
    assert codec.loads(codec.dumps(manual), Cycle) == manual
    assert manual.exit_cause == "operator" and manual.target_reached_at_exit is False
    with pytest.raises(DomainError, match="threshold"):
        replace(manual, manual_exit=None)
    with pytest.raises(DomainError, match="cleared exit latch"):
        replace(manual, phase=CyclePhase.ACTIVE)
    with pytest.raises(DomainError, match="different cycle"):
        replace(manual, manual_exit=ExitApproval("other", codec.digest(cycle.policy)))


@pytest.mark.parametrize("mutation", ["missing_required", "unknown", "explicit_null"])
def test_cycle_extension_does_not_permit_unreviewed_snapshot_fields(mutation: str) -> None:
    encoded = json.loads(codec.dumps(legacy_cycle(triggered=True)))
    if mutation == "missing_required":
        del encoded["fields"]["trigger_baseline"]
    elif mutation == "unknown":
        encoded["fields"]["bypass"] = True
    else:
        encoded["fields"]["manual_exit"] = None  # Noncanonical redundant field.
    with pytest.raises(DomainError):
        codec.loads(json.dumps(encoded, sort_keys=True, separators=(",", ":")), Cycle)
