from __future__ import annotations

from datetime import date, datetime, time, timedelta
import os

import pandas as pd
import streamlit as st

from zentra_api import ZentraV4Client, ZentraAPIError
from hydrology import (
    analyze_event,
    build_event_figure,
    detect_rain_events,
    metrics_to_frame,
    normalize_precipitation_to_depth,
    prepare_series,
)


PRECIP_DEVICE_DEFAULT = "z6-10438"
WATER_DEVICE_DEFAULT = "z6-10439"
LOCAL_TZ_DEFAULT = "Europe/Bucharest"

SERVERS = {
    "EU server (zentracloud.eu)": "https://zentracloud.eu",
    "US server (zentracloud.com)": "https://zentracloud.com",
}


st.set_page_config(
    page_title="ZENTRA Hydro Event Analyzer",
    page_icon="🌧️",
    layout="wide",
)

st.title("🌧️ ZENTRA Hydro Event Analyzer")
st.caption(
    "ZENTRA Cloud 1.0 / API v4 · precipitation from one logger + water level "
    "from another · automatic rainfall-event analysis."
)


def secret_or_env(name: str) -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def local_midnight(d: date, tz: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, time.min), tz=tz)


def make_series_options(df: pd.DataFrame):
    if df.empty:
        return []

    cols = ["measurement", "sensor_name", "port_num", "position", "unit"]
    u = df[cols].drop_duplicates().copy()
    u["position_sort"] = pd.to_numeric(u["position"], errors="coerce")
    u = u.sort_values(["measurement", "sensor_name", "port_num", "position_sort"])

    options = []
    for row in u.itertuples(index=False):
        position = row.position
        pos_txt = "" if pd.isna(position) else f", position={position}"
        label = (
            f"{row.measurement} | {row.sensor_name} | "
            f"port {row.port_num}{pos_txt} | {row.unit}"
        )
        options.append(
            {
                "label": label,
                "measurement": row.measurement,
                "sensor_name": row.sensor_name,
                "port_num": int(row.port_num),
                "position": position,
                "unit": row.unit,
            }
        )
    return options


def best_option_index(options, measurement_terms, sensor_terms):
    if not options:
        return 0
    scores = []
    for opt in options:
        m = str(opt["measurement"]).lower()
        s = str(opt["sensor_name"]).lower()
        score = 0
        score += 10 * sum(term.lower() in m for term in measurement_terms)
        score += 3 * sum(term.lower() in s for term in sensor_terms)
        scores.append(score)
    return int(max(range(len(scores)), key=scores.__getitem__))


with st.sidebar:
    st.header("Connection")

    api_server_label = st.selectbox(
        "ZENTRA Cloud 1.0 server",
        list(SERVERS.keys()),
        index=0,
        help=(
            "Choose the same regional server you use to sign in to ZENTRA Cloud 1.0. "
            "Romanian/EU accounts are usually on zentracloud.eu."
        ),
    )
    api_server = SERVERS[api_server_label]

    saved_token = secret_or_env("ZENTRA_API_TOKEN") or secret_or_env("ZENTRA_API_KEY")
    api_token = saved_token or st.text_input(
        "ZENTRA API token",
        type="password",
        help=(
            "ZENTRA Cloud 1.0: API → Keys → Copy Token. "
            "You may paste either 'Token abc123...' or only 'abc123...'."
        ),
    )

    precip_device = st.text_input(
        "Precipitation logger",
        value=PRECIP_DEVICE_DEFAULT,
    )
    water_device = st.text_input(
        "Water-level logger",
        value=WATER_DEVICE_DEFAULT,
    )

    st.header("Download window")
    today = date.today()
    default_start = today - timedelta(days=3)
    start_date = st.date_input("From", value=default_start)
    end_date = st.date_input("To", value=today)

    local_tz = st.text_input("Local timezone", value=LOCAL_TZ_DEFAULT)

    st.caption(
        "ZENTRA 1.0 v4 allows only one call per minute per device. "
        "Keep the window reasonably short so each logger fits in one API page."
    )

    st.header("Event detection")
    dry_gap = st.number_input(
        "Dry gap separating events (min)",
        min_value=5,
        max_value=720,
        value=30,
        step=5,
    )
    rain_threshold = st.number_input(
        "Rain threshold per interval (mm)",
        min_value=0.0,
        value=0.0,
        step=0.01,
        format="%.2f",
    )
    before_buffer = st.number_input(
        "Plot / baseline before event (min)",
        min_value=5,
        max_value=360,
        value=30,
        step=5,
    )
    after_buffer = st.number_input(
        "Search / plot after last rain (min)",
        min_value=15,
        max_value=1440,
        value=180,
        step=15,
    )

    fetch_clicked = st.button(
        "Fetch data",
        type="primary",
        use_container_width=True,
    )


