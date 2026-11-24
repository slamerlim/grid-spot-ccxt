from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import pytest_socket
import streamlit as st
from streamlit.testing.v1 import AppTest

from scripts import research_sweep_matrix as matrix
from scripts import results_viewer as app_module
from scripts import results_viewer_data as viewer
from scripts.research_sweep_reference import EXPANDED_CONFIGURATION_COUNT, expanded_configuration


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main_table(app: AppTest) -> dict[str, Any]:
    frame = app.dataframe[0].value
    return {"rows": frame.to_dict("records"), "columns": [{"key": key} for key in frame.columns]}


def fixture_row(index: int) -> dict[str, Any]:
    primary: dict[str, Any] = dict.fromkeys(matrix.METRIC_FIELDS, "0/1")
    primary.update(
        buy_count=0,
        sell_count=0,
        longest_hold_ms=0,
        max_drawdown_ms=0,
        peak_spent_ms=0,
        return_pct=None,
    )
    period: dict[str, Any] = dict.fromkeys(matrix.PERIOD_FIELDS)
    for key in matrix.PERIOD_RATIO_FIELDS:
        period[key] = "0/1"
    for key in matrix.PERIOD_INTEGER_FIELDS:
        period[key] = "0"
    period.update(
        start_ms=1704067200000,
        end_ms=1706745600000,
        complete_month=True,
        profit_factor=None,
        profit_factor_status="no_closures",
        sample_months=1,
        sharpe=None,
        sharpe_squared=None,
        sharpe_status="insufficient_months",
        sortino=None,
        sortino_squared=None,
        sortino_status="insufficient_months",
        buy_count=0,
        sell_count=0,
        winning_closures=0,
        losing_closures=0,
        breakeven_closures=0,
        peak_spent_ms=0,
    )
    second = dict(period, start_ms=1706745600000, end_ms=1709251200000, total_pnl=f"{index + 1}/7")
    overall = dict(second, start_ms=period["start_ms"], complete_month=None)
    scope = {"money_scale_denominator": "7", "overall": overall, "monthly": [period, second]}
    return {
        **expanded_configuration(index),
        "total": dict(primary, total_pnl=overall["total_pnl"]),
        "venues": [dict(primary, venue=name) for name in ("okx", "gate")],
        "trade_hash": "ab" * 32,
        "pair_fingerprint": {"sha256": "cd" * 32, "rows": 2, "bytes": 500},
        "aggregate_cycles": {
            "global": {"completed_count": 2},
            "venues": [
                {"venue": "okx", "completed_count": 3},
                {"venue": "gate", "completed_count": 4},
            ],
        },
        "performance": {
            "schema": "grid-monthly-performance-v1",
            "total": copy.deepcopy(scope),
            "venues": [dict(copy.deepcopy(scope), venue=name) for name in ("okx", "gate")],
        },
    }


def publish_rows(
    tmp_path: Path, rows: list[dict[str, Any]], identity: dict[str, Any] | None = None
) -> Path:
    if identity is None:
        identity = {"matrix": {"canonical_ordinals": list(range(len(rows)))}}
    catalog = matrix.ResultCatalog(tmp_path / "results.sqlite", identity, create=True)
    try:
        catalog.append(0, rows, {"fixture": True})
        catalog.verify(len(rows), complete=True)
    finally:
        catalog.close()
    publication = tmp_path / "final-publication.json"
    publication.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "study_sha256": matrix._digest(identity),
                "binary_results": str(tmp_path / "results.bin"),
                "index": str(tmp_path / "results.sqlite"),
                "binary_bytes": (tmp_path / "results.bin").stat().st_size,
                "binary_sha256": sha(tmp_path / "results.bin"),
                "index_sha256": sha(tmp_path / "results.sqlite"),
                "configuration_count": len(rows),
                "source_count": 2,
                "stage_observations": [{"wall_seconds": 12.0}],
            }
        )
    )
    return publication


@pytest.fixture
def published(tmp_path: Path) -> Path:
    return publish_rows(tmp_path, [fixture_row(index) for index in range(3)])


