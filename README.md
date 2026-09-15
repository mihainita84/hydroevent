# ZENTRA Hydro Event Analyzer - ZENTRA Cloud 1.0

This version is for **ZENTRA Cloud 1.0** and uses the **v4 Pull API**, not v5.

## Correct authentication

In ZENTRA Cloud 1.0:

1. Open **API**
2. Open the **Keys** tab
3. Click **Copy Token**

The app accepts either:

```text
31aa...
```

or:

```text
Token 31aa...
```

Internally it sends:

```text
Authorization: Token 31aa...
```

## Correct endpoint

EU accounts:

```text
https://zentracloud.eu/api/v4/get_readings/
```

US accounts:

```text
https://zentracloud.com/api/v4/get_readings/
```

The app has a server selector in the sidebar.

**Do not add a Push API Endpoint.** The Endpoint screen in ZENTRA Cloud 1.0 is for a webhook/push integration and is unrelated to this Streamlit application's historical data pull.

## Devices configured by default

- Precipitation: `z6-10438`
- Water level: `z6-10439`

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Important v4 limit

ZENTRA Cloud 1.0 v4 documents a limit of one API call per minute per device. The application therefore requests up to 2000 records in one call and asks you to narrow the selected date window instead of automatically hammering the next page.

## Event metrics

The event interface calculates:

- rainfall start and end
- duration
- total rainfall
- peak interval rainfall
- peak rainfall intensity
- rainfall centroid
- peak water level and its time
- `t_lag`: rainfall centroid to water-level peak
- `T_c`: rainfall start to water-level peak, matching the supplied annotated graph
- pre-event median level
- rise to peak

It provides both the raw and annotated event figures and CSV/PNG export.