if fetch_clicked:
    if not api_token:
        st.error("Enter your ZENTRA Cloud 1.0 API token first.")
        st.stop()
    if start_date > end_date:
        st.error("'From' must be before or equal to 'To'.")
        st.stop()

    # v4 accepts start/end as ordinary date-time strings. We submit local clock
    # times matching the user's chosen calendar dates.
    local_start = datetime.combine(start_date, time.min)
    local_end = datetime.combine(end_date, time(23, 59, 59))

    with st.spinner("Fetching data from ZENTRA Cloud 1.0 API v4…"):
        try:
            with ZentraV4Client(api_token, server=api_server) as client:
                precip_raw = client.get_device_data(
                    precip_device, local_start, local_end
                )
                water_raw = client.get_device_data(
                    water_device, local_start, local_end
                )
        except ZentraAPIError as exc:
            st.error(str(exc))
            st.stop()
        except Exception as exc:
            st.exception(exc)
            st.stop()

    st.session_state["precip_raw"] = precip_raw
    st.session_state["water_raw"] = water_raw
    st.session_state["window"] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "tz": local_tz,
        "precip_device": precip_device,
        "water_device": water_device,
        "server": api_server,
    }


if "precip_raw" not in st.session_state or "water_raw" not in st.session_state:
    st.info(
        "Select the ZENTRA Cloud 1.0 server and a date window, then click "
        "**Fetch data**. The default devices are z6-10438 for precipitation "
        "and z6-10439 for water level."
    )
    st.stop()


precip_raw = st.session_state["precip_raw"]
water_raw = st.session_state["water_raw"]

c1, c2 = st.columns(2)
with c1:
    st.subheader("Precipitation series")
    precip_options = make_series_options(precip_raw)
    if not precip_options:
        st.error(
            "No measurement series were returned by the precipitation logger. "
            "Check server, date range, logger access and API subscription."
        )
        st.stop()
    p_default = best_option_index(
        precip_options,
        measurement_terms=["precip", "rain"],
        sensor_terms=["ecrn"],
    )
    p_label = st.selectbox(
        "Select precipitation measurement",
        [o["label"] for o in precip_options],
        index=p_default,
    )
    p_opt = precip_options[[o["label"] for o in precip_options].index(p_label)]

with c2:
    st.subheader("Water-level series")
    water_options = make_series_options(water_raw)
    if not water_options:
        st.error(
            "No measurement series were returned by the water-level logger. "
            "Check server, date range, logger access and API subscription."
        )
        st.stop()
    w_default = best_option_index(
        water_options,
        measurement_terms=["water level", "water depth", "level", "depth", "pressure"],
        sensor_terms=["ctd", "hydros"],
    )
    w_label = st.selectbox(
        "Select water-level measurement",
        [o["label"] for o in water_options],
        index=w_default,
    )
    w_opt = water_options[[o["label"] for o in water_options].index(w_label)]


precip = prepare_series(
    precip_raw,
    p_opt["measurement"],
    p_opt["sensor_name"],
    p_opt["port_num"],
    p_opt["position"],
    local_tz,
    aggregation="sum",
)
precip, precip_conversion_note = normalize_precipitation_to_depth(precip)

water = prepare_series(
    water_raw,
    w_opt["measurement"],
    w_opt["sensor_name"],
    w_opt["port_num"],
    w_opt["position"],
    local_tz,
    aggregation="mean",
)

if precip.empty:
    st.error("The selected precipitation series has no valid values in this window.")
    st.stop()
if water.empty:
    st.error("The selected water-level series has no valid values in this window.")
    st.stop()

if precip_conversion_note:
    st.info("Rainfall-unit correction: " + precip_conversion_note)


events = detect_rain_events(
    precip,
    threshold_mm=float(rain_threshold),
    dry_gap_minutes=float(dry_gap),
)
if not events:
    st.warning(
        "No rainfall events were found using the current threshold and dry-gap settings."
    )
    st.stop()


event_labels = []
for ev in events:
    event_labels.append(
        f"{ev.start.strftime('%Y-%m-%d %H:%M')} → "
        f"{ev.last_rain.strftime('%H:%M')}  |  "
        f"P={ev.total_mm:.2f} mm  |  duration={ev.duration_min:.0f} min"
    )

st.divider()
st.subheader("Select event")
selected_label = st.selectbox(
    "Detected rainfall events",
    event_labels,
    index=len(event_labels) - 1,
)
event = events[event_labels.index(selected_label)]

