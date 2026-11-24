"""Compact Streamlit viewer for one completed binary GRID research run."""

from __future__ import annotations

import os
import time
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import altair as alt
import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from scripts import research_sweep as runner
from scripts import results_viewer_data as data
from scripts import results_viewer_jobs as jobs

DEFAULT_PUBLICATION = Path(
    "/opt/grid-runs/results-viewer-ssh/flat-hour-20261122-0435/final-publication.json"
)
INTEGER_COLUMNS = frozenset(
    {
        "ordinal",
        "buy_count",
        "sell_count",
        "winning_closures",
        "losing_closures",
        "breakeven_closures",
        "sample_months",
        "global_exits",
        "exchange_exits",
        "full_months",
        "median_pnl_months",
        "median_pnl_missing_months",
        "median_pf_months",
        "median_pf_missing_months",
        "positive_pnl_months",
        "negative_pnl_months",
        "median_dd_months",
        "median_dd_missing_months",
    }
)
MAIN_COLUMNS = (
    "id",
    "step_pct",
    "tp_pct",
    "exchange_tp_pct",
    "global_tp_pct",
    "buy_floor_pct",
    "total_pnl",
    "return_pct",
    "median_monthly_pnl_pct",
    "max_drawdown_pct",
    "median_monthly_drawdown_pct",
    "profit_factor",
    "profit_factor_status",
    "median_monthly_profit_factor",
    "monthly_net_pnl_ratio",
    "net_profit_closed",
    "net_loss_closed",
    "unrealized_gross",
)
MAIN_DEFAULT_COLUMNS = MAIN_COLUMNS[:12]
MAIN_TABLE_ROW_HEIGHT = 35
MAIN_TABLE_DOUBLE_CLICK_JS = """
export default function(component) {
  const {data, setTriggerValue} = component;
  const allowed = new Set(data.config_ids);
  function openRow(event) {
    const table = event.target.closest?.('.st-key-main_results_table');
    if (!table) return;
    const canvas = table.querySelector('canvas[data-testid="data-grid-canvas"]');
    const header = table.querySelector('.dvn-underlay > canvas:not([data-testid])');
    const scroller = table.querySelector('.dvn-scroller');
    if (!canvas || !header || !scroller) return;
    const bounds = canvas.getBoundingClientRect();
    const top = bounds.top + header.getBoundingClientRect().height;
    if (event.clientY < top || event.clientY >= bounds.bottom ||
        event.clientX < bounds.left || event.clientX >= bounds.right) return;
    const rowIndex = Math.floor((event.clientY - top + scroller.scrollTop) / data.row_height);
    if (rowIndex < 0 || rowIndex >= data.config_ids.length) return;
    // The native grid's accessibility rows follow its own header sorting and scroll.
    // Resolve the actual displayed ID; never reuse a selection from an earlier click.
    const row = canvas.querySelector(`tbody tr[aria-rowindex="${rowIndex + 2}"]`);
    if (!row) return;
    const configId = Array.from(row.querySelectorAll('td'))
      .map(cell => cell.textContent).find(value => allowed.has(value));
    if (configId) setTriggerValue('open_config_id', configId);
    else setTriggerValue('missing_config_id', true);
  }
  document.addEventListener('dblclick', openRow);
  return () => document.removeEventListener('dblclick', openRow);
}
"""
MAIN_TABLE_DOUBLE_CLICK = st.components.v2.component(
    "grid_native_table_double_click_v1", js=MAIN_TABLE_DOUBLE_CLICK_JS
)
METHODS = """
**PnL, %** — чистый PnL, делённый на пик spent и умноженный на 100.
В месячной строке используются PnL и пик spent этого месяца.
**Максимальная просадка, %** — сохранённая максимальная денежная просадка за всю
историю, делённая на пик spent за всю историю. Например, 200 USDT просадки при
пике spent 1 000 USDT — это 20%. Стартовый баланс в этой модели не задан.
При нулевой базе процент отсутствует.

**Медианный месячный PnL, %** — медиана отдельных месячных PnL / пик spent
того же месяца × 100. Берём только полные календарные месяцы UTC; месяцы с
нулевым пиком spent не имеют определённого процента и пропускаются. Это
невзвешенная медиана: у каждого доступного месяца одинаковый вес.
**Медианный месячный PF закрытий** — медиана доступных PF полных месяцев. PF считается
по чистым результатам закрытых позиций после обеих комиссий; месяцы без
убыточных закрытий или без закрытий пропускаются. Число учтённых и пропущенных
месяцев показано отдельно. При чётном числе месяцев берём среднее двух
центральных значений. Медианы относятся ко всей истории выбранной области,
даже если в основной таблице выбран отдельный месяц.
**PF закрытых позиций** делит сумму прибыльных закрытий после обеих комиссий
на сумму убыточных. При отсутствии убыточных закрытий он не определён.
Обычный SELL по TP монеты всегда прибыльный в этой модели; отрицательная
переоценка незакрытых позиций остаётся в общем PnL и в этот PF не входит.
**Прибыль / убыток полных месяцев по общему PnL** — сумма положительных
изменений чистого PnL за полные месяцы UTC, делённая на сумму модулей
отрицательных. Открытые позиции переоцениваются по последней цене, поэтому этот
показатель охватывает их убытки; он не является стандартным PF закрытых сделок.
Если убыточных полных месяцев нет, отношение не определено. Рядом показано
число прибыльных и убыточных месяцев.
**Месячная просадка** — наибольшее за месяц отставание чистого накопленного
PnL от его исторического максимума, включая максимумы предыдущих месяцев.
Например, при прежнем максимуме 100 и пути 80 → 70 → 90 это 30 USDT.
Процент — от пика spent этого месяца. Подробный расчёт измеряет её по всем
O/H/L/C и после общих продаж, до сокращения графиков. Медиана использует
только полные месяцы UTC с определённым процентом. До расчёта — «Ещё не рассчитано».

**Откуда USDT без стартового баланса.** Стоимость покупки — цена в USDT,
умноженная на минимальный допустимый лот пары. Покупки не ограничены балансом;
spent показывает стоимость открытых покупок, пик spent — её одновременный
максимум. Например, для лота 1 монета: покупка по 100 и продажа по 110 дают
10 USDT до комиссий и 9,58 USDT после комиссий 0,20 + 0,22. Стартовый баланс
ограничивал бы доступность покупок, но для расчёта стоимости сделок не нужен.
Пик spent не включает комиссии и не является заданным стартовым депозитом.

**Sharpe**: берём изменения чистого PnL в USDT за полные календарные месяцы UTC,
находим среднее и делим его на выборочное стандартное отклонение всех этих
месячных результатов. **Sortino** использует то же среднее, но в знаменателе —
корень из суммы квадратов отрицательных месячных PnL, делённой на общее число
месяцев, включая положительные и нулевые. Оба результата умножаются на √12.
Безрисковая ставка и целевой результат — ноль.

В месячных строках Sharpe/Sortino накоплены от начала истории до конца строки.
Неполные месяцы входят в финансовые итоги, но исключены из выборки этих
коэффициентов. При менее чем двух полных месяцах, нулевом разбросе для Sharpe
или отсутствии отрицательных месяцев для Sortino значение отсутствует;
причина указана в столбце статуса. Это коэффициенты по месячному PnL,
а не по доходности заданного стартового капитала.

Месячные графики используют сохранённые месячные результаты. Кнопка подробного
расчёта воспроизводит только выбранную конфигурацию и добавляет траекторию
и месячные просадки после точной сверки с исходным прогоном.
"""


