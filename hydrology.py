from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Tuple
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


@dataclass
class RainEvent:
    index: int
    start: pd.Timestamp
    last_rain: pd.Timestamp
    end_exclusive: pd.Timestamp
    duration_min: float
    total_mm: float
    interval_min: float
    n_rainy_intervals: int


@dataclass
class EventMetrics:
    start: pd.Timestamp
    last_rain: pd.Timestamp
    duration_min: float
    total_precip_mm: float
    peak_interval_mm: float
    peak_intensity_mmh: float
    peak_rain_time: pd.Timestamp
    rainfall_centroid: pd.Timestamp
    water_peak: float
    water_peak_time: pd.Timestamp
    water_unit: str
    lag_min: float
    tc_min: float
    baseline_water: Optional[float]
    water_rise: Optional[float]
    rain_interval_min: float


def estimate_interval_minutes(series_df: pd.DataFrame) -> float:
    if len(series_df) < 2:
        return 5.0
    diffs = (
        series_df["datetime"]
        .sort_values()
        .drop_duplicates()
        .diff()
        .dt.total_seconds()
        .div(60)
        .dropna()
    )
    diffs = diffs[(diffs > 0) & (diffs < 24 * 60)]
    if diffs.empty:
        return 5.0
    return float(diffs.median())


def normalize_precipitation_to_depth(
    precip: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """
    Convert precipitation-rate data (for example mm/h) to precipitation depth
    per logger interval (mm). ECRN/ZENTRA exports may expose a rate stream.
    Hydrological event totals must sum interval depths, not rates.

    Returns (converted_dataframe, note). If no conversion is needed, note is None.
    """
    if precip.empty:
        return precip.copy(), None

    out = precip.copy()
    unit_values = out["unit"].dropna().astype(str).str.strip()
    unit = unit_values.iloc[0] if not unit_values.empty else ""
    unit_l = unit.lower().replace(" ", "")

    interval_min = estimate_interval_minutes(out)

    # Common forms used by ZENTRA/user unit preferences.
    is_mm_per_hour = (
        "mm/h" in unit_l
        or "mm/hr" in unit_l
        or "mmh-1" in unit_l
        or "mmh⁻¹" in unit_l
        or "mmhour" in unit_l
    )

    if is_mm_per_hour:
        original_unit = unit
        out["value"] = pd.to_numeric(out["value"], errors="coerce") * interval_min / 60.0
        out["unit"] = "mm"
        note = (
            f"Converted precipitation from {original_unit} to interval depth (mm) "
            f"using the detected {interval_min:g}-min logger interval. "
            "This prevents rainfall rates from being summed as rainfall depths."
        )
        return out, note

    # Already a depth series. Preserve it.
    return out, None


def prepare_series(
    df: pd.DataFrame,
    measurement: str,
    sensor_name: str,
    port_num: int,
    position,
    local_tz: str,
    aggregation: str,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["datetime", "value", "unit"])

    mask = (
        (df["measurement"].astype(str) == str(measurement))
        & (df["sensor_name"].astype(str) == str(sensor_name))
        & (pd.to_numeric(df["port_num"], errors="coerce") == int(port_num))
    )

    # ZENTRA v4 may return sensor position/depth as a number, text
    # (for example "0.2 m"), an empty string, or None. Match robustly.
    if position is None or (not isinstance(position, (list, dict, tuple)) and pd.isna(position)):
        mask &= df["position"].isna() | (df["position"].astype(str).str.strip() == "")
    else:
        # First try numeric matching. Extract the first numeric token from text
        # so values such as "0.2 m" still match 0.2.
        pos_series_raw = df["position"]
        pos_series_num = pd.to_numeric(pos_series_raw, errors="coerce")

        target_num = None
        try:
            target_num = float(position)
        except (TypeError, ValueError):
            m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(position))
            if m:
                try:
                    target_num = float(m.group(0))
                except ValueError:
                    target_num = None

        if target_num is not None:
            numeric_mask = pd.Series(
                np.isclose(
                    pos_series_num.to_numpy(dtype=float),
                    target_num,
                    rtol=1e-9,
                    atol=1e-12,
                    equal_nan=False,
                ),
                index=df.index,
            )
            # Also allow exact text equality as a fallback.
            text_mask = (
                pos_series_raw.astype(str).str.strip().str.lower()
                == str(position).strip().lower()
            )
            mask &= (numeric_mask | text_mask)
        else:
            # Non-numeric positions/depth labels: compare as normalized text.
            mask &= (
                pos_series_raw.astype(str).str.strip().str.lower()
                == str(position).strip().lower()
            )

    x = df.loc[mask].copy()
    x = x[(x["error_code"] == 0) & x["value"].notna() & x["datetime"].notna()]
    if x.empty:
        return pd.DataFrame(columns=["datetime", "value", "unit"])

    x["datetime"] = x["datetime"].dt.tz_convert(local_tz)
    unit = x["unit"].dropna().astype(str)
    unit = unit.iloc[0] if not unit.empty else ""

    if aggregation == "sum":
        x = x.groupby("datetime", as_index=False)["value"].sum()
    else:
        x = x.groupby("datetime", as_index=False)["value"].mean()

    x["unit"] = unit
    return x.sort_values("datetime").reset_index(drop=True)


