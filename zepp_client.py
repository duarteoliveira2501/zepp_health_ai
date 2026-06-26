import base64
import json
import os
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

SLEEP_MODES = {
    4: "Light",
    5: "Deep",
    7: "Awake",
    8: "REM",
}

load_dotenv()


class ZeppClient:
    def __init__(self):
        self.app_token = os.environ["APP_TOKEN"]
        self.user_id = os.environ["USER_ID"]
        self.host = os.environ["HOST"]
        self.base_url = f"https://{self.host}"

    def _headers(self):
        return {
            "apptoken": self.app_token,
            "Content-Type": "application/json",
        }

    def refresh_token(self):
        url = f"{self.base_url}/v1/client/renew_login_token"
        resp = requests.post(url, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        print(json.dumps(data, indent=2))
        if "token" in data:
            self.app_token = data["token"]
        return data

    def fetch_sleep_data(self, start_date: str = None, end_date: str = None):
        """Fetch raw band sleep data.

        Args:
            start_date: ISO date string YYYY-MM-DD (defaults to 7 days ago)
            end_date:   ISO date string YYYY-MM-DD (defaults to today)
        """
        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()

        url = f"{self.base_url}/v1/data/band_data.json"
        params = {
            "userid": self.user_id,
            "from_date": start_date,
            "to_date": end_date,
            "type": "SLEEP",
        }
        resp = requests.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        data = resp.json()
        print(json.dumps(data, indent=2))
        return data


    def fetch_detail_data(self, start_date: str = None, end_date: str = None):
        """Fetch detail band data and decode minute-by-minute HR blob.

        Args:
            start_date: ISO date string YYYY-MM-DD (defaults to 7 days ago)
            end_date:   ISO date string YYYY-MM-DD (defaults to today)
        """
        import struct

        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()

        url = f"{self.base_url}/v1/data/band_data.json"
        params = {
            "query_type": "detail",
            "device_type": "android",
            "userid": self.user_id,
            "from_date": start_date,
            "to_date": end_date,
        }
        headers = {**self._headers(), "User-Agent": "APP_PLATFORM_ANDROID"}
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()

        records = resp.json().get("data", [])

        for record in records:
            raw_date = record.get("date_time", "unknown")
            print(f"\n{'='*50}")
            print(f"Date: {raw_date}")
            print(f"Top-level keys: {list(record.keys())}")

            hr_b64 = record.get("data_hr")
            if not hr_b64:
                print("  No data_hr field found.")
                continue

            padding = (4 - len(hr_b64) % 4) % 4
            raw_bytes = base64.b64decode(hr_b64 + "=" * padding)
            print(f"  data_hr blob size: {len(raw_bytes)} bytes")

            # single byte per minute, not shorts
            values = list(raw_bytes)
            total_minutes = len(values)

            actual_bpm = [v for v in values if v < 200 and v != 0]
            unscheduled = values.count(255)
            failed = values.count(254)

            print(f"  Total minutes: {total_minutes}")
            print(f"  Unscheduled (255): {unscheduled}")
            print(f"  Failed (254): {failed}")
            print(f"  Actual readings: {len(actual_bpm)}")
            if actual_bpm:
                print(f"  HR range: {min(actual_bpm)}–{max(actual_bpm)} BPM")
                print(f"  HR avg: {round(sum(actual_bpm)/len(actual_bpm), 1)} BPM")

    def decode_sleep(self, start_date: str = None, end_date: str = None):
        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()

        url = f"{self.base_url}/v1/data/band_data.json"
        summary_params = {
            "query_type": "summary",
            "device_type": "android",
            "userid": self.user_id,
            "from_date": start_date,
            "to_date": end_date,
        }
        detail_params = {
            "query_type": "detail",
            "device_type": "android",
            "userid": self.user_id,
            "from_date": start_date,
            "to_date": end_date,
        }
        band_headers = {**self._headers(), "User-Agent": "APP_PLATFORM_ANDROID"}

        resp = requests.get(url, headers=band_headers, params=summary_params)
        resp.raise_for_status()
        records = resp.json().get("data", [])

        detail_resp = requests.get(url, headers=band_headers, params=detail_params)
        detail_resp.raise_for_status()
        detail_by_date = {
            r.get("date_time"): r
            for r in detail_resp.json().get("data", [])
        }

        for record in records:
            raw_date = record.get("date_time", "unknown")
            summary_b64 = record.get("summary", "")
            if not summary_b64:
                print(f"\n{raw_date}: no summary data\n")
                continue

            obj = json.loads(base64.b64decode(summary_b64 + "=="))
            slp = obj.get("slp", {})

            tz_offset_sec = int(obj.get("tz", 0))
            tz_info = timezone(timedelta(seconds=tz_offset_sec))

            st_ts = slp.get("st")
            ed_ts = slp.get("ed")
            sleep_start = datetime.fromtimestamp(st_ts, tz=tz_info).strftime("%H:%M") if st_ts else "?"
            sleep_end = datetime.fromtimestamp(ed_ts, tz=tz_info).strftime("%H:%M") if ed_ts else "?"
            total_min = round((ed_ts - st_ts) / 60) if st_ts and ed_ts else 0

            totals = {name: 0 for name in SLEEP_MODES.values()}
            for stage in slp.get("stage", []):
                mode = stage.get("mode")
                duration = stage["stop"] - stage["start"] + 1
                label = SLEEP_MODES.get(mode)
                if label:
                    totals[label] += duration

            # Compute avg sleep HR from detail blob
            avg_hr_incl_str = "n/a"
            avg_hr_excl_str = "n/a"
            detail_record = detail_by_date.get(raw_date)
            if detail_record and st_ts and ed_ts:
                hr_b64 = detail_record.get("data_hr")
                if hr_b64:
                    hr_padding = (4 - len(hr_b64) % 4) % 4
                    hr_bytes = list(base64.b64decode(hr_b64 + "=" * hr_padding))
                    # Midnight in local tz for this date
                    date_obj = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=tz_info)
                    midnight_ts = int(date_obj.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                    idx_start = (st_ts - midnight_ts) // 60
                    idx_end = (ed_ts - midnight_ts) // 60
                    idx_start = max(0, idx_start)
                    idx_end = min(len(hr_bytes), idx_end)
                    sleep_hr = [v for v in hr_bytes[idx_start:idx_end] if 0 < v < 200]
                    if sleep_hr:
                        avg_hr_incl_str = f"{round(sum(sleep_hr) / len(sleep_hr), 1)} BPM"

                    # Build set of awake minute indices to exclude (stage start/stop are minute indices from midnight)
                    awake_indices = set()
                    for stage in slp.get("stage", []):
                        if stage.get("mode") == 7:
                            s_idx = stage["start"] - 1440
                            e_idx = stage["stop"] - 1440
                            awake_indices.update(range(s_idx, e_idx + 1))
                    sleep_hr_excl = [
                        hr_bytes[i] for i in range(idx_start, idx_end)
                        if i not in awake_indices and 0 < hr_bytes[i] < 200
                    ]
                    if sleep_hr_excl:
                        avg_hr_excl_str = f"{round(sum(sleep_hr_excl) / len(sleep_hr_excl), 1)} BPM"

            print(f"{'='*50}")
            print(f"Date : {raw_date}")
            print(f"Sleep: {sleep_start} → {sleep_end}  ({total_min // 60}h {total_min % 60}m total)")
            print(f"Score: {slp.get('ss', '?')}")
            print(f"{'─'*50}")
            for label in ("Deep", "Light", "REM", "Awake"):
                mins = totals[label]
                bar = "█" * (mins // 5)
                print(f"  {label:<6} {mins:>4} min  {bar}")

            rhr = slp.get("rhr")
            if rhr is not None:
                print(f"  RHR: {rhr} BPM")

            wc = slp.get("wc")
            if wc is not None:
                print(f"  Wake count: {wc}")

            print(f"  Avg sleep HR (incl. awake): {avg_hr_incl_str}")
            print(f"  Avg sleep HR (excl. awake): {avg_hr_excl_str}")

            naps = obj.get("odd_stage", [])
            if naps:
                print(f"  Naps ({len(naps)}):")
                for nap in naps:
                    nap_st = nap.get("start") or nap.get("st")
                    nap_ed = nap.get("stop") or nap.get("ed")
                    if nap_st and nap_ed:
                        nap_start = datetime.fromtimestamp(nap_st, tz=tz_info).strftime("%H:%M")
                        nap_end = datetime.fromtimestamp(nap_ed, tz=tz_info).strftime("%H:%M")
                        nap_dur = round((nap_ed - nap_st) / 60)
                        print(f"    {nap_start} → {nap_end}  ({nap_dur}m)")
            print()

    def fetch_raw(self):
        url = (
            "https://api-mifit-de2.zepp.com/v1/data/band_data.json"
            "?query_type=summary&device_type=android"
            f"&userid={self.user_id}&from_date=2026-06-09&to_date=2026-06-16"
        )
        headers = {
            "apptoken": self.app_token,
            "Content-Type": "application/json",
            "User-Agent": "APP_PLATFORM_ANDROID",
        }
        resp = requests.get(url, headers=headers)
        print(f"Status: {resp.status_code}")
        print(f"Headers: {dict(resp.headers)}")
        print(f"Body:\n{resp.text}")


if __name__ == "__main__":
    client = ZeppClient()
    client.decode_sleep()