@st.cache_resource(max_entries=2, show_spinner="Проверяем опубликованные результаты…")
def archive(path: str, identity: Any) -> data.Archive:
    return data.open_archive(Path(path))


@st.cache_data(max_entries=6, show_spinner="Читаем результаты конфигураций…")
def view(path: str, identity: Any, venue: str, period: str) -> list[data.Row]:
    rows = data.read_rows(archive(path, identity), venue, period)
    medians = median_index(path, identity)
    for row in rows:
        row.update(medians[str(row["id"])][venue])
        status = row["monthly_net_pnl_ratio_status"]
        row["monthly_net_pnl_ratio_status"] = data.STATUS.get(str(status), str(status))
    return rows


@st.cache_resource(max_entries=2, show_spinner="Читаем медианы полных месяцев…")
def median_index(path: str, identity: Any) -> data.MedianIndex:
    saved = archive(path, identity)
    cache = os.environ.get("GRID_RESULTS_MEDIANS")
    return data.load_medians(saved, Path(cache)) if cache else data.build_medians(saved)


@st.cache_data(max_entries=8, show_spinner="Читаем выбранную конфигурацию…")
def configuration(
    path: str, identity: Any, config_id: str
) -> tuple[list[data.Row], dict[str, list[data.Row]]]:
    return data.read_configuration(archive(path, identity), config_id)


def dataframe(rows: list[data.Row], columns: list[str]) -> pd.DataFrame:
    display: dict[str, Any] = {}
    for key in columns:
        spec = data.BY_KEY[key]
        values = [row.get(key) for row in rows]
        if key in ("exchange_tp_pct", "global_tp_pct"):
            display[key] = ["выкл." if value is None else f"{float(value):g}%" for value in values]
        elif spec.kind == "number":
            display[key] = pd.array(
                [None if value is None else float(value) for value in values],
                dtype="Int64" if key in INTEGER_COLUMNS else "Float64",
            )
        elif spec.kind == "date":
            display[key] = pd.to_datetime(values, unit="ms", utc=True).tz_convert("Europe/Moscow")
        else:
            display[key] = values
    return pd.DataFrame(display)


