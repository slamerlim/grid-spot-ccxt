"""Observed worker progress and failure reasons reach the research UI."""

from __future__ import annotations

from pathlib import Path

from scripts.backtest_progress import engine_error


def test_engine_failure_keeps_bounded_explanation(tmp_path: Path) -> None:
    (tmp_path / "stderr.txt").write_text(
        "x" * 5000 + "\nOrder quantity rounds to zero; increase order_quote\n"
    )
    detail = engine_error(tmp_path, "Audited engine refused this run")
    assert "Order quantity rounds to zero; increase order_quote" in detail
    assert len(detail) < 550