def staggered_row(index: int) -> dict[str, Any]:
    row = fixture_row(index)
    performance = row["performance"]
    gate = performance["venues"][1]
    gate["monthly"] = [gate["monthly"][1]]
    gate["overall"]["start_ms"] = gate["monthly"][0]["start_ms"]
    # Each venue owns its own time span. Only Gate has a result in February.
    for period in performance["venues"][0]["monthly"]:
        period["total_pnl"] = "0/1"
    performance["venues"][0]["overall"]["total_pnl"] = "0/1"
    row["venues"][1]["total_pnl"] = gate["overall"]["total_pnl"]
    return row


def test_archive_and_every_projection_match_existing_exact_decoder(published: Path) -> None:
    before = {path.name: sha(path) for path in published.parent.iterdir() if path.is_file()}
    archive = viewer.open_archive(published)
    assert archive.months == ("2024-01", "2024-02")
    for venue in ("total", *archive.venues):
        for period in ("overall", *archive.months):
            rows = viewer.read_rows(archive, venue, period)
            assert len(rows) == 3
            assert [row["ordinal"] for row in rows] == [1, 2, 3]
            for index, row in enumerate(rows):
                original = fixture_row(index)
                scopes = original["performance"]
                scope = (
                    scopes["total"]
                    if venue == "total"
                    else next(entry for entry in scopes["venues"] if entry["venue"] == venue)
                )
                expected = (
                    scope["overall"]
                    if period == "overall"
                    else scope["monthly"][archive.months.index(period)]
                )
                assert row["total_pnl"] == Fraction(expected["total_pnl"])
                assert row["buy_floor_pct"] == -60
                assert row["profit_factor"] is None
                assert row["profit_factor_status"] == "Нет закрытий"
                if period == "overall":
                    assert row["global_exits"] == 2
                    assert row["exchange_exits"] == (
                        7 if venue == "total" else 3 if venue == "okx" else 4
                    )
                else:
                    assert "max_drawdown" not in row and "global_exits" not in row
    after = {path.name: sha(path) for path in published.parent.iterdir() if path.is_file()}
    assert after == before


def test_depth_matrix_projects_exact_thresholds_and_preserves_legacy_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical = [
        29,
        EXPANDED_CONFIGURATION_COUNT + 29,
        2 * EXPANDED_CONFIGURATION_COUNT + 29,
    ]
    configurations = [matrix._depth_configuration(index) for index in canonical]
    identity = {
        "matrix": {
            "configuration_plan_schema": matrix.DEPTH_PLAN_SCHEMA,
            "canonical_ordinals": canonical,
            "configurations": configurations,
        }
    }
    rows = [dict(fixture_row(29), **config) for config in configurations]
    for row in rows:
        row["grid_depth_pct"] += "/1"
    publication = publish_rows(tmp_path, rows, identity)
    archive = viewer.open_archive(publication)
    assert archive.configurations == tuple(configurations)
    before = {p.name: sha(p) for p in publication.parent.iterdir()}
    for venue in ("total", *archive.venues):
        for period in ("overall", *archive.scope_months[venue]):
            projected = viewer.read_rows(archive, venue, period)
            assert [row["buy_floor_pct"] for row in projected] == [-60, -30, -35]
            assert [row["id"] for row in projected] == [config["id"] for config in configurations]
            assert all(row["total_pnl"] == projected[0]["total_pnl"] for row in projected)
    for config in configurations:
        overall, monthly = viewer.read_configuration(archive, config["id"])
        expected = -Fraction(config["grid_depth_pct"])
        assert all(row["buy_floor_pct"] == expected for row in overall)
        assert all(row["buy_floor_pct"] == expected for scope in monthly.values() for row in scope)
    assert before == {p.name: sha(p) for p in publication.parent.iterdir()}
    filtered = viewer.filter_rows(projected, [("buy_floor_pct", "≥", Fraction(-35), None)])
    assert {row["buy_floor_pct"] for row in filtered} == {Fraction(-30), Fraction(-35)}
    assert [row["buy_floor_pct"] for row in viewer.sort_rows(projected, "buy_floor_pct")] == [
        -60,
        -35,
        -30,
    ]
    display = app_module.dataframe(projected, ["buy_floor_pct"])
    assert display["buy_floor_pct"].tolist() == [-60.0, -30.0, -35.0]
    assert (
        app_module.column_config(["buy_floor_pct"])["buy_floor_pct"]["type_config"]["format"]
        == "%.0f%%"
    )
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(publication))
    app = AppTest.from_file(str(Path(app_module.__file__)))
    app.run(timeout=20)
    assert not app.exception
    assert [row["buy_floor_pct"] for row in main_table(app)["rows"]] == [-60, -30, -35]
    app.query_params["config"] = configurations[1]["id"]
    app.run()
    assert not app.exception
    assert any("Нижняя граница BUY, %: -30.0" in item.value for item in app.caption)