def column_config(columns: list[str]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for key in columns:
        spec = data.BY_KEY[key]
        if key in ("exchange_tp_pct", "global_tp_pct"):
            config[key] = st.column_config.TextColumn(spec.label)
        elif spec.kind == "number":
            config[key] = st.column_config.NumberColumn(
                spec.label,
                format="%.0f%%"
                if key == "buy_floor_pct"
                else ("%.0f" if key in INTEGER_COLUMNS else "%.6f"),
            )
        elif spec.kind == "date":
            config[key] = st.column_config.DatetimeColumn(spec.label, format="DD.MM.YYYY HH:mm")
        else:
            config[key] = st.column_config.TextColumn(spec.label)
    return config


def monthly_chart(
    rows: list[data.Row], keys: tuple[str, ...], title: str, *, bars: bool = False
) -> alt.Chart:
    records = [
        {
            "month": row["month"],
            "series": data.BY_KEY[key].label,
            "value": None if row.get(key) is None else float(row[key]),  # type: ignore[arg-type]
        }
        for row in rows
        for key in keys
    ]
    months = [str(row["month"]) for row in rows]
    ticks = list(dict.fromkeys([*months[:: max(1, len(months) // 8)], *months[-1:]]))
    chart = alt.Chart(pd.DataFrame(records)).encode(
        x=alt.X(
            "month:O", title="Месяц (UTC)", sort=None, axis=alt.Axis(values=ticks, labelAngle=-45)
        ),
        y=alt.Y("value:Q", title=None),
        color=alt.Color("series:N", title=None, legend=alt.Legend(orient="bottom")),
        tooltip=[
            alt.Tooltip("month:O", title="Месяц (UTC)"),
            alt.Tooltip("series:N", title="Показатель"),
            alt.Tooltip("value:Q", title="Значение", format=",.6f"),
        ],
    )
    if bars:
        chart = chart.mark_bar().encode(
            color=alt.condition(alt.datum.value >= 0, alt.value("#17875d"), alt.value("#c74343"))
        )
    else:
        chart = chart.mark_line(point=True, invalid="break-paths-show-domains")
    return cast(alt.Chart, chart.properties(title=title, height=240))


def open_double_clicked_configuration(config_id: object, rows: list[data.Row], venue: str) -> bool:
    if type(config_id) is not str or config_id not in {str(row["id"]) for row in rows}:
        return False
    st.session_state["detail_scope"] = venue
    st.query_params["config"] = config_id
    return True


def metric_text(value: data.Scalar, suffix: str = "") -> str:
    return "Недоступно" if value is None else f"{float(value):,.4f}{suffix}"


def show_leader_shortcut(row: data.Row, key: str) -> None:
    del key
    st.link_button(f"Подробнее: {row['id']}", f"?config={row['id']}")


def show_medians(row: data.Row) -> None:
    columns = st.columns(4)
    for container, key in zip(
        columns,
        (
            "median_monthly_pnl_pct",
            "median_monthly_drawdown_pct",
            "median_monthly_profit_factor",
            "monthly_net_pnl_ratio",
        ),
        strict=True,
    ):
        value = metric_text(row[key], " %" if key.endswith("_pct") else "")
        if key == "median_monthly_drawdown_pct" and row[key] is None:
            value = str(row["median_drawdown_status"])
        if key == "median_monthly_profit_factor" and row[key] is None:
            value = "Не определён"
        if key == "monthly_net_pnl_ratio" and row[key] is None:
            status = str(row["monthly_net_pnl_ratio_status"])
            value = data.STATUS.get(status, status)
        container.metric(data.BY_KEY[key].label, value)
    st.caption(
        f"Полных месяцев UTC: {row['full_months']}. "
        f"PnL: учтено {row['median_pnl_months']}, пропущено {row['median_pnl_missing_months']}. "
        f"PF: учтено {row['median_pf_months']}, пропущено {row['median_pf_missing_months']}. "
        + (
            f"Просадка: учтено {row['median_dd_months']}, "
            f"пропущено {row['median_dd_missing_months']}."
            if row.get("median_dd_months") is not None
            else "Просадка: ещё не рассчитано."
        )
        + (
            f" Прибыльных/убыточных полных месяцев: "
            f"{row['positive_pnl_months']}/{row['negative_pnl_months']}."
        )
    )


def detail_root() -> Path:
    return Path(os.environ.get("GRID_DETAIL_ROOT", str(jobs.DEFAULT_ROOT)))


def detail_stamp(job: Path) -> tuple[data.Stamp, ...]:
    saved = jobs.status(job)
    result = jobs._checked_attempt(job, saved["result_dir"])
    return tuple(
        data.stamp(path)
        for path in (
            job / "status.json",
            result / "receipt.json",
            result / "details.bin",
            result / "details.sqlite",
        )
    )


@st.cache_data(max_entries=8, show_spinner="Проверяем подробный расчёт…")
def detail_bundle(
    path: str, identity: Any, job_path: str, stamps: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    job = Path(job_path)
    receipt = jobs.verified_receipt(job, archive(path, identity))
    if receipt is None:
        raise ValueError("Подробный расчёт ещё не завершён")
    with closing(jobs.DetailCatalog(Path(receipt["result_dir"]))) as catalog:
        rows = list(catalog.rows("configuration"))
    if len(rows) != 1 or rows[0]["id"] != receipt["config_id"]:
        raise ValueError("Неверная конфигурация в подробностях")
    jobs.verify_drawdowns(rows[0])
    return rows[0], receipt


@st.cache_data(max_entries=16, show_spinner=False)
def detail_rows(result_dir: str, kind: str, scope: str | None, stamps: Any) -> list[dict[str, Any]]:
    with closing(jobs.DetailCatalog(Path(result_dir))) as catalog:
        return list(catalog.rows(kind, scope))


@st.cache_data(max_entries=6, show_spinner=False)
def completed_overlays(
    path: str, identity: Any, root_path: str, completed_stamp: Any, venue: str, period: str
) -> dict[str, data.Row]:
    saved = archive(path, identity)
    root = Path(root_path)
    completed = runner.read_json(root / "completed.json")
    result = {}
    for job_id, entry in completed.items():
        if entry["parent_hashes"] != list(saved.source_hashes):
            continue
        job = jobs._job(root, job_id)
        if job != jobs.job_for(root, saved, entry["config_id"]):
            continue
        raw, _ = detail_bundle(path, identity, str(job), detail_stamp(job))
        totals, months = configuration(path, identity, raw["id"])
        data.apply_drawdowns(totals, months, raw["detail"])
        row = totals[("total", *saved.venues).index(venue)]
        result[raw["id"]] = {
            key: value
            for key, value in row.items()
            if key.startswith("median_d") or key == "median_monthly_drawdown_pct"
        }
        if period != "overall":
            month = next((row for row in months[venue] if row["month"] == period), None)
            if month is None:
                raise ValueError("Месяц отсутствует в подробностях выбранной биржи")
            result[raw["id"]].update(
                {key: month[key] for key in ("max_drawdown", "max_drawdown_pct", "max_drawdown_ms")}
            )
    return result


def dismiss_detail_confirmation() -> None:
    st.session_state.pop("confirm_detail_intent", None)


@st.dialog("Запустить подробный бэктест?", on_dismiss=dismiss_detail_confirmation)
def confirm_detail(
    path: str, identity: Any, config_id: str, resume: bool = False, restart: bool = False
) -> None:
    saved = archive(path, identity)
    totals, _ = configuration(path, identity, config_id)
    params = totals[0]
    st.write(
        f"Конфигурация **{config_id}**: шаг {float(cast(Any, params['step_pct'])):g}%, "
        f"TP монеты {float(cast(Any, params['tp_pct'])):g}%."
    )
    common = dataframe([params], ["exchange_tp_pct", "global_tp_pct"]).iloc[0]
    st.write(f"TP биржи: {common['exchange_tp_pct']} · Глобальный TP: {common['global_tp_pct']}")
    st.write(f"Нижняя граница BUY: {float(cast(Any, params['buy_floor_pct'])):g}%")
    st.write(
        f"Все {saved.source_count:,} пар, {len(saved.venues)} бирж, вся исходная история. "
        "Будут рассчитаны месячные просадки, графики и итоги пар."
    )
    st.caption("Расчёт продолжится после закрытия браузера. Полный журнал сделок не сохраняется.")
    if restart:
        st.caption(
            "Новый расчёт с начала истории. Прежние попытки и контрольные точки сохраняются."
        )
    elif resume:
        st.caption(
            "Используем последнюю доступную совместимую контрольную точку; "
            "при её отсутствии начнём сначала."
        )
    st.caption("Биржи: " + ", ".join(venue.upper() for venue in saved.venues))
    st.caption(
        "История UTC: "
        + str(pd.to_datetime(params["start_ms"], unit="ms", utc=True))
        + " — "
        + str(pd.to_datetime(params["end_ms"], unit="ms", utc=True))
        + " (конец не включён)"
    )
    yes, no = st.columns(2)
    if yes.button("Да", type="primary", key="confirm_detail_yes"):
        try:
            jobs.submit(detail_root(), saved, config_id, resume=resume, restart=restart)
        except (OSError, ValueError) as error:
            st.error(str(error))
        else:
            dismiss_detail_confirmation()
            st.rerun()
    if no.button("Нет", key="confirm_detail_no"):
        dismiss_detail_confirmation()
        st.rerun()


def duration_text(seconds: Any) -> str:
    if seconds is None:
        return "Оценивается"
    minutes = max(0, int(seconds)) // 60
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


@st.fragment(run_every=1)
def detail_progress(path: str, identity: Any, config_id: str) -> None:
    root = detail_root()
    if not (root / "worker.json").exists():
        st.info("Worker подробных расчётов ещё не настроен.")
        return
    try:
        job = jobs.job_for(root, archive(path, identity), config_id)
        current = jobs.progress(job)
    except (OSError, ValueError) as error:
        st.error(f"Не удалось прочитать состояние: {error}")
        return
    state = current["status"]
    labels = {
        "NOT_CALCULATED": "Ещё не рассчитано",
        "QUEUED": "Запуск принят",
        "RUNNING": "Выполняется",
        "STOPPING": "Сохраняем контрольную точку",
        "PAUSED": "Остановлено с сохранением",
        "INTERRUPTED": "Расчёт прерван",
        "COMPLETE": "Подробности рассчитаны и проверены",
        "FAILED": "Ошибка расчёта",
        "RESOURCE_LIMIT": "Достигнут предел ресурсов",
        "BLOCKED": "Запуск заблокирован",
    }
    st.write("**" + labels.get(state, state) + "**")
    if current.get("error"):
        st.error(current["error"])
    if state == "COMPLETE":
        marker = str(data.stamp(job / "status.json"))
        if st.session_state.get("detail_seen_" + config_id) != marker:
            st.session_state["detail_seen_" + config_id] = marker
            st.rerun()
        return
    if state in jobs.LIVE:
        observed = current.get("progress", {})
        stages = {
            "RUNNING": "Расчёт стратегии",
            "AUDITING": "Проверка источников",
            "VERIFYING": "Заключительная проверка и сверка",
            "CHECKPOINTING": "Сохранение контрольной точки",
            "RESTORING": "Восстановление",
            "PUBLISHING": "Проверка и сохранение бинарных подробностей",
        }
        st.caption(stages.get(observed.get("status"), "Подготовка / проверка источников"))
        processed, total = observed.get("processed_records", 0), observed.get("input_records", 0)
        if total:
            st.progress(
                min(1.0, processed / total),
                text=f"{processed:,} / {total:,} записей · {processed / total:.2%}",
            )
        elapsed = time.time() - current.get(
            "started_unix", current.get("created_unix", time.time())
        )
        left, center, right = st.columns(3)
        left.metric("Прошло", duration_text(elapsed))
        center.metric(
            "Осталось расчёта", duration_text(observed.get("estimated_replay_remaining_seconds"))
        )
        rate = observed.get("record_rate_per_second")
        right.metric("Записей / с", "Оценивается" if rate is None else f"{rate:,.0f}")
        if observed.get("last_ms"):
            st.caption(
                "История: "
                + str(
                    pd.to_datetime(observed["last_ms"], unit="ms", utc=True).tz_convert(
                        "Europe/Moscow"
                    )
                )
            )
        resource_keys = (
            ("rss_bytes", "RAM worker"),
            ("available_bytes", "Свободная RAM"),
            ("free_disk_bytes", "Свободный диск"),
            ("artifact_bytes", "Артефакты"),
        )
        st.caption(
            " · ".join(
                f"{label}: {observed[key] / 1024**3:.2f} GiB"
                for key, label in resource_keys
                if key in observed
            )
        )
        checkpoint = observed.get("latest_checkpoint_unix")
        st.caption(
            "Контрольная точка: "
            + (
                str(pd.to_datetime(checkpoint, unit="s", utc=True).tz_convert("Europe/Moscow"))
                if checkpoint
                else "пока не сохранена"
            )
        )
        if state != "STOPPING" and st.button("Остановить с сохранением", key="detail_stop"):
            jobs.stop(job)
            st.rerun(scope="fragment")
    else:
        resume = state != "NOT_CALCULATED"
        label = (
            "Рассчитать подробности и графики"
            if not resume
            else "Продолжить"
            if current.get("checkpoint_available")
            else "Повторить расчёт"
        )
        if st.button(label, type="primary", key="detail_start"):
            st.session_state["confirm_detail_intent"] = {"config_id": config_id, "resume": resume}
            st.rerun()
        if (
            resume
            and current.get("checkpoint_available")
            and st.button("Пересчитать с начала", key="detail_restart")
        ):
            st.session_state["confirm_detail_intent"] = {
                "config_id": config_id,
                "resume": True,
                "restart": True,
            }
            st.rerun()


def trajectory_chart(rows: list[dict[str, Any]], kind: str, title: str) -> alt.Chart:
    records = []
    for row in rows:
        fields = (
            ("first_ms", 0, "first"),
            ("last_ms", 1, "last"),
            (kind + "_min_ms", 2 if kind == "pnl" else 4 if kind == "spent" else 6, "min"),
            (kind + "_max_ms", 3 if kind == "pnl" else 5 if kind == "spent" else 7, "max"),
        )
        for timestamp, order, suffix in fields:
            value = row["detail"]["dd_" + suffix] if kind == "dd" else row[kind + "_" + suffix]
            moment = (
                row["detail"][timestamp]
                if kind == "dd" and suffix in ("min", "max")
                else row[timestamp]
            )
            records.append(
                {
                    "time": moment,
                    "order": row["detail"]["orders"][order],
                    "value": float(Fraction(value)),
                }
            )
    points = pd.DataFrame(records).drop_duplicates("order").sort_values("order")
    points["time"] = pd.to_datetime(points["time"], unit="ms", utc=True)
    return cast(
        alt.Chart,
        alt.Chart(points)
        .mark_line()
        .encode(
            x=alt.X("time:T", title="Месяц (UTC)", axis=alt.Axis(format="%Y-%m")),
            y=alt.Y("value:Q", title="USDT"),
            order="order:Q",
            tooltip=[
                alt.Tooltip("time:T", title="Время UTC", format="%Y-%m-%d %H:%M:%S"),
                alt.Tooltip("value:Q", title="USDT", format=",.6f"),
            ],
        )
        .properties(title=title, height=280),
    )


def configuration_body(path: str, identity: Any, config_id: str) -> None:
    try:
        overall, monthly = configuration(path, identity, config_id)
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Не удалось прочитать конфигурацию: {error}")
        return
    detailed, receipt, stamps = None, None, None
    root = detail_root()
    if (root / "worker.json").exists():
        try:
            job = jobs.job_for(root, archive(path, identity), config_id)
            if jobs.status(job)["status"] == "COMPLETE":
                stamps = detail_stamp(job)
                detailed, receipt = detail_bundle(path, identity, str(job), stamps)
                data.apply_drawdowns(overall, monthly, detailed["detail"])
        except (OSError, ValueError, KeyError) as error:
            st.error(f"Подробности не прошли проверку: {error}")
    st.subheader(config_id)
    first = overall[0]
    params = dataframe(
        [first], ["step_pct", "tp_pct", "exchange_tp_pct", "global_tp_pct", "buy_floor_pct"]
    )
    st.caption(" · ".join(f"{data.BY_KEY[key].label}: {params.iloc[0][key]}" for key in params))
    venue = st.selectbox(
        "Детализация",
        tuple(monthly),
        key="detail_scope",
        format_func=lambda name: "Весь портфель" if name == "total" else name.upper(),
    )
    show_medians(overall[tuple(monthly).index(venue)])
    selected = overall[tuple(monthly).index(venue)]
    pf = (
        metric_text(selected["profit_factor"])
        if selected["profit_factor"] is not None
        else str(selected["profit_factor_status"])
    )
    st.caption(
        "За всю историю выбранной области: "
        f"PF закрытий {pf}; прибыль закрытий {metric_text(selected['net_profit_closed'])} USDT; "
        f"убыток закрытий {metric_text(selected['net_loss_closed'])} USDT; "
        f"открытый PnL до комиссий {metric_text(selected['unrealized_gross'])} USDT; "
        f"общий чистый PnL {metric_text(selected['total_pnl'])} USDT."
    )
    detail_progress(path, identity, config_id)
    intent = st.session_state.get("confirm_detail_intent")
    if intent and intent["config_id"] == config_id:
        confirm_detail(path, identity, config_id, intent["resume"], intent.get("restart", False))
    month_tab, total_tab, pair_tab, method_tab = st.tabs(
        ["Месяцы и графики", "Итоги по биржам", "Итоги пар", "Расчёт показателей"]
    )
    with month_tab:
        rows = monthly[venue]
        if not rows:
            st.info("За выбранный период биржи нет месячных записей.")
        else:
            columns = [
                "month",
                "total_pnl",
                "return_pct",
                "pnl_cumulative",
                "cumulative_return_pct",
            ]
            columns += [
                column.key
                for column in data.COLUMNS
                if column.key in rows[0]
                and column.key
                not in (
                    *columns,
                    "ordinal",
                    "id",
                    "scope",
                    "step_pct",
                    "tp_pct",
                    "exchange_tp_pct",
                    "global_tp_pct",
                    "buy_floor_pct",
                )
            ]
            st.caption(
                f"{len(rows)} месяцев. PnL — за месяц; Sharpe/Sortino — накопленные. "
                "Неполные месяцы отмечены в таблице."
            )
            st.dataframe(
                dataframe(rows, columns),
                column_config=column_config(columns),
                hide_index=True,
                width="stretch",
                height=330,
                key="configuration_months",
            )
            st.download_button(
                "Месяцы: CSV с точными значениями",
                data.exact_csv(rows, columns),
                file_name=f"grid-{config_id}-{venue}-monthly.csv",
                mime="text/csv",
                on_click="ignore",
            )
            charts: tuple[tuple[tuple[str, ...], str, bool], ...] = (
                (("pnl_cumulative",), "Накопленный чистый PnL, USDT", False),
                (("total_pnl",), "Чистый PnL за месяц, USDT", True),
                (("spent", "peak_spent"), "Spent: конец и максимум месяца, USDT", False),
                (("return_pct",), "PnL месяца / пик spent месяца, %", True),
                (("sharpe", "sortino"), "Накопленные Sharpe / Sortino", False),
                (("profit_factor",), "Profit factor за месяц", False),
            )
            if detailed is not None:
                charts += (
                    (("max_drawdown",), "Максимальная просадка, достигнутая в месяце, USDT", True),
                    (("max_drawdown_pct",), "Месячная просадка / пик spent месяца, %", True),
                )
            for start in range(0, len(charts), 2):
                for container, (keys, title, bars) in zip(
                    st.columns(2), charts[start : start + 2], strict=True
                ):
                    with container:
                        st.altair_chart(
                            monthly_chart(rows, keys, title, bars=bars), width="stretch", height=320
                        )
            if receipt is not None:
                scope = "all" if venue == "total" else "venue:" + venue
                series = detail_rows(receipt["result_dir"], "series", scope, stamps)
                st.caption(
                    "Подробная траектория: начало, конец и экстремумы каждого интервала "
                    "в исходном порядке. Торговые расчёты и просадки используют все события."
                )
                if series:
                    for kind, title in (
                        ("pnl", "Чистый накопленный PnL"),
                        ("spent", "Стоимость открытых покупок — spent"),
                        ("dd", "Текущая просадка от исторического максимума PnL"),
                    ):
                        st.altair_chart(
                            trajectory_chart(series, kind, title), width="stretch", height=340
                        )
    with total_tab:
        columns = [
            "scope",
            *[
                key
                for key in data.DEFAULT_COLUMNS
                if key
                not in (
                    "id",
                    "step_pct",
                    "tp_pct",
                    "exchange_tp_pct",
                    "global_tp_pct",
                    "buy_floor_pct",
                )
            ],
        ]
        columns += [
            column.key
            for column in data.COLUMNS
            if column.key in first
            and column.key
            not in (
                *columns,
                "ordinal",
                "id",
                "step_pct",
                "tp_pct",
                "exchange_tp_pct",
                "global_tp_pct",
                "buy_floor_pct",
            )
        ]
        st.dataframe(
            dataframe(overall, columns),
            column_config=column_config(columns),
            hide_index=True,
            width="stretch",
            key="configuration_totals",
        )
        st.download_button(
            "Итоги: CSV с точными значениями",
            data.exact_csv(overall, columns),
            file_name=f"grid-{config_id}-totals.csv",
            mime="text/csv",
            on_click="ignore",
        )
    with method_tab:
        st.markdown(METHODS)
    with pair_tab:
        if receipt is None:
            st.info("Итоги всех пар появятся после подробного расчёта.")
        else:
            pairs = detail_rows(
                receipt["result_dir"], "pair", None if venue == "total" else venue, stamps
            )
            pair_rows = [
                data._row(dict(row, id=row["config_id"]), i) for i, row in enumerate(pairs)
            ]
            query = st.text_input("Найти пару", key="detail_pair_search").strip().casefold()
            pair_rows = [row for row in pair_rows if query in str(row["source_id"]).casefold()]
            columns = [
                "source_id",
                "total_pnl",
                "return_pct",
                "max_drawdown_pct",
                "spent",
                "peak_spent",
                "fees",
                "buy_count",
                "sell_count",
                "open_qty",
                "last_price",
                "last_price_ms",
                "trade_hash",
            ]
            st.caption(
                f"Показано {len(pair_rows):,} из {len(pairs):,} пар. "
                "Заголовки столбцов поддерживают сортировку."
            )
            st.dataframe(
                dataframe(pair_rows, columns),
                column_config=column_config(columns),
                hide_index=True,
                width="stretch",
                height=520,
            )
            st.download_button(
                "Пары: CSV с точными значениями",
                data.exact_csv(pair_rows, columns),
                file_name=f"grid-{config_id}-{venue}-pairs.csv",
                mime="text/csv",
                on_click="ignore",
            )


def show_leaders(rows: list[data.Row]) -> None:
    best, worst = data.leaders(rows)
    for container, row, title in zip(
        st.columns(2), (best, worst), ("Самый высокий PnL", "Самый низкий PnL"), strict=True
    ):
        with container:
            st.metric(f"{title} · {row['id']}", metric_text(row["total_pnl"], " USDT"))
            st.caption(f"PnL / пик spent: {metric_text(row['return_pct'], ' %')}")
            show_leader_shortcut(row, f"total_pnl_leader_{title}_{row['id']}")
    st.caption(
        "Максимум и минимум чистого PnL всего портфеля за всю историю среди всех "
        "конфигураций. Фильтры таблицы на этот выбор не влияют."
    )


def show_median_pnl_leaders(rows: list[data.Row]) -> None:
    ranked = data.median_pnl_leaders(rows)
    if ranked is None:
        st.info("Нет конфигураций с месячным PnL % за каждый полный месяц UTC.")
        return
    best, worst, comparable_count = ranked
    for container, row, title in zip(
        st.columns(2),
        (best, worst),
        ("Наибольшая медиана месячного PnL", "Наименьшая медиана месячного PnL"),
        strict=True,
    ):
        with container:
            st.metric(
                f"{title} · {row['id']}",
                metric_text(row["median_monthly_pnl_pct"], " %"),
            )
            st.caption(
                f"Полных месяцев UTC: {row['full_months']}; "
                f"учтено {row['median_pnl_months']}, "
                f"пропущено {row['median_pnl_missing_months']}."
            )
            show_leader_shortcut(row, f"monthly_pnl_leader_{title}_{row['id']}")
    st.caption(
        f"Сравнение охватывает {comparable_count:,} из {len(rows):,} конфигураций: "
        "только те, где PnL % определён за каждый полный месяц UTC. "
        "Фильтры таблицы на этот выбор не влияют."
    )


def main() -> None:
    st.set_page_config(page_title="GRID — результаты", page_icon="📊", layout="wide")
    st.title("GRID — результаты конфигураций")
    path = str(Path(os.environ.get("GRID_RESULTS_PUBLICATION", str(DEFAULT_PUBLICATION))).resolve())
    try:
        identity = (
            data.signature(Path(path)),
            data.stamp(Path(__file__)),
            data.stamp(Path(data.__file__)),
            data.stamp(Path(jobs.__file__)),
        )
        saved = archive(path, identity)
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Не удалось открыть результаты: {error}")
        st.stop()
    st.caption(
        f"{len(saved.entries):,} конфигураций · {saved.source_count:,} пар · "
        f"{len(saved.venues)} бирж · исследовательская симуляция · полный прогон завершён"
    )
    st.caption(
        f"Источник: {saved.publication.parent.name} · SHA-256 исследования: "
        f"{saved.source_hashes[0]}"
    )
    if saved.flat_hour_study:
        st.info(
            "Исследование с часовым фильтром: исключены пары с 60 подряд минутами "
            "одинаковой цены O=H=L=C. Официальные архивы не проверялись; "
            "устаревшие цены на участках короче часа всё ещё возможны."
        )
    if selected_config := st.query_params.get("config"):
        st.link_button("← Все конфигурации", "./")
        if selected_config not in {row[1] for row in saved.entries}:
            st.error("Неизвестная конфигурация")
            return
        configuration_body(path, identity, selected_config)
        return
    try:
        portfolio_overall = view(path, identity, "total", "overall")
        show_leaders(portfolio_overall)
        show_median_pnl_leaders(portfolio_overall)
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Не удалось прочитать итоги: {error}")
        st.stop()
    left, right = st.columns(2)
    venue = left.selectbox(
        "Результат",
        ("total", *saved.venues),
        format_func=lambda name: "Весь портфель" if name == "total" else name.upper(),
        key="selected_venue",
    )
    periods = ("overall", *saved.scope_months[venue])
    if st.session_state.get("selected_period") not in periods:
        st.session_state["selected_period"] = "overall"
    period = right.selectbox(
        "Период",
        periods,
        format_func=lambda name: "Вся история" if name == "overall" else name,
        key="selected_period",
    )
    try:
        rows = view(path, identity, venue, period)
        completed = detail_root() / "completed.json"
        if completed.exists():
            overlays = completed_overlays(
                path, identity, str(detail_root()), data.stamp(completed), venue, period
            )
            for row in rows:
                row.update(overlays.get(str(row["id"]), {}))
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Не удалось прочитать таблицу: {error}")
        st.stop()
    available = [column.key for column in data.COLUMNS if any(column.key in row for row in rows)]

    def label(key: str) -> str:
        return data.BY_KEY[key].label

    filters: list[tuple[str, str, data.Scalar, data.Scalar]] = []
    with st.sidebar:
        st.subheader("Фильтры")
        selected = st.multiselect(
            "Поля для фильтрации", available, format_func=label, key="selected_filters"
        )
        try:
            for key in selected:
                spec = data.BY_KEY[key]
                choices = data.TEXT_OPERATORS if spec.kind == "text" else data.OPERATORS
                op = st.selectbox(spec.label, choices, key="operator_" + key)
                if op in ("Нет значения", "Есть значение"):
                    filters.append((key, op, None, None))
                    continue
                text = st.text_input(
                    "Значение",
                    key="value_" + key,
                    placeholder="2024-01-01" if spec.kind == "date" else None,
                )
                upper = st.text_input("До", key="upper_" + key) if op == "Между" else ""
                if text.strip() and (op != "Между" or upper.strip()):
                    filters.append(
                        (
                            key,
                            op,
                            data.parse_value(text, spec),
                            data.parse_value(upper, spec) if op == "Между" else None,
                        )
                    )
            filtered = data.filter_rows(rows, filters)
        except (ValueError, TypeError) as error:
            st.error(str(error))
            st.stop()
        st.divider()
        sort_key = st.selectbox("Сортировать по", available, format_func=label, key="sort_column")
        descending = st.checkbox("По убыванию", value=False, key="sort_descending")
        visible_options = [key for key in MAIN_COLUMNS if key in available]
        if "visible_columns" in st.session_state:
            cleaned = [key for key in st.session_state["visible_columns"] if key in visible_options]
            if cleaned != st.session_state["visible_columns"]:
                st.session_state["visible_columns"] = cleaned
            default_columns: list[str] | None = None
        else:
            default_columns = [key for key in MAIN_DEFAULT_COLUMNS if key in visible_options]
        visible = st.multiselect(
            "Столбцы таблицы",
            visible_options,
            default=default_columns,
            format_func=label,
            key="visible_columns",
        )
    filtered = data.sort_rows(filtered, sort_key, descending)
    st.caption(f"Показано {len(filtered):,} из {len(rows):,} конфигураций. Даты — по Москве.")
    st.caption(
        "Медианы — по полным месяцам UTC всей истории выбранного портфеля / биржи. "
        "Месячные просадки добавляются после подробного расчёта выбранной конфигурации."
    )
    st.caption(
        "PF закрытий не учитывает открытые позиции. Отношение прибыльных и убыточных "
        "месяцев использует общий чистый PnL с переоценкой открытых позиций. "
        "Отсутствие убыточных закрытий означает неопределённый PF, а не нулевой риск."
    )
    if period != "overall":
        st.caption(
            "PnL и комиссии — за выбранный месяц. Sharpe/Sortino — накопленные до его конца."
        )
    if not visible:
        st.info("Выберите хотя бы один столбец в боковой панели.")
        return
    # Keep the native grid, including header sorting, resizing, search and scrolling.
    # Its fixed ID column lets the double-click bridge follow browser-side sorting.
    table_columns = ["id", *(key for key in visible if key != "id")]
    settings = column_config(table_columns)
    settings["id"] = st.column_config.TextColumn(data.BY_KEY["id"].label, pinned=True)
    with st.container(key="main_results_table"):
        st.dataframe(
            dataframe(filtered, table_columns),
            key="main_results_dataframe",
            column_config=settings,
            hide_index=True,
            width="stretch",
            height=640,
            row_height=MAIN_TABLE_ROW_HEIGHT,
            on_select="ignore",
        )
    event = MAIN_TABLE_DOUBLE_CLICK(
        key="main_results_table_double_click",
        data={
            "config_ids": [str(row["id"]) for row in filtered],
            "row_height": MAIN_TABLE_ROW_HEIGHT,
        },
        height=0,
        on_open_config_id_change=lambda: None,
        on_missing_config_id_change=lambda: None,
    )
    if event.missing_config_id:
        st.warning("Верните столбец «Конфигурация» в таблицу, чтобы открыть строку двойным кликом.")
    if event.open_config_id:
        if open_double_clicked_configuration(event.open_config_id, filtered, venue):
            st.rerun()
        st.warning("Строка изменилась после выбора. Повторите двойной клик.")
    st.caption(
        "Отображение округлено. Фильтры и сортировка в боковой панели используют точные значения."
    )
    st.caption(
        "Один клик выделяет ячейку; двойной клик по строке открывает итоги, месячную таблицу "
        "и графики конфигурации. Точная сортировка и фильтрация всех полей — в боковой панели."
    )
    st.download_button(
        "CSV с точными значениями",
        data.exact_csv(filtered, visible),
        file_name=f"grid-{venue}-{period}.csv",
        mime="text/csv",
        on_click="ignore",
    )
    with st.expander("Как считаются USDT, проценты, медианы, Sharpe и Sortino"):
        st.markdown(METHODS)


if __name__ == "__main__":
    main()