try:
    metrics = analyze_event(
        precip,
        water,
        event,
        water_after_minutes=float(after_buffer),
        baseline_before_minutes=float(before_buffer),
    )
except Exception as exc:
    st.error(f"Could not analyze the selected event: {exc}")
    st.stop()


m1, m2, m3, m4 = st.columns(4)
m1.metric("Total rainfall", f"{metrics.total_precip_mm:.2f} mm")
m2.metric(
    "Peak intensity",
    f"{metrics.peak_intensity_mmh:.1f} mm/h",
    help=(
        f"{metrics.peak_interval_mm:.2f} mm measured over "
        f"~{metrics.rain_interval_min:.0f} min."
    ),
)
m3.metric("t_lag", f"{metrics.lag_min:.0f} min")
m4.metric("T_c", f"{metrics.tc_min:.0f} min")

m5, m6, m7, m8 = st.columns(4)
m5.metric("Rainfall duration", f"{metrics.duration_min:.0f} min")
m6.metric("Rainfall centroid", metrics.rainfall_centroid.strftime("%H:%M"))
m7.metric("Peak water level", f"{metrics.water_peak:.1f} {metrics.water_unit}")
m8.metric("Peak time", metrics.water_peak_time.strftime("%H:%M"))

if metrics.water_rise is not None:
    st.caption(
        f"Pre-event median water level ≈ {metrics.baseline_water:.2f} "
        f"{metrics.water_unit}; rise to peak ≈ {metrics.water_rise:.2f} "
        f"{metrics.water_unit}."
    )

if metrics.lag_min < 0:
    st.warning(
        f"t_lag is {metrics.lag_min:.1f} min because the selected water-level peak "
        "occurs before the rainfall centroid. This can happen in overlapping/compound "
        "events or when the hydrograph is already rising before the selected rain burst. "
        "The value is kept rather than forced to be positive."
    )

tab_after, tab_before, tab_data = st.tabs(
    ["Analyzed event", "Raw event", "Data & export"]
)

with tab_after:
    annotated_fig = build_event_figure(
        precip,
        water,
        event,
        metrics,
        before_minutes=float(before_buffer),
        after_minutes=float(after_buffer),
        annotated=True,
    )
    st.plotly_chart(annotated_fig, use_container_width=True)
    st.caption(
        "t_lag = rainfall centroid → peak water level. "
        "T_c = rainfall start → peak water level, matching your annotated example."
    )

with tab_before:
    raw_fig = build_event_figure(
        precip,
        water,
        event,
        metrics=None,
        before_minutes=float(before_buffer),
        after_minutes=float(after_buffer),
        annotated=False,
    )
    st.plotly_chart(raw_fig, use_container_width=True)

with tab_data:
    metrics_df = metrics_to_frame(metrics)
    st.dataframe(metrics_df, use_container_width=True, hide_index=True)

    start_export = event.start - pd.Timedelta(minutes=float(before_buffer))
    end_export = event.last_rain + pd.Timedelta(minutes=float(after_buffer))

    p_export = precip[
        (precip["datetime"] >= start_export) & (precip["datetime"] <= end_export)
    ].rename(columns={"value": "precipitation_value", "unit": "precipitation_unit"})
    w_export = water[
        (water["datetime"] >= start_export) & (water["datetime"] <= end_export)
    ].rename(columns={"value": "water_level_value", "unit": "water_level_unit"})

    merged = pd.merge(
        p_export,
        w_export,
        on="datetime",
        how="outer",
    ).sort_values("datetime")

    dc1, dc2, dc3 = st.columns(3)
    dc1.download_button(
        "Download metrics CSV",
        data=metrics_df.to_csv(index=False).encode("utf-8"),
        file_name=f"zentra_event_metrics_{event.start.strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
    )
    dc2.download_button(
        "Download event data CSV",
        data=merged.to_csv(index=False).encode("utf-8"),
        file_name=f"zentra_event_data_{event.start.strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    try:
        png = annotated_fig.to_image(format="png", scale=2)
        dc3.download_button(
            "Download graph PNG",
            data=png,
            file_name=f"zentra_event_graph_{event.start.strftime('%Y%m%d_%H%M')}.png",
            mime="image/png",
            use_container_width=True,
        )
    except Exception:
        dc3.info("PNG export needs Kaleido from requirements.txt.")

    with st.expander("Show raw ZENTRA rows"):
        st.markdown("**Precipitation logger**")
        st.dataframe(precip_raw, use_container_width=True)
        st.markdown("**Water-level logger**")
        st.dataframe(water_raw, use_container_width=True)