def test_depth_identity_mismatch_rejected_before_display(published: Path) -> None:
    archive = viewer.open_archive(published)
    altered = (dict(archive.configurations[0], grid_depth_pct="30"), *archive.configurations[1:])
    mismatch = replace(archive, configurations=altered)
    with pytest.raises(ValueError, match="Глубина BUY"):
        viewer.read_rows(mismatch, "total", "overall")
    with pytest.raises(ValueError, match="Глубина BUY"):
        viewer.read_configuration(mismatch, archive.entries[0][1])


def test_different_venue_calendars_preserve_exact_months_and_old_format(tmp_path: Path) -> None:
    publication = publish_rows(tmp_path, [staggered_row(index) for index in range(3)])
    archive = viewer.open_archive(publication)
    assert archive.months == ("2024-01", "2024-02")
    assert archive.scope_months == {
        "total": ("2024-01", "2024-02"),
        "okx": ("2024-01", "2024-02"),
        "gate": ("2024-02",),
    }
    with pytest.raises(ValueError, match="истории выбранной биржи"):
        viewer.read_rows(archive, "gate", "2024-01")
    gate_rows = viewer.read_rows(archive, "gate", "2024-02")
    assert [row["total_pnl"] for row in gate_rows] == [Fraction(index + 1, 7) for index in range(3)]
    medians = viewer.build_medians(archive)
    assert all(medians[str(row["id"])]["gate"]["full_months"] == 1 for row in gate_rows)
    assert all(medians[str(row["id"])]["total"]["full_months"] == 2 for row in gate_rows)
    for index, (_, config_id, *_rest) in enumerate(archive.entries):
        overall, monthly = viewer.read_configuration(archive, config_id)
        assert [row["month"] for row in monthly["gate"]] == ["2024-02"]
        assert [row["month"] for row in monthly["total"]] == ["2024-01", "2024-02"]
        assert monthly["gate"][0]["pnl_cumulative"] == Fraction(index + 1, 7)
        assert overall[2]["start_ms"] == 1706745600000


def test_venue_period_selector_resets_when_switching_to_shorter_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    publication = publish_rows(tmp_path, [staggered_row(index) for index in range(3)])
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(publication))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.run(timeout=20)
    assert not app.exception
    app.selectbox(key="selected_period").set_value("2024-01").run()
    app.selectbox(key="selected_venue").set_value("gate").run()
    assert not app.exception
    assert app.selectbox(key="selected_period").value == "overall"
    app.selectbox(key="selected_period").set_value("2024-02").run()
    assert not app.exception and len(main_table(app)["rows"]) == 3
    assert "total_pnl" in {column["key"] for column in main_table(app)["columns"]}


def test_integrity_and_completed_publication_required(published: Path) -> None:
    archive = viewer.open_archive(published)
    with archive.binary.open("r+b") as handle:
        handle.seek(-1, 2)
        handle.write(b"!")
    with pytest.raises(ValueError, match="изменились"):
        viewer.read_rows(archive, "total", "overall")
    with pytest.raises(ValueError, match="сумма"):
        viewer.open_archive(published)
    manifest = json.loads(published.read_text())
    manifest["status"] = "RUNNING"
    published.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="завершения"):
        viewer.open_archive(published)


def test_projection_checks_each_frame_and_period(published: Path) -> None:
    archive = viewer.open_archive(published)
    with archive.binary.open("rb") as handle:
        entry = archive.entries[0]
        payload = matrix._read_payload(handle, *entry[2:])
    with pytest.raises(ValueError, match="checksum"):
        viewer.project(payload, bytes(32), "total", None)
    with pytest.raises(ValueError, match="Месяц"):
        viewer.project(payload, entry[4], "total", 99)
    with pytest.raises(ValueError, match="биржа"):
        viewer.read_rows(archive, "unknown", "overall")


