import base64
import json
import os
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

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
        self.supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

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

    def _fetch_sleep_records(self, start_date: str, end_date: str):
        """Fetch summary + detail band data for a date range and pair them up
        by date_time. Shared by decode_sleep() and sync_sleep_to_supabase()."""
        url = f"{self.base_url}/v1/data/band_data.json"
        summary_params = {
            "query_type": "summary",
            "device_type": "android",
            "userid": self.user_id,
            "from_date": start_date,
            "to_date": end_date,
        }
        detail_params = {**summary_params, "query_type": "detail"}
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
        return records, detail_by_date

    def _extract_sleep_record(self, record, detail_by_date):
        """Decode one summary record (+ its paired detail record) into a
        plain dict of raw fields. Returns None if there's no summary data.
        Does not compute avg HR — see _compute_avg_sleep_hr(), since that
        depends on the (possibly corrected) sleep window."""
        raw_date = record.get("date_time", "unknown")
        summary_b64 = record.get("summary", "")
        if not summary_b64:
            return None

        obj = json.loads(base64.b64decode(summary_b64 + "=="))
        slp = obj.get("slp", {})

        tz_offset_sec = int(obj.get("tz", 0))
        tz_info = timezone(timedelta(seconds=tz_offset_sec))

        totals = {name: 0 for name in SLEEP_MODES.values()}
        for stage in slp.get("stage", []):
            mode = stage.get("mode")
            duration = stage["stop"] - stage["start"] + 1
            label = SLEEP_MODES.get(mode)
            if label:
                totals[label] += duration

        hr_bytes = None
        detail_record = detail_by_date.get(raw_date)
        if detail_record:
            hr_b64 = detail_record.get("data_hr")
            if hr_b64:
                hr_padding = (4 - len(hr_b64) % 4) % 4
                hr_bytes = list(base64.b64decode(hr_b64 + "=" * hr_padding))

        return {
            "raw_date": raw_date,
            "tz_info": tz_info,
            "st_ts": slp.get("st"),
            "ed_ts": slp.get("ed"),
            "score": slp.get("ss"),
            "totals": totals,
            "stages": slp.get("stage", []),
            "rhr": slp.get("rhr"),
            "wake_count": slp.get("wc"),
            "naps": obj.get("odd_stage", []),
            "hr_bytes": hr_bytes,
        }

    def _compute_avg_sleep_hr(self, raw_date, tz_info, st_ts, ed_ts, hr_bytes, stages):
        """Avg sleep HR incl./excl. awake minutes, sliced from the data_hr
        blob using the (possibly corrected) st_ts/ed_ts window. Returns
        (avg_incl, avg_excl), either of which may be None if no data."""
        if not (hr_bytes and st_ts and ed_ts):
            return None, None

        # Midnight in local tz for this date
        date_obj = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=tz_info)
        midnight_ts = int(date_obj.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        idx_start = max(0, (st_ts - midnight_ts) // 60)
        idx_end = min(len(hr_bytes), (ed_ts - midnight_ts) // 60)

        sleep_hr = [v for v in hr_bytes[idx_start:idx_end] if v not in (254, 255) and v > 0]
        avg_incl = round(sum(sleep_hr) / len(sleep_hr), 1) if sleep_hr else None

        # Stage start/stop are minute indices from the previous day's midnight
        # (1440-2880 range) regardless of any st_ts/ed_ts correction applied
        # above — CLAUDE.md confirms only slp.st/ed are affected by the tz bug.
        awake_indices = set()
        for stage in stages:
            if stage.get("mode") == 7:
                s_idx = stage["start"] - 1440
                e_idx = stage["stop"] - 1440
                awake_indices.update(range(s_idx, e_idx + 1))
        sleep_hr_excl = [
            hr_bytes[i] for i in range(idx_start, idx_end)
            if i not in awake_indices and hr_bytes[i] not in (254, 255) and hr_bytes[i] > 0
        ]
        avg_excl = round(sum(sleep_hr_excl) / len(sleep_hr_excl), 1) if sleep_hr_excl else None

        return avg_incl, avg_excl

    def decode_sleep(self, start_date: str = None, end_date: str = None):
        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()

        records, detail_by_date = self._fetch_sleep_records(start_date, end_date)
        extracted = []

        for record in records:
            rec = self._extract_sleep_record(record, detail_by_date)
            if rec is None:
                print(f"\n{record.get('date_time', 'unknown')}: no summary data\n")
                continue
            extracted.append(rec)

            raw_date, tz_info = rec["raw_date"], rec["tz_info"]
            st_ts, ed_ts = rec["st_ts"], rec["ed_ts"]
            sleep_start = datetime.fromtimestamp(st_ts, tz=tz_info).strftime("%H:%M") if st_ts else "?"
            sleep_end = datetime.fromtimestamp(ed_ts, tz=tz_info).strftime("%H:%M") if ed_ts else "?"
            total_min = round((ed_ts - st_ts) / 60) if st_ts and ed_ts else 0

            avg_incl, avg_excl = self._compute_avg_sleep_hr(
                raw_date, tz_info, st_ts, ed_ts, rec["hr_bytes"], rec["stages"]
            )
            avg_hr_incl_str = f"{avg_incl} BPM" if avg_incl is not None else "n/a"
            avg_hr_excl_str = f"{avg_excl} BPM" if avg_excl is not None else "n/a"

            print(f"{'='*50}")
            print(f"Date : {raw_date}")
            print(f"Sleep: {sleep_start} → {sleep_end}  ({total_min // 60}h {total_min % 60}m total)")
            print(f"Score: {rec['score'] if rec['score'] is not None else '?'}")
            print(f"{'─'*50}")
            for label in ("Deep", "Light", "REM", "Awake"):
                mins = rec["totals"][label]
                bar = "█" * (mins // 5)
                print(f"  {label:<6} {mins:>4} min  {bar}")

            if rec["rhr"] is not None:
                print(f"  RHR: {rec['rhr']} BPM")

            if rec["wake_count"] is not None:
                print(f"  Wake count: {rec['wake_count']}")

            print(f"  Avg sleep HR (incl. awake): {avg_hr_incl_str}")
            print(f"  Avg sleep HR (excl. awake): {avg_hr_excl_str}")

            if rec["naps"]:
                print(f"  Naps ({len(rec['naps'])}):")
                for nap in rec["naps"]:
                    nap_st = nap.get("start") or nap.get("st")
                    nap_ed = nap.get("stop") or nap.get("ed")
                    if nap_st and nap_ed:
                        nap_start = datetime.fromtimestamp(nap_st, tz=tz_info).strftime("%H:%M")
                        nap_end = datetime.fromtimestamp(nap_ed, tz=tz_info).strftime("%H:%M")
                        nap_dur = round((nap_ed - nap_st) / 60)
                        print(f"    {nap_start} → {nap_end}  ({nap_dur}m)")
            print()

        return extracted

    def sync_sleep_to_supabase(
        self,
        confirmed_dates,
        offsets: dict = None,
        start_date: str = None,
        end_date: str = None,
    ):
        """Upload sleep/HR/HybridCharge data to Supabase for explicitly
        confirmed dates only.

        This does NOT do its own interactive prompting — per the agreed
        workflow, run decode_sleep() first, compare the printed
        sleep_start/sleep_end against the Zepp app in chat, then call this
        with the dates you've confirmed (and any corrections) once agreed.

        Args:
            confirmed_dates: list of "YYYY-MM-DD" date strings to upload.
                Any date NOT in this list is skipped entirely — nothing is
                uploaded without being explicitly confirmed.
            offsets: optional {"YYYY-MM-DD": minutes} dict. For a listed
                date, shifts BOTH sleep_start and sleep_end by that many
                minutes (matches the documented "+1h to both boundaries"
                correction pattern) before recomputing total duration and
                avg sleep HR. Does NOT affect sleep_stages, whose raw
                start/stop values are a separate, unaffected code path.
            start_date/end_date: range to fetch from the API (defaults to
                the last 7 days, same as decode_sleep()).
        """
        if end_date is None:
            end_date = date.today().isoformat()
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).isoformat()
        offsets = offsets or {}

        records, detail_by_date = self._fetch_sleep_records(start_date, end_date)

        hybridcharge_by_date = {}
        for wc_rec in self.fetch_wake_hybridcharge(days=(date.today() - date.fromisoformat(start_date)).days + 1):
            ts = wc_rec["start_time"] or wc_rec["item_timestamp"]
            if ts:
                day = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                hybridcharge_by_date[day] = wc_rec["wake_charge"]

        confirmed = set(confirmed_dates)

        for record in records:
            rec = self._extract_sleep_record(record, detail_by_date)
            if rec is None:
                continue
            raw_date = rec["raw_date"]
            if raw_date not in confirmed:
                continue

            tz_info = rec["tz_info"]
            st_ts, ed_ts = rec["st_ts"], rec["ed_ts"]

            offset_min = offsets.get(raw_date, 0)
            if offset_min and st_ts and ed_ts:
                st_ts += offset_min * 60
                ed_ts += offset_min * 60

            avg_incl, avg_excl = self._compute_avg_sleep_hr(
                raw_date, tz_info, st_ts, ed_ts, rec["hr_bytes"], rec["stages"]
            )
            total_min = round((ed_ts - st_ts) / 60) if st_ts and ed_ts else None
            # Stored as naive (no UTC offset) wall-clock local time on purpose:
            # Supabase Studio's table grid always renders timestamptz in UTC
            # with no per-project override, so an offset-aware value here
            # would always display 1-2h off from the confirmed local time.
            # Dropping the offset makes the grid show exactly the confirmed
            # clock time, matching the Zepp app.
            sleep_start_iso = datetime.fromtimestamp(st_ts, tz=tz_info).replace(tzinfo=None).isoformat() if st_ts else None
            sleep_end_iso = datetime.fromtimestamp(ed_ts, tz=tz_info).replace(tzinfo=None).isoformat() if ed_ts else None

            self.supabase.table("dates").upsert({"date": raw_date}, on_conflict="date").execute()

            self.supabase.table("sleep_summary").upsert({
                "date": raw_date,
                "sleep_start": sleep_start_iso,
                "sleep_end": sleep_end_iso,
                "total_minutes": total_min,
                "score": rec["score"],
                "deep_minutes": rec["totals"]["Deep"],
                "light_minutes": rec["totals"]["Light"],
                "rem_minutes": rec["totals"]["REM"],
                "awake_minutes": rec["totals"]["Awake"],
                "resting_hr": rec["rhr"],
                "wake_count": rec["wake_count"],
                "avg_sleep_hr_incl_awake": avg_incl,
                "avg_sleep_hr_excl_awake": avg_excl,
            }, on_conflict="date").execute()

            if rec["hr_bytes"] is not None:
                clean_hr = [v for v in rec["hr_bytes"] if v not in (254, 255) and v > 0]
                self.supabase.table("heart_rate_daily").upsert({
                    "date": raw_date,
                    "hr_values": clean_hr,
                }, on_conflict="date").execute()

            self.supabase.table("sleep_stages").delete().eq("date", raw_date).execute()
            stage_rows = [
                {
                    "date": raw_date,
                    "stage_start": stage["start"] - 1440,
                    "stage_stop": stage["stop"] - 1440,
                    "mode": SLEEP_MODES.get(stage.get("mode"), str(stage.get("mode"))),
                }
                for stage in rec["stages"]
            ]
            if stage_rows:
                self.supabase.table("sleep_stages").insert(stage_rows).execute()

            if raw_date in hybridcharge_by_date:
                self.supabase.table("wake_hybridcharge").upsert({
                    "date": raw_date,
                    "wake_charge": hybridcharge_by_date[raw_date],
                }, on_conflict="date").execute()

            print(f"Uploaded {raw_date} to Supabase.")

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