def detect_rain_events(
    precip: pd.DataFrame,
    threshold_mm: float = 0.0,
    dry_gap_minutes: float = 30.0,
) -> List[RainEvent]:
    if precip.empty:
        return []

    interval = estimate_interval_minutes(precip)
    rainy = precip.loc[precip["value"] > threshold_mm, ["datetime", "value"]].copy()
    rainy = rainy.sort_values("datetime").reset_index(drop=True)
    if rainy.empty:
        return []

    event_ids = [0]
    current = 0
    for i in range(1, len(rainy)):
        gap = (rainy.loc[i, "datetime"] - rainy.loc[i - 1, "datetime"]).total_seconds() / 60
        if gap > dry_gap_minutes:
            current += 1
        event_ids.append(current)
    rainy["event_id"] = event_ids

    out: List[RainEvent] = []
    for event_id, g in rainy.groupby("event_id"):
        start = g["datetime"].min()
        last_rain = g["datetime"].max()
        end_exclusive = last_rain + pd.Timedelta(minutes=interval)
        duration = (end_exclusive - start).total_seconds() / 60
        out.append(
            RainEvent(
                index=int(event_id),
                start=start,
                last_rain=last_rain,
                end_exclusive=end_exclusive,
                duration_min=float(duration),
                total_mm=float(g["value"].sum()),
                interval_min=float(interval),
                n_rainy_intervals=int(len(g)),
            )
        )
    return out