def test_numeric_filter_and_sort_do_not_collapse_to_binary_float() -> None:
    small = Fraction(10**30 + 1, 10**30)
    large = Fraction(10**30 + 2, 10**30)
    assert float(small) == float(large)
    rows: list[viewer.Row] = [
        {"id": "large", "fees": large},
        {"id": "small", "fees": small},
        {"id": "missing", "fees": None},
    ]
    assert [row["id"] for row in viewer.sort_rows(rows, "fees")] == ["small", "large", "missing"]
    assert [row["id"] for row in viewer.sort_rows(rows, "fees", True)] == [
        "large",
        "small",
        "missing",
    ]
    assert viewer.filter_rows(rows, [("fees", "=", small, None)]) == [rows[1]]
    assert viewer.filter_rows(rows, [("fees", "Нет значения", None, None)]) == [rows[2]]
    assert viewer.filter_rows(rows, [("fees", "Между", small, large)]) == rows[:2]
    assert viewer.matches("AbCd", "Содержит", "bc")
    assert viewer.matches(3, "≥", Fraction(3)) and viewer.matches(3, "≤", Fraction(3))
    assert viewer.matches(3, "≠", 4) and viewer.matches(3, "Есть значение")
    assert not viewer.matches(None, "≠", 4)
    with pytest.raises(ValueError, match="граница"):
        viewer.matches(3, "Между", 4, 2)
    output = viewer.exact_csv(rows, ["id", "fees"])
    assert str(small) in output and str(large) in output


@pytest.mark.parametrize("text", ["NaN", "Inf", "-Infinity", "1/0", "nonsense"])
def test_invalid_numeric_filter_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        viewer.parse_value(text, viewer.BY_KEY["fees"])


def test_every_available_column_has_filter_and_sort_support(published: Path) -> None:
    rows = viewer.read_rows(viewer.open_archive(published), "total", "overall")
    for key, value in rows[0].items():
        op = "Нет значения" if value is None else "="
        assert rows[0] in viewer.filter_rows(rows, [(key, op, value, None)])
        assert len(viewer.sort_rows(rows, key)) == len(rows)
    date = viewer.parse_value("2024-01-01", viewer.BY_KEY["start_ms"])
    assert date == int(datetime(2023, 12, 31, 21, tzinfo=UTC).timestamp() * 1000)
    assert viewer.parse_value("0,25", viewer.BY_KEY["fees"]) == Fraction(1, 4)


@pytest.mark.parametrize(
    ("pnl", "peak", "drawdown", "expected_pnl", "expected_drawdown"),
    [
        ("100", "1000", "200", Fraction(10), Fraction(20)),
        ("-50", "1000", "300", Fraction(-5), Fraction(30)),
        ("1/7", "3/11", "2/13", Fraction(1100, 21), Fraction(2200, 39)),
        ("0", "0", "0", None, None),
    ],
)
def test_percentages_use_exact_peak_spent_and_preserve_missing_base(
    pnl: str,
    peak: str,
    drawdown: str,
    expected_pnl: Fraction | None,
    expected_drawdown: Fraction | None,
) -> None:
    row = viewer._row({"total_pnl": pnl, "peak_spent": peak, "max_drawdown": drawdown}, 0)
    assert row["return_pct"] == expected_pnl
    assert row["max_drawdown_pct"] == expected_drawdown


def test_selected_configuration_matches_overview_and_preserves_exact_months(
    published: Path,
) -> None:
    archive = viewer.open_archive(published)
    before = {path.name: sha(path) for path in published.parent.iterdir() if path.is_file()}
    for ordinal, config_id, *_ in archive.entries:
        overall, monthly = viewer.read_configuration(archive, config_id)
        assert len(overall) == 3
        for index, venue in enumerate(("total", *archive.venues)):
            expected = viewer.read_rows(archive, venue, "overall")[ordinal]
            assert all(overall[index][key] == value for key, value in expected.items())
            assert [row["month"] for row in monthly[venue]] == list(archive.months)
            assert monthly[venue][-1]["pnl_cumulative"] == expected["total_pnl"]
            for month, row in zip(archive.months, monthly[venue], strict=True):
                period = viewer.read_rows(archive, venue, month)[ordinal]
                assert all(row[key] == value for key, value in period.items())
                assert "max_drawdown_pct" not in row
    with pytest.raises(ValueError, match="Неизвестная конфигурация"):
        viewer.read_configuration(archive, "not-a-config")
    assert before == {path.name: sha(path) for path in published.parent.iterdir() if path.is_file()}


