from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests


class ZentraAPIError(RuntimeError):
    pass


def _normalise_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise ValueError("API token is empty.")
    # ZENTRA Cloud 1.0 v3/v4 expects: Authorization: Token <token>
    if token.lower().startswith("token "):
        return token
    if token.lower() == "token":
        raise ValueError("Paste the complete API token, not only the word Token.")
    return f"Token {token}"


def _extract_pagination(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    for key in ("pagination", "page", "meta"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {
        k: payload.get(k)
        for k in ("page_num", "total_pages", "next_page", "previous_page", "per_page", "count")
        if k in payload
    }


def _has_more_pages(payload: Any, page_num: int) -> bool:
    p = _extract_pagination(payload)
    if not p:
        return False

    for key in ("total_pages", "page_count", "num_pages"):
        try:
            total = int(p.get(key))
            if total > page_num:
                return True
        except Exception:
            pass

    nxt = p.get("next_page", p.get("next"))
    if nxt not in (None, False, "", 0, "0"):
        return True
    return False


def _unpack_data(payload: Any) -> Any:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, str):
        try:
            return json.loads(data)
        except Exception:
            return data
    return data


def _flatten_json_payload(payload: Any) -> pd.DataFrame:
    """
    Flatten the ZENTRA v4 JSON structure into the long schema used by the app.

    Expected v4 measurement structure:
      {
        "Measurement name": [
          {
            "metadata": {...},
            "readings": [{...}, ...]
          }
        ]
      }
    """
    data = _unpack_data(payload)
    rows = []

    # Some responses wrap the actual measurements once more.
    if isinstance(data, dict):
        for wrapper in ("readings", "measurements", "results"):
            if wrapper in data and isinstance(data[wrapper], dict):
                data = data[wrapper]
                break

    if not isinstance(data, dict):
        return pd.DataFrame()

    for measurement, sensor_entries in data.items():
        # Ignore obvious response metadata keys.
        if str(measurement).lower() in {
            "pagination", "location_history", "page", "metadata",
            "device", "device_info", "errors"
        }:
            continue

        if isinstance(sensor_entries, dict):
            sensor_entries = [sensor_entries]
        if not isinstance(sensor_entries, list):
            continue

        for entry in sensor_entries:
            if not isinstance(entry, dict):
                continue

            metadata = entry.get("metadata") or {}
            readings = entry.get("readings") or entry.get("values") or []

            # Occasionally a single reading may be represented directly.
            if isinstance(readings, dict):
                readings = [readings]
            if not isinstance(readings, list):
                continue

            unit = (
                metadata.get("units")
                or metadata.get("unit")
                or ""
            )
            sensor_name = (
                metadata.get("sensor_name")
                or metadata.get("sensor")
                or metadata.get("sensor_model")
                or ""
            )
            port_num = (
                metadata.get("port_number")
                or metadata.get("port_num")
                or metadata.get("port")
                or 0
            )
            position = (
                metadata.get("sensor_depth")
                if "sensor_depth" in metadata
                else metadata.get("depth", metadata.get("position"))
            )

            for r in readings:
                if not isinstance(r, dict):
                    continue
                dt = r.get("datetime")
                timestamp_utc = r.get("timestamp_utc", r.get("timestamp"))
                value = r.get("value")
                error_flag = bool(r.get("error_flag", False))
                rows.append(
                    {
                        "port_num": port_num,
                        "measurement": measurement,
                        "unit": str(unit).strip(),
                        "sensor_name": sensor_name,
                        "position": position,
                        "value": value,
                        "timestamp": timestamp_utc,
                        "datetime": dt,
                        "error_code": 1 if error_flag else 0,
                        "reading_id": r.get("mrid", r.get("reading_id")),
                        "error_description": r.get("error_description"),
                    }
                )

    return pd.DataFrame(rows)


class ZentraV4Client:
    """
    Client for ZENTRA Cloud 1.0 Pull API v4.

    EU server:
        https://zentracloud.eu/api/v4/get_readings/
    US server:
        https://zentracloud.com/api/v4/get_readings/

    Authentication:
        Authorization: Token <API token>
    """

    def __init__(self, token: str, server: str = "https://zentracloud.eu", timeout: int = 60):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": _normalise_token(token),
                "Accept": "application/json",
                "User-Agent": "ZentraHydroEventAnalyzer-v4/1.0",
            }
        )
        self.last_pagination = {}

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def get_device_data(
        self,
        device_sn: str,
        start_datetime: datetime,
        end_datetime: datetime,
        per_page: int = 2000,
    ) -> pd.DataFrame:
        url = f"{self.server}/api/v4/get_readings/"
        params = {
            "device_sn": device_sn.strip(),
            "start_date": start_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "output_format": "json",
            "page_num": 1,
            "per_page": min(int(per_page), 2000),
            "sort_by": "ascending",
            "device_depth": "true",
        }

        response = self.session.get(url, params=params, timeout=self.timeout)

        if response.status_code == 401:
            raise ZentraAPIError(
                "401 Unauthorized. For ZENTRA Cloud 1.0, use the token from "
                "API → Keys → Copy Token. The app accepts it with or without the word 'Token'."
            )
        if response.status_code == 403:
            raise ZentraAPIError(
                f"403 Forbidden for {device_sn}. Check that this device is added to "
                "your ZENTRA Cloud 1.0 account and that the API subscription is active."
            )
        if response.status_code == 404:
            raise ZentraAPIError(
                f"404 for {device_sn}. Check the selected ZENTRA server (EU/US), "
                "the logger serial number, and device access."
            )
        if response.status_code == 429:
            raise ZentraAPIError(
                "ZENTRA Cloud 1.0 rate limit reached. v4 limits each device to "
                "one API call per minute. Wait about 60 seconds before retrying."
            )

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ZentraAPIError(
                f"ZENTRA Cloud v4 error for {device_sn}: "
                f"{response.status_code} {response.text[:500]}"
            ) from exc

        payload = response.json()
        self.last_pagination = _extract_pagination(payload)

        if _has_more_pages(payload, 1):
            raise ZentraAPIError(
                f"The selected window for {device_sn} contains more than one v4 page. "
                "To avoid the ZENTRA Cloud 1.0 one-call-per-device-per-minute limit, "
                "choose a shorter date window and fetch again."
            )

        df = _flatten_json_payload(payload)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "port_num", "measurement", "unit", "sensor_name", "position",
                    "value", "timestamp", "datetime", "error_code", "reading_id",
                    "error_description"
                ]
            )

        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        # If datetime parsing failed but epoch timestamp exists, use it.
        missing_dt = df["datetime"].isna() & pd.to_numeric(df["timestamp"], errors="coerce").notna()
        if missing_dt.any():
            df.loc[missing_dt, "datetime"] = pd.to_datetime(
                pd.to_numeric(df.loc[missing_dt, "timestamp"], errors="coerce"),
                unit="s",
                utc=True,
                errors="coerce",
            )

        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").fillna(0).astype(int)
        df["error_code"] = pd.to_numeric(df["error_code"], errors="coerce").fillna(0).astype(int)

        return df.sort_values("datetime").reset_index(drop=True)
