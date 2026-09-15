from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import time

import pandas as pd
import requests


BASE_URL = "https://api.zentracloud.io/v5"


class ZentraAPIError(RuntimeError):
    pass


@dataclass
class RateLimitInfo:
    next_allowed_unix: Optional[int] = None

    @property
    def message(self) -> str:
        if not self.next_allowed_unix:
            return "ZENTRA Cloud rate limit reached. Please wait and try again."
        dt = datetime.fromtimestamp(self.next_allowed_unix, tz=timezone.utc)
        return (
            "ZENTRA Cloud rate limit reached. "
            f"Earliest retry reported by the API: {dt.isoformat()}."
        )


def _parse_rate_limit(response: requests.Response) -> RateLimitInfo:
    try:
        payload = response.json()
    except Exception:
        return RateLimitInfo()

    detail = payload.get("detail")
    candidates = []

    if isinstance(detail, (int, float)):
        candidates.append(detail)
    elif isinstance(detail, str):
        import re
        candidates += [int(x) for x in re.findall(r"\b1\d{9}\b", detail)]
    elif isinstance(detail, dict):
        for v in detail.values():
            if isinstance(v, (int, float)):
                candidates.append(v)
            elif isinstance(v, str):
                import re
                candidates += [int(x) for x in re.findall(r"\b1\d{9}\b", v)]

    for value in candidates:
        try:
            ivalue = int(value)
            if ivalue > 1_500_000_000:
                return RateLimitInfo(ivalue)
        except Exception:
            pass

    return RateLimitInfo()


class ZentraV5Client:
    """Small direct client for the public ZENTRA Cloud v5 REST API."""

    def __init__(self, api_key: str, timeout: int = 45):
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValueError("API key is empty.")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "ZentraHydroEventAnalyzer/1.0",
            }
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def get_device_data(
        self,
        device_id: str,
        start_datetime: datetime,
        end_datetime: datetime,
        units: str = "metric",
    ) -> pd.DataFrame:
        """
        Fetch all pages for one device and one time window.

        ZENTRA v5 paginates device data by UTC calendar-month windows.
        The API returns pagination.next_url; this client follows it verbatim.
        """
        device_id = device_id.strip()
        url = f"{BASE_URL}/devices/{device_id}/data"
        params = {
            "start_datetime": start_datetime.isoformat(),
            "end_datetime": end_datetime.isoformat(),
            "direction": "ascending",
            "units": units,
        }

        rows = []
        first_request = True
        page_count = 0

        while url:
            page_count += 1
            if page_count > 36:
                raise ZentraAPIError(
                    "Stopped after 36 API pages. Narrow the requested date window."
                )

            response = self.session.get(
                url,
                params=params if first_request else None,
                timeout=self.timeout,
            )
            first_request = False

            if response.status_code == 429:
                info = _parse_rate_limit(response)
                raise ZentraAPIError(info.message)

            if response.status_code == 401:
                raise ZentraAPIError(
                    "401 Unauthorized. Check the ZENTRA Cloud API key."
                )
            if response.status_code == 403:
                raise ZentraAPIError(
                    f"403 Forbidden for {device_id}. The API key does not have "
                    "permission to read this device."
                )
            if response.status_code == 404:
                raise ZentraAPIError(
                    f"404 for {device_id}. The device is not visible to this API key "
                    "or the device ID is incorrect."
                )
            if response.status_code == 422:
                raise ZentraAPIError(
                    f"422 Invalid request for {device_id}: {response.text[:500]}"
                )

            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise ZentraAPIError(
                    f"ZENTRA API error for {device_id}: {exc}"
                ) from exc

            payload = response.json()
            rows.extend(payload.get("values", []))

            pagination = payload.get("pagination") or {}
            url = pagination.get("next_url")
            params = None

        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "port_num",
                    "measurement",
                    "unit",
                    "sensor_name",
                    "position",
                    "value",
                    "timestamp",
                    "datetime",
                    "error_code",
                    "reading_id",
                ]
            )

        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["error_code"] = pd.to_numeric(df["error_code"], errors="coerce").fillna(0).astype(int)

        # Invalid/suspect sensor rows remain retrievable in the raw table,
        # but downstream analysis uses only error_code == 0 and non-null values.
        return df.sort_values("datetime").reset_index(drop=True)
