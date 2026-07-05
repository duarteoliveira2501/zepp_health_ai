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

            actual_bpm = [v for v in values if v not in (254, 255) and v > 0]
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
                    sleep_hr = [v for v in hr_bytes[idx_start:idx_end] if v not in (254, 255) and v > 0]
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
                        if i not in awake_indices and hr_bytes[i] not in (254, 255) and hr_bytes[i] > 0
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

    def _last_n_days_ms(self, days: int = 7):
        """Build from/to timestamps in ms for 'last N days', ending now.

        Anchored to local midnight of (today - (days-1)) rather than a
        rolling "now minus N days" window, so the oldest day is fully
        included regardless of what time the script runs (a rolling window
        can cut off early-morning events on the Nth day back).
        """
        now = datetime.now()
        to_ts = int(now.timestamp() * 1000)
        from_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        from_ts = int(from_day.timestamp() * 1000)
        return from_ts, to_ts

    def fetch_wake_hybridcharge(self, from_ts: int = None, to_ts: int = None, days: int = 7):
        """Fetch Wake HybridCharge samples.

        Confirmed mapping (CLAUDE.md, "Wake HybridCharge (CONFIRMED)"):
        GET /v2/users/me/events, eventType=Charge, subType=wake_data.
        wakeCharge is the primary score but is absent on some samples
        (e.g. earliest records only have mentalWake/physicalWake) — those
        are skipped, not treated as errors.

        The API filters on item.timestamp, but item.timestamp runs ~22h
        behind value.startTime (the field we actually bucket by for the
        display date). When defaulting the range, pad `from` by one extra
        day so the oldest requested day's item isn't excluded, then filter
        the parsed records back down to exactly `days` by startTime.
        Explicit from_ts/to_ts are used as-is, no padding or filtering.

        Args:
            from_ts: range start, ms since epoch (defaults to `days` days ago)
            to_ts:   range end, ms since epoch (defaults to now)
            days:    size of the default range in calendar days (ignored if
                     from_ts/to_ts are both given)
        """
        explicit_range = from_ts is not None and to_ts is not None
        if not explicit_range:
            padded_from, default_to = self._last_n_days_ms(days=days + 1)
            if from_ts is None:
                from_ts = padded_from
            if to_ts is None:
                to_ts = default_to

        url = f"{self.base_url}/v2/users/me/events"
        headers = {
            "apptoken": self.app_token,
            "appname": "com.huami.midong",
            "appplatform": "android_phone",
            "Content-Type": "application/json",
        }
        params = {
            "eventType": "Charge",
            "subType": "wake_data",
            "from": from_ts,
            "to": to_ts,
            "limit": 200,
            "reverse": "true",
        }
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 401:
            raise RuntimeError(
                "Zepp apptoken expired or invalid (401). Redo the emulator "
                "capture ritual: log into the Zepp app, grab a fresh apptoken "
                "via HTTP Toolkit, and update APP_TOKEN in .env."
            )
        resp.raise_for_status()

        items = resp.json().get("items", [])
        records = []
        for item in items:
            item_timestamp = item.get("timestamp")
            value = item.get("value", {})
            start_time = value.get("startTime")
            for sample in value.get("samples", []):
                if "wakeCharge" not in sample:
                    # Not every sample has the primary score (e.g. earliest
                    # history only has mentalWake/physicalWake) — skip, don't crash.
                    continue
                records.append({
                    "item_timestamp": item_timestamp,
                    "start_time": start_time,
                    "wake_charge": sample.get("wakeCharge"),
                    "mental_wake": sample.get("mentalWake"),
                    "physical_wake": sample.get("physicalWake"),
                    "exertion_score": sample.get("exertionScore"),
                    "daily_fitness_score": sample.get("dailyFitnessScore"),
                    "chronic_weight_daily": sample.get("chronicWeightDaily"),
                    "algo_version": sample.get("algoVersion"),
                })

        if not explicit_range:
            cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
            cutoff_ts = int(cutoff.timestamp() * 1000)
            records = [r for r in records if (r["start_time"] or 0) >= cutoff_ts]

        return records

    def decode_hybridcharge(self, from_ts: int = None, to_ts: int = None):
        """Print date: wakeCharge for the given range (defaults to last 7 days)."""
        records = self.fetch_wake_hybridcharge(from_ts=from_ts, to_ts=to_ts)

        print(f"{'='*50}")
        print("Wake HybridCharge")
        print(f"{'─'*50}")
        if not records:
            print("  No HybridCharge samples with wakeCharge found in range.")
            return records

        for rec in records:
            ts = rec["start_time"] or rec["item_timestamp"]
            day = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else "unknown"
            print(f"  {day}: {rec['wake_charge']}")
        print()
        return records

    def probe_sleep_subtypes(self):
        import time
        from datetime import datetime, timedelta

        headers = {
            "apptoken": self.app_token,
            "appname": "com.huami.midong",
            "appplatform": "android_phone",
            "Content-Type": "application/json",
        }

        from_ts, to_ts = self._last_n_days_ms(days=7)

        candidates = [
            # From HTTP Toolkit observed subtypes
            ("blood_oxygen", "odi"),
            ("blood_oxygen", "spo2"),
            ("blood_oxygen", "sleep_spo2"),
            ("SleepBreath", "breathing_quality"),
            ("SleepBreath", "respiratory_rate"),
            ("SleepBreath", "hypopnea"),
            ("sleepBreath", "breathing_quality"),
            ("sleepBreath", "respiratory_rate"),
            ("sleepBreathing", "respiratory_rate"),
            ("sleepBreathing", "hypopnea"),
            ("Sleep", "breath"),
            ("Sleep", "br"),
            ("SleepMonitor", "respiratory_rate"),
            ("SleepMonitor", "hypopnea"),
            ("SleepMonitor", "breathing_quality"),
            ("HealthMonitor", "respiratory_rate"),
            ("HealthMonitor", "hypopnea"),
            ("phn", "sleep_breath"),
            ("phn", "sleep_br"),
            ("phn", "sleep_breathing"),
        ]

        for event_type, sub_type in candidates:
            url = f"{self.base_url}/v2/users/me/events"
            params = {
                "limit": 10,
                "eventType": event_type,
                "subType": sub_type,
                "from": from_ts,
                "to": to_ts,
                "reverse": "true",
            }
            resp = requests.get(url, headers=headers, params=params)
            items = resp.json().get("items", [])
            count = len(items)
            print(f"[{resp.status_code}] eventType={event_type} subType={sub_type} → {count} items")
            if count > 0:
                print(f"  First item keys: {list(items[0].keys())}")
                if "value" in items[0]:
                    val = items[0]["value"]
                    print(f"  Value: {val if not isinstance(val, dict) else list(val.keys())}")
            time.sleep(0.2)

        # Also probe dateString endpoint with different eventTypes
        date_string_candidates = [
            ("blood_oxygen", "odi"),
            ("blood_oxygen", "respiratory_rate"),
            ("blood_oxygen", "breathing_quality"),
            ("SleepBreathing", "odi"),
            ("SleepBreathing", "respiratory_rate"),
            ("Sleep", "respiratory_rate"),
            ("Sleep", "regularity"),
        ]

        print("\n--- Probing /users/{userid}/events/dateString ---")
        for event_type, sub_type in date_string_candidates:
            url = f"{self.base_url}/users/{self.user_id}/events/dateString"
            params = {
                "limit": 10,
                "eventType": event_type,
                "subType": sub_type,
                "from": from_ts,
                "to": to_ts,
                "timezone": "Europe/Lisbon",
                "reverse": "true",
            }
            resp = requests.get(url, headers=headers, params=params)
            items = resp.json().get("items", [])
            count = len(items)
            print(f"[{resp.status_code}] eventType={event_type} subType={sub_type} → {count} items")
            if count > 0:
                print(f"  First item keys: {list(items[0].keys())}")
                if "value" in items[0]:
                    val = items[0]["value"]
                    print(f"  Value: {val if not isinstance(val, dict) else list(val.keys())}")
            time.sleep(0.2)

    def probe_sec_hr(self):
        import zipfile
        import io

        # Direct S3 URLs seen in Firebase performance log
        from datetime import date, timedelta
        dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(7)]

        for d in dates:
            url = f"https://s3.eu-central-1.amazonaws.com/huami-de/SEC_HR/com.xiaomi.hm.health/{self.user_id}/{d}.zip"
            resp = requests.get(url)
            print(f"[{resp.status_code}] {d} → {len(resp.content)} bytes")
            if resp.status_code == 200:
                try:
                    z = zipfile.ZipFile(io.BytesIO(resp.content))
                    print(f"  Files in zip: {z.namelist()}")
                    for name in z.namelist():
                        data = z.read(name)
                        print(f"  {name}: {len(data)} bytes")
                        print(f"  First 200 bytes (hex): {data[:200].hex()}")
                        print(f"  First 200 bytes (text attempt): {data[:200]}")
                except Exception as e:
                    print(f"  Error reading zip: {e}")


if __name__ == "__main__":
    client = ZeppClient()
    client.decode_sleep()
    client.decode_hybridcharge()
