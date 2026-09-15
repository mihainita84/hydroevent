# ZENTRA Hydro Event Analyzer

A Streamlit interface for combining:

- precipitation from **z6-10438** (default), and
- water level from **z6-10439** (default),

through the **ZENTRA Cloud v5 API**.

The app downloads both time series, lets you choose the exact sensor/measurement stream returned by the API, automatically detects rainfall events, and produces both a raw graph and an annotated event graph.

## Extracted event parameters

For the selected event, the app calculates:

- rainfall start
- last positive-rainfall interval
- rainfall duration
- total precipitation
- peak interval precipitation
- peak rainfall intensity in mm/h
- rainfall centroid
- peak water level and peak time
- **t_lag** = peak-water-level time minus rainfall-centroid time
- **T_c** = rainfall-start time to peak-water-level time
- pre-event median water level
- rise from baseline to peak

The duration follows the convention in the supplied example: if the logger interval is 5 minutes, a sequence from 18:05 through 19:15 has duration `(19:15 - 18:05) + 5 min = 75 min`.

## ZENTRA Cloud v5 API

The app calls:

```text
GET https://api.zentracloud.io/v5/devices/{device_id}/data
```

with the API key in:

```text
X-API-Key: <your key>
```

It follows `pagination.next_url` exactly when the API returns another page.

## Install

Create a Python environment, then:

```bash
pip install -r requirements.txt
```

## API key

### Recommended

Copy:

```text
.streamlit/secrets.toml.example
```

to:

```text
.streamlit/secrets.toml
```

and insert your API key there.

Alternatively, set an environment variable:

### Windows PowerShell

```powershell
$env:ZENTRA_API_KEY="your-new-api-key"
streamlit run app.py
```

### Windows CMD

```cmd
set ZENTRA_API_KEY=your-new-api-key
streamlit run app.py
```

### Linux/macOS

```bash
export ZENTRA_API_KEY="your-new-api-key"
streamlit run app.py
```

If no key is stored, the app shows a password-type input in the sidebar.

## Run

```bash
streamlit run app.py
```

Your browser will open the interface.

## Suggested settings

For 5-minute rainfall data, start with:

- dry gap separating events: **30 min**
- rain threshold: **0 mm**
- pre-event buffer: **30 min**
- post-event search: **180 min**
- timezone: **Europe/Bucharest**

If two rainfall bursts should be treated as one hydrological event, increase the dry gap. If the water-level line is still rising at the end of the graph, increase the post-event search window.

## Important hydrological note

The app labels `T_c` as an **operational rainfall-start-to-hydrograph-peak time**, because that is the convention shown in the supplied annotated example. In strict hydrological terminology, time of concentration can be defined differently depending on the method and catchment model. The label can be changed easily if you want another definition.

## Security

Do not hard-code the API key in `app.py`, and do not commit `.streamlit/secrets.toml`.