def analyze_event(
    precip: pd.DataFrame,
    water: pd.DataFrame,
    event: RainEvent,
    water_after_minutes: float = 180.0,
    baseline_before_minutes: float = 30.0,
) -> EventMetrics:
    if precip.empty or water.empty:
        raise ValueError("Precipitation and water-level series are both required.")

    interval = event.interval_min

    rain_event = precip[
        (precip["datetime"] >= event.start)
        & (precip["datetime"] <= event.last_rain)
        & (precip["value"] > 0)
    ].copy()
    if rain_event.empty:
        raise ValueError("No positive precipitation values found in the selected event.")

    total = float(rain_event["value"].sum())
    peak_idx = rain_event["value"].idxmax()
    peak_interval_mm = float(rain_event.loc[peak_idx, "value"])
    peak_rain_time = rain_event.loc[peak_idx, "datetime"]
    peak_intensity = peak_interval_mm * 60.0 / interval

    # Weighted centroid. Use POSIX seconds explicitly rather than astype("int64"),
    # because pandas may store timezone-aware datetimes internally at ns/us/ms
    # resolution. Dividing a microsecond integer by 1e9 would incorrectly push
    # the centroid back toward 1970 and create a gigantic t_lag.
    weights = rain_event["value"].to_numpy(dtype=float)
    seconds = np.array(
        [pd.Timestamp(ts).timestamp() for ts in rain_event["datetime"]],
        dtype=float,
    )
    centroid_seconds = float(np.average(seconds, weights=weights))
    centroid = pd.Timestamp.fromtimestamp(centroid_seconds, tz="UTC")
    centroid = centroid.tz_convert(event.start.tz)
    centroid = centroid + pd.Timedelta(minutes=interval / 2.0)

    # Sanity check: a rainfall centroid must lie inside (or at most one interval
    # around) the selected rainfall event.
    centroid_lo = event.start - pd.Timedelta(minutes=interval)
    centroid_hi = event.end_exclusive + pd.Timedelta(minutes=interval)
    if not (centroid_lo <= centroid <= centroid_hi):
        raise ValueError(
            "Calculated rainfall centroid falls outside the rainfall event. "
            "Check timestamp/timezone parsing."
        )

    water_search_end = event.last_rain + pd.Timedelta(minutes=water_after_minutes)
    w_peak = water[
        (water["datetime"] >= event.start)
        & (water["datetime"] <= water_search_end)
    ].copy()
    if w_peak.empty:
        raise ValueError(
            "No water-level values were found from rainfall start through the "
            "post-event search window."
        )

    max_idx = w_peak["value"].idxmax()
    water_peak = float(w_peak.loc[max_idx, "value"])
    water_peak_time = w_peak.loc[max_idx, "datetime"]
    water_unit = str(w_peak.loc[max_idx, "unit"]) if "unit" in w_peak else ""

    baseline_window = water[
        (water["datetime"] >= event.start - pd.Timedelta(minutes=baseline_before_minutes))
        & (water["datetime"] < event.start)
    ]
    baseline = (
        float(baseline_window["value"].median())
        if not baseline_window.empty
        else None
    )
    rise = (water_peak - baseline) if baseline is not None else None

    lag_min = (water_peak_time - centroid).total_seconds() / 60.0
    tc_min = (water_peak_time - event.start).total_seconds() / 60.0

    return EventMetrics(
        start=event.start,
        last_rain=event.last_rain,
        duration_min=event.duration_min,
        total_precip_mm=total,
        peak_interval_mm=peak_interval_mm,
        peak_intensity_mmh=peak_intensity,
        peak_rain_time=peak_rain_time,
        rainfall_centroid=centroid,
        water_peak=water_peak,
        water_peak_time=water_peak_time,
        water_unit=water_unit,
        lag_min=float(lag_min),
        tc_min=float(tc_min),
        baseline_water=baseline,
        water_rise=rise,
        rain_interval_min=interval,
    )


def _format_time(ts: pd.Timestamp) -> str:
    return ts.strftime("%H:%M")