def test_monthly_charts_keep_month_axis_and_gaps_without_changing_exact_rows(
    published: Path,
) -> None:
    archive = viewer.open_archive(published)
    _, months = viewer.read_configuration(archive, archive.entries[0][1])
    before = copy.deepcopy(months)
    for keys, bars in (
        (("pnl_cumulative",), False),
        (("total_pnl",), True),
        (("sharpe", "sortino"), False),
        (("profit_factor",), False),
    ):
        spec = app_module.monthly_chart(months["total"], keys, "Fixture", bars=bars).to_dict()
        assert spec["encoding"]["x"]["field"] == "month"
        assert spec["encoding"]["x"]["type"] == "ordinal"
        if not bars:
            assert spec["mark"]["invalid"] == "break-paths-show-domains"
        plotted = next(iter(spec["datasets"].values()))
        if keys == ("sharpe", "sortino"):
            assert all(row["value"] is None for row in plotted)
    assert months == before


def test_only_valid_double_click_id_opens_permalink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {}
    query: dict[str, str] = {}
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(st, "query_params", query)
    rows: list[viewer.Row] = [{"id": "third"}, {"id": "first"}]
    assert not app_module.open_double_clicked_configuration("stale", rows, "gate")
    assert not app_module.open_double_clicked_configuration(1, rows, "gate")
    assert query == {} and state == {}
    assert app_module.open_double_clicked_configuration("first", rows, "gate")
    assert query["config"] == "first" and state["detail_scope"] == "gate"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_native_table_double_click_uses_actual_sorted_row_and_ignores_header() -> None:
    harness = r"""
import assert from 'node:assert/strict';
let source = '';
for await (const chunk of process.stdin) source += chunk.toString();
const {default: mount} = await import('data:text/javascript,' + encodeURIComponent(source));
const handlers = {};
globalThis.document = {
  addEventListener: (name, callback) => { handlers[name] = callback; },
  removeEventListener: (name, callback) => {
    assert.equal(handlers[name], callback); delete handlers[name];
  },
};
const triggers = [];
const cleanup = mount({
  data: {config_ids: ['first', 'second', 'third'], row_height: 35},
  setTriggerValue: (name, value) => triggers.push([name, value]),
});
const scroller = {scrollTop: 0};
// Native header sorting has changed the visual order independently of Python.
const sorted = ['third', 'first', 'second'];
const canvas = {
  getBoundingClientRect: () => ({top: 100, bottom: 300, left: 10, right: 900}),
  querySelector: selector => {
    const match = /aria-rowindex="(\d+)"/.exec(selector);
    const value = sorted[Number(match?.[1]) - 2];
    return value ? {querySelectorAll: () => [{textContent: '42'}, {textContent: value}]} : null;
  },
};
const header = {getBoundingClientRect: () => ({height: 36})};
const table = {querySelector: selector => selector.includes('not(') ? header :
  selector.includes('canvas') ? canvas : scroller};
const target = {closest: () => table};
assert.equal(handlers.click, undefined);
handlers.dblclick({target, clientX: 80, clientY: 118}); // Header is not a data row.
assert.deepEqual(triggers, []);
handlers.dblclick({target, clientX: 80, clientY: 150});
assert.deepEqual(triggers, [['open_config_id', 'third']]);
scroller.scrollTop = 35;
handlers.dblclick({target, clientX: 80, clientY: 150});
assert.deepEqual(triggers.at(-1), ['open_config_id', 'first']);
handlers.dblclick({target, clientX: 950, clientY: 150});
handlers.dblclick({target, clientX: 80, clientY: 290}); // Empty space after final row.
assert.equal(triggers.length, 2);
cleanup();
assert.deepEqual(handlers, {});
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        input=app_module.MAIN_TABLE_DOUBLE_CLICK_JS,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def median_period(pnl: str, peak: str, factor: str | None) -> dict[str, Any]:
    return {
        "complete_month": True,
        "total_pnl": pnl,
        "peak_spent": peak,
        "profit_factor": factor,
        "profit_factor_status": "no_closures" if factor is None else "available",
        "net_profit_closed": factor or "0",
        "net_loss_closed": "0" if factor is None else "1",
    }


def test_medians_use_full_months_exact_percentages_and_available_pf_including_zero() -> None:
    periods = [
        median_period("1", "100", "1"),
        median_period("6", "200", "3"),
        median_period("50", "100", "0"),
        median_period("0", "0", None),
        median_period("-2", "100", None),
        dict(median_period("9999", "1", "9999"), complete_month=False),
    ]
    summary = viewer.monthly_medians(periods)
    # Returns: -2%, 1%, 3%, 50%; PF: 0, 1, 3. Neither ratio is a ratio of medians.
    assert summary["median_monthly_pnl_pct"] == Fraction(2)
    assert summary["median_monthly_profit_factor"] == Fraction(1)
    assert [summary[key] for key in viewer.LEGACY_MEDIAN_KEYS[2:]] == [5, 4, 1, 3, 2]
    assert summary["monthly_net_pnl_ratio"] == Fraction(57, 2)
    assert (summary["positive_pnl_months"], summary["negative_pnl_months"]) == (3, 1)
    assert summary["monthly_net_pnl_ratio_status"] == "available"
    assert summary["median_monthly_drawdown_pct"] is None
    assert summary["median_drawdown_status"] == "Ещё не рассчитано"


def test_median_never_loses_small_exact_differences_or_invents_missing_values() -> None:
    values = [Fraction(10**30 + i, 10**30) for i in (1, 2, 3, 4)]
    assert len({float(value) for value in values}) == 1
    result = viewer.monthly_medians(
        [median_period(str(value), "100", str(value)) for value in values]
    )
    expected = (values[1] + values[2]) / 2
    assert result["median_monthly_pnl_pct"] == expected
    assert result["median_monthly_profit_factor"] == expected
    missing = viewer.monthly_medians([median_period("0", "0", None)])
    assert missing["median_monthly_pnl_pct"] is None
    assert missing["median_monthly_profit_factor"] is None
    assert missing["median_pf_months"] == 0 and missing["median_pf_missing_months"] == 1
    assert missing["monthly_net_pnl_ratio"] is None
    assert missing["monthly_net_pnl_ratio_status"] == "no_negative_months"
    assert viewer.monthly_medians([])["monthly_net_pnl_ratio_status"] == "no_full_months"
    with pytest.raises(ValueError, match="PF"):
        viewer.monthly_medians([dict(median_period("0", "1", "2"), net_loss_closed="3")])


def test_median_index_is_exact_bound_to_source_and_read_only(
    published: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = viewer.open_archive(published)
    before = {path.name: sha(path) for path in published.parent.iterdir() if path.is_file()}
    medians = viewer.build_medians(archive)
    for config_id, scopes in medians.items():
        totals, _ = viewer.read_configuration(archive, config_id)
        for total, (venue, row) in zip(totals, scopes.items(), strict=True):
            assert all(
                total[key]
                == (
                    viewer.STATUS.get(str(value), str(value))
                    if key == "monthly_net_pnl_ratio_status"
                    else value
                )
                for key, value in row.items()
            ), venue
    output = tmp_path / "viewer-medians.bin"
    viewer.save_medians(archive, medians, output)
    assert viewer.load_medians(archive, output) == medians
    legacy = tmp_path / "viewer-medians-legacy.bin"
    with monkeypatch.context() as patcher:
        patcher.setattr(viewer, "MEDIAN_KEYS", viewer.LEGACY_MEDIAN_KEYS)
        viewer.save_medians(archive, medians, legacy)
    legacy_rows = viewer.load_medians(archive, legacy)
    assert all(
        row["monthly_net_pnl_ratio"] is None
        and row["monthly_net_pnl_ratio_status"] == "not_computed"
        for scopes in legacy_rows.values()
        for row in scopes.values()
    )
    assert before == {
        path.name: sha(path) for path in published.parent.iterdir() if path.name in before
    }
    with pytest.raises(FileExistsError):
        viewer.save_medians(archive, medians, output)
    with pytest.raises(ValueError, match="другому прогону"):
        viewer.load_medians(replace(archive, source_hashes=("other", "other", "other")), output)
    raw = output.read_bytes()
    for damaged in (raw[:12], raw[:-1], raw + b"uncommitted tail"):
        output.write_bytes(damaged)
        with pytest.raises(ValueError):
            viewer.load_medians(archive, output)


def test_complete_utc_month_flags_are_checked_against_actual_boundaries() -> None:
    period = fixture_row(0)["performance"]["total"]["monthly"][0]
    assert list(viewer._packed_periods([matrix._pack_period(period)], ("2024-01",)))
    for changed in (
        dict(period, start_ms=period["start_ms"] + 1),
        dict(period, complete_month=False),
    ):
        with pytest.raises(ValueError, match="месяца UTC"):
            list(viewer._packed_periods([matrix._pack_period(changed)], ("2024-01",)))


def test_leaders_use_exact_net_usdt_and_deterministic_ties() -> None:
    base = Fraction(10**30 + 1, 10**30)
    rows: list[viewer.Row] = [
        {"ordinal": 3, "id": "tied-later", "total_pnl": base},
        {"ordinal": 1, "id": "loss", "total_pnl": -1, "realized_gross": 100},
        {"ordinal": 2, "id": "profit", "total_pnl": base},
    ]
    best, worst = viewer.leaders(rows)
    assert best["id"] == "profit" and worst["id"] == "loss"
    rows.append({"ordinal": 4, "id": "more", "total_pnl": base + Fraction(1, 10**30)})
    assert viewer.leaders(rows)[0]["id"] == "more"


def test_median_leaders_require_same_complete_month_coverage_and_exact_values() -> None:
    base = Fraction(10**30 + 1, 10**30)
    rows: list[viewer.Row] = [
        {
            "ordinal": 3,
            "id": "missing-month",
            "median_monthly_pnl_pct": 100,
            "full_months": 51,
            "median_pnl_months": 50,
            "median_pnl_missing_months": 1,
        },
        {
            "ordinal": 2,
            "id": "exact-best",
            "median_monthly_pnl_pct": base + Fraction(1, 10**30),
            "full_months": 51,
            "median_pnl_months": 51,
            "median_pnl_missing_months": 0,
        },
        {
            "ordinal": 1,
            "id": "exact-worst",
            "median_monthly_pnl_pct": base,
            "full_months": 51,
            "median_pnl_months": 51,
            "median_pnl_missing_months": 0,
        },
    ]
    best, worst, count = viewer.median_pnl_leaders(rows)  # type: ignore[misc]
    assert (best["id"], worst["id"], count) == ("exact-best", "exact-worst", 2)
    assert viewer.median_pnl_leaders([rows[0]]) is None
    with pytest.raises(ValueError, match="Несопоставимое"):
        viewer.median_pnl_leaders([rows[0], dict(rows[1], full_months=50)])


def test_flat_hour_label_and_median_cards_only_on_matching_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    rows = [fixture_row(index) for index in range(3)]
    for row in rows:
        performance = row["performance"]
        for scope in (performance["total"], *performance["venues"]):
            for month in scope["monthly"]:
                month["peak_spent"] = "1/1"
    # The last configuration has the largest one-month median, but not full coverage.
    for scope in (rows[2]["performance"]["total"], *rows[2]["performance"]["venues"]):
        scope["monthly"][0]["peak_spent"] = "0/1"
    publication = publish_rows(tmp_path, rows)
    manifest = json.loads(publication.read_text())
    manifest.update(
        selection_policy="reviewed_gap_free_current_rules_flat_hour_five_venues",
        flat_run_minutes=59,
    )
    publication.write_text(json.dumps(manifest))
    assert viewer.open_archive(publication).flat_hour_study is False
    manifest["flat_run_minutes"] = 60
    publication.write_text(json.dumps(manifest))
    assert viewer.open_archive(publication).flat_hour_study is True
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(publication))
    monkeypatch.delenv("GRID_RESULTS_MEDIANS", raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.run(timeout=20)
    assert not app.exception
    assert len(app.metric) == 4
    assert rows[1]["id"] in app.metric[2].label
    assert rows[0]["id"] in app.metric[3].label
    assert any("Исследование с часовым фильтром" in item.value for item in app.info)
    assert any("2 из 3 конфигураций" in item.value for item in app.caption)


def test_streamlit_table_filters_sorts_and_selects_month(
    published: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AppTest's asyncio loop needs its local socketpair; internet sockets stay blocked.
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(published))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.run(timeout=20)
    assert not app.exception and len(main_table(app)["rows"]) == 3
    displayed = {column["key"] for column in main_table(app)["columns"]}
    assert {
        "id",
        "total_pnl",
        "return_pct",
        "median_monthly_pnl_pct",
        "max_drawdown_pct",
    } <= displayed
    hidden = {"spent", "peak_spent", "buy_count", "sell_count", "sharpe", "sortino"}
    assert not hidden & displayed
    assert not hidden & set(app_module.MAIN_COLUMNS)
    leader_values = [metric.value for metric in app.metric]
    assert len(leader_values) == 2
    assert len(app.get("link_button")) == 2
    next(widget for widget in app.selectbox if widget.label == "Сортировать по").set_value(
        "total_pnl"
    )
    app.checkbox[0].check().run()
    assert main_table(app)["rows"][0]["id"] == fixture_row(2)["id"]
    next(widget for widget in app.selectbox if widget.label == "Период").set_value("2024-02").run()
    assert not app.exception and len(main_table(app)["rows"]) == 3
    assert next(widget for widget in app.selectbox if widget.label == "Сортировать по").value == (
        "total_pnl"
    )
    next(widget for widget in app.multiselect if widget.label == "Поля для фильтрации").set_value(
        ["step_pct"]
    ).run()
    app.text_input(key="value_step_pct").set_value("1").run()
    assert not app.exception and len(main_table(app)["rows"]) == 0
    assert [metric.value for metric in app.metric] == leader_values
    next(widget for widget in app.selectbox if widget.label == "Период").set_value("overall").run()
    assert not app.exception and len(main_table(app)["rows"]) == 0
    assert next(
        widget for widget in app.multiselect if widget.label == "Поля для фильтрации"
    ).value == ["step_pct"]
    app.text_input(key="value_step_pct").set_value("NaN").run()
    assert not app.exception and app.error


def test_streamlit_configuration_page_shows_months_and_all_venue_totals(
    published: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(published))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.query_params["config"] = fixture_row(2)["id"]
    app.session_state["detail_scope"] = "gate"
    app.run(timeout=20)
    assert not app.exception
    assert len(app.dataframe) == 2
    assert len(app.dataframe[0].value) == 2
    assert list(app.dataframe[0].value["month"]) == ["2024-01", "2024-02"]
    assert len(app.dataframe[1].value) == 3
    assert len(app.get("vega_lite_chart")) == 6
    assert [metric.value for metric in app.metric] == [
        "Недоступно",
        "Ещё не рассчитано",
        "Не определён",
        "Нет убыточных полных месяцев",
    ]
    app.selectbox(key="detail_scope").set_value("okx").run()
    assert not app.exception and len(app.dataframe[0].value) == 2


def test_detail_permalink_opens_full_page_and_rejects_unknown_id(
    published: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(published))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.query_params["config"] = fixture_row(1)["id"]
    app.run(timeout=20)
    assert not app.exception and len(app.dataframe) == 2 and len(app.metric) == 4
    assert len(app.get("vega_lite_chart")) == 6
    assert any(heading.value == fixture_row(1)["id"] for heading in app.subheader)
    assert any("PF: учтено 0, пропущено 2" in item.value for item in app.caption)
    app.query_params["config"] = "unknown"
    app.run()
    assert not app.exception and "Неизвестная конфигурация" in app.error[0].value


def test_detail_confirmation_no_does_nothing_yes_submits_once(
    published: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import results_viewer_jobs as jobs

    pytest_socket.enable_socket()
    pytest_socket.disable_socket(allow_unix_socket=True)
    root = tmp_path / "detail-root"
    root.mkdir()
    (root / "worker.json").write_text(
        json.dumps({"publication": str(published), "code_sha256": "c", "binary_sha256": "b"})
    )
    monkeypatch.setenv("GRID_RESULTS_PUBLICATION", str(published))
    monkeypatch.setenv("GRID_DETAIL_ROOT", str(root))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "scripts/results_viewer.py"))
    app.query_params["config"] = fixture_row(1)["id"]
    app.run(timeout=20)
    app.button(key="detail_start").click().run()
    assert not app.exception
    app.button(key="confirm_detail_no").click().run()
    assert not app.exception and not (root / "jobs").exists()
    app.button(key="detail_start").click().run()
    app.button(key="confirm_detail_yes").click().run()
    assert not app.exception
    archive = viewer.open_archive(published)
    job = jobs.job_for(root, archive, fixture_row(1)["id"])
    assert jobs.status(job)["status"] == "QUEUED"
    assert len(list((root / "jobs").iterdir())) == 1
    assert jobs.submit(root, archive, fixture_row(1)["id"]) == job
    with pytest.raises(ValueError, match="другая"):
        jobs.submit(root, archive, fixture_row(2)["id"])
