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


    def decode_sleep(self, start_date: str = None, end_date: str = None):
        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()

        url = f"{self.base_url}/v1/data/band_data.json"
        params = {
            "query_type": "summary",
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

            print(f"{'='*50}")
            print(f"Date : {raw_date}")
            print(f"Sleep: {sleep_start} → {sleep_end}  ({total_min // 60}h {total_min % 60}m total)")
            print(f"Score: {slp.get('ss', '?')}")
            print(f"{'─'*50}")
            for label in ("Deep", "Light", "REM", "Awake"):
                mins = totals[label]
                bar = "█" * (mins // 5)
                print(f"  {label:<6} {mins:>4} min  {bar}")
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