def build_event_figure(
    precip: pd.DataFrame,
    water: pd.DataFrame,
    event: RainEvent,
    metrics: Optional[EventMetrics],
    before_minutes: float = 30,
    after_minutes: float = 180,
    annotated: bool = True,
):
    start_plot = event.start - pd.Timedelta(minutes=before_minutes)
    end_plot = event.last_rain + pd.Timedelta(minutes=after_minutes)

    p = precip[
        (precip["datetime"] >= start_plot) & (precip["datetime"] <= end_plot)
    ].copy()
    w = water[
        (water["datetime"] >= start_plot) & (water["datetime"] <= end_plot)
    ].copy()

    p_unit = p["unit"].iloc[0] if not p.empty and "unit" in p else "mm"
    w_unit = w["unit"].iloc[0] if not w.empty and "unit" in w else ""

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    bar_width_ms = event.interval_min * 60 * 1000 * 0.68
    fig.add_trace(
        go.Bar(
            x=p["datetime"],
            y=p["value"],
            name=f"Precipitation ({p_unit})",
            marker_color="#F9C400",
            width=bar_width_ms,
            opacity=0.95,
            hovertemplate="%{x|%d %b %H:%M}<br>Precipitation: %{y:.3f} " + p_unit + "<extra></extra>",
        ),
        secondary_y=False,
    )

    fig.add_trace(
        go.Scatter(
            x=w["datetime"],
            y=w["value"],
            name=f"Water level ({w_unit})",
            mode="lines",
            line=dict(color="#D45A00", width=2.6),
            hovertemplate="%{x|%d %b %H:%M}<br>Water level: %{y:.2f} " + w_unit + "<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_yaxes(title_text=f"Precipitation ({p_unit})", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text=f"Water level ({w_unit})", secondary_y=True)

    fig.update_xaxes(
        title_text="Local time",
        showgrid=False,
        tickformat="%d %b %H:%M",
        range=[start_plot, end_plot],
    )

    fig.update_layout(
        height=720,
        barmode="overlay",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="center", x=0.5),
        margin=dict(l=65, r=75, t=150 if annotated else 80, b=65),
        template="plotly_white",
        title=None,
    )

    if not annotated or metrics is None:
        return fig

    blue = "#0B4CCB"
    red = "#E01B16"

    # Vertical markers
    for x, color, dash in [
        (metrics.start, blue, "dash"),
        (metrics.rainfall_centroid, blue, "dash"),
        (metrics.last_rain, blue, "dash"),
        (metrics.water_peak_time, red, "dash"),
    ]:
        fig.add_vline(x=x, line_dash=dash, line_width=2, line_color=color, opacity=0.9)

    # Horizontal timing guides in paper coordinates
    fig.add_shape(
        type="line",
        x0=metrics.start,
        x1=metrics.water_peak_time,
        y0=1.10,
        y1=1.10,
        xref="x",
        yref="paper",
        line=dict(color=blue, width=2),
    )
    fig.add_annotation(
        x=metrics.start + (metrics.water_peak_time - metrics.start) / 2,
        y=1.115,
        xref="x",
        yref="paper",
        text=f"<b>Concentration time T<sub>c</sub> ≈ {metrics.tc_min:.0f} min</b>",
        showarrow=False,
        font=dict(color=blue, size=15),
    )

    fig.add_shape(
        type="line",
        x0=metrics.rainfall_centroid,
        x1=metrics.water_peak_time,
        y0=1.02,
        y1=1.02,
        xref="x",
        yref="paper",
        line=dict(color=blue, width=2),
    )
    fig.add_annotation(
        x=metrics.rainfall_centroid + (metrics.water_peak_time - metrics.rainfall_centroid) / 2,
        y=1.035,
        xref="x",
        yref="paper",
        text=f"<b>t<sub>lag</sub> ≈ {metrics.lag_min:.0f} min</b>",
        showarrow=False,
        font=dict(color=blue, size=14),
    )

    fig.add_shape(
        type="line",
        x0=metrics.start,
        x1=metrics.last_rain + pd.Timedelta(minutes=metrics.rain_interval_min),
        y0=0.03,
        y1=0.03,
        xref="x",
        yref="paper",
        line=dict(color=blue, width=2),
    )
    fig.add_annotation(
        x=metrics.start + (
            (metrics.last_rain + pd.Timedelta(minutes=metrics.rain_interval_min)) - metrics.start
        ) / 2,
        y=0.055,
        xref="x",
        yref="paper",
        text=f"<b>Duration = {metrics.duration_min:.0f} min "
             f"({_format_time(metrics.start)}–{_format_time(metrics.last_rain)})</b>",
        showarrow=False,
        font=dict(color=blue, size=13),
    )

    # Labels
    fig.add_annotation(
        x=metrics.start,
        y=0.11,
        xref="x",
        yref="paper",
        text=f"<b>Rainfall start<br>{_format_time(metrics.start)}</b>",
        showarrow=False,
        bgcolor="rgba(255,255,255,0.9)",
        bordercolor=blue,
        borderwidth=1.5,
        font=dict(color=blue, size=12),
    )

    fig.add_annotation(
        x=metrics.last_rain,
        y=0.11,
        xref="x",
        yref="paper",
        text=f"<b>Rainfall end<br>{_format_time(metrics.last_rain)}</b>",
        showarrow=False,
        bgcolor="rgba(255,255,255,0.9)",
        bordercolor=blue,
        borderwidth=1.5,
        font=dict(color=blue, size=12),
    )

    fig.add_annotation(
        x=metrics.rainfall_centroid,
        y=0.67,
        xref="x",
        yref="paper",
        text=f"<b>Rainfall centroid ≈ {_format_time(metrics.rainfall_centroid)}</b>",
        showarrow=False,
        bgcolor="rgba(255,255,255,0.92)",
        bordercolor=blue,
        borderwidth=1.5,
        font=dict(color=blue, size=12),
    )

    # Peak rainfall arrow
    ymax_p = max(float(p["value"].max()) if not p.empty else 1.0, 0.001)
    fig.add_annotation(
        x=metrics.peak_rain_time,
        y=metrics.peak_interval_mm,
        xref="x",
        yref="y",
        text=(
            f"<b>Peak intensity i<sub>max</sub><br>"
            f"= {metrics.peak_interval_mm:.2f} mm/{metrics.rain_interval_min:.0f} min<br>"
            f"= {metrics.peak_intensity_mmh:.1f} mm h⁻¹</b>"
        ),
        showarrow=True,
        arrowhead=2,
        ax=-120,
        ay=40,
        bgcolor="rgba(255,255,255,0.95)",
        bordercolor=blue,
        borderwidth=1.5,
        font=dict(color=blue, size=12),
    )

    # Peak water arrow on secondary y axis
    fig.add_annotation(
        x=metrics.water_peak_time,
        y=metrics.water_peak,
        xref="x",
        yref="y2",
        text=(
            f"<b>Peak water level = {metrics.water_peak:.1f} {metrics.water_unit}<br>"
            f"at {_format_time(metrics.water_peak_time)}</b>"
        ),
        showarrow=True,
        arrowhead=2,
        ax=115,
        ay=25,
        bgcolor="rgba(255,245,245,0.98)",
        bordercolor=red,
        borderwidth=1.5,
        font=dict(color=red, size=12),
    )

    params_text = (
        "<b>Extracted parameters</b><br>"
        f"• Total rainfall = {metrics.total_precip_mm:.2f} mm<br>"
        f"• Duration = {metrics.duration_min:.0f} min<br>"
        f"• i<sub>max</sub> = {metrics.peak_interval_mm:.2f} mm/"
        f"{metrics.rain_interval_min:.0f} min "
        f"({metrics.peak_intensity_mmh:.1f} mm h⁻¹)<br>"
        f"• t<sub>lag</sub> ≈ {metrics.lag_min:.0f} min<br>"
        f"• T<sub>c</sub> ≈ {metrics.tc_min:.0f} min"
    )
    fig.add_annotation(
        x=0.985,
        y=0.93,
        xref="paper",
        yref="paper",
        text=params_text,
        showarrow=False,
        align="left",
        bgcolor="rgba(255,255,255,0.92)",
        bordercolor="#555",
        borderwidth=1,
        font=dict(size=12, color="#111"),
    )

    return fig


def metrics_to_frame(metrics: EventMetrics) -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("rainfall_start", metrics.start.isoformat(), ""),
            ("rainfall_last_positive_interval", metrics.last_rain.isoformat(), ""),
            ("duration", metrics.duration_min, "min"),
            ("total_precipitation", metrics.total_precip_mm, "mm"),
            ("peak_interval_precipitation", metrics.peak_interval_mm, "mm"),
            ("peak_intensity", metrics.peak_intensity_mmh, "mm/h"),
            ("peak_rain_time", metrics.peak_rain_time.isoformat(), ""),
            ("rainfall_centroid", metrics.rainfall_centroid.isoformat(), ""),
            ("peak_water_level", metrics.water_peak, metrics.water_unit),
            ("peak_water_level_time", metrics.water_peak_time.isoformat(), ""),
            ("t_lag", metrics.lag_min, "min"),
            ("Tc_operational", metrics.tc_min, "min"),
            ("baseline_water_level", metrics.baseline_water, metrics.water_unit),
            ("water_level_rise", metrics.water_rise, metrics.water_unit),
        ],
        columns=["parameter", "value", "unit"],
    )
