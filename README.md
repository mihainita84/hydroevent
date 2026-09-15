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

## v4.1 fix

Fixed sensor position/depth matching for ZENTRA v4 responses where `position` may be text such as `0.2 m`, blank, or non-numeric.


## v4.2 fixes

1. **Rainfall centroid / t_lag fix**  
   The previous code converted pandas internal datetime integers assuming nanoseconds.
   On environments using microsecond datetime resolution, that produced dates near 1970
   and therefore enormous lag values. v4.2 calculates the centroid from explicit POSIX
   timestamps and checks that the centroid lies inside the event.

2. **Annotated graph compression fix**  
   The annotated plot now pins the x-axis to the selected event window. A bad annotation
   can no longer stretch the axis and squeeze the precipitation and water-level traces
   into a vertical strip.

3. **ECRN precipitation rate conversion**  
   If ZENTRA returns precipitation in `mm/h`, the app converts each logger interval to
   rainfall depth in `mm` before calculating total rainfall. For a 5-minute logger:
   `depth_mm = rate_mm_h * 5/60`. Peak intensity remains in `mm/h`.


## v4.3 improvements

1. **Full-series overview graph added**  
   The app now shows a graph for the entire downloaded period before the event selector.  
   Detected rainfall events are highlighted with blue shaded windows and labelled `E1`, `E2`, etc.

2. **Detected-event table added**  
   An expandable table lists all identified events with start, end, duration, total rainfall and number of rainy intervals.

3. **Larger v4 `per_page` request**  
   The client now asks for up to 10,000 records in one v4 call.  
   This helps month-long 5-minute series fit into one response and prevents the app from showing only the first few events.

4. **Near-limit warning**  
   If the download is close to the current per-page cap, the app warns that the selected time window may be too large.
