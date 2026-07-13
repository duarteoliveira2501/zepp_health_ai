# CLAUDE.md — zepp_health_ai

## Project Purpose

Personal health data pipeline that reverse-engineers the Zepp Health API to extract
sleep and health metrics from an Amazfit Helio Strap, stores data in Supabase, and
layers AI features on top. Dual purpose: genuinely useful personal tool + AI/data
engineering portfolio piece. Longer-term ambition: SaaS for other technically willing
users (requires official Zepp data partner status for OAuth REST API access).

**Pillars:** weekly AI-generated health briefings, daily curiosity cards via email,
interactive web dashboard, daily journaling app, conversational data layer via
Supabase MCP + Claude.

## Environment

- Solo developer, Mac (username `teresamelo`), Python 3 (`python3`, not `python`)
- GitHub: `duarteoliveira2501/zepp_health_ai`
- Zepp userid: `7084546365`
- API host: `api-mifit-de2.zepp.com`
- Traffic interception: Android Studio emulator (Pixel 9a, Android 17) + HTTP Toolkit
- Storage: Supabase (`supabase-py`)
- AI layer: Anthropic API (pay-per-use)

## Workflow (IMPORTANT — follow this order)

1. **Research first:** Before touching the emulator/HTTP Toolkit for any new metric,
   audit community projects exhaustively — especially `bentasker/zepp_to_influxdb`
   and `zepp-health-cli` by m4ary. Treat their field/endpoint guesses as unverified
   hypotheses, never as ground truth to copy directly.
2. **Verify via live capture:** Confirm the actual eventType/subType/field names by
   triggering the specific screen in the Zepp app and inspecting HTTP Toolkit traffic.
   Community repos have been wrong before (e.g. bentasker's `lt`=REM mapping was
   incorrect; m4ary's repo guessed `subType=real_data` for HybridCharge when the
   actual value is `wake_data`).
3. **Document in CLAUDE.md first**, including edge cases discovered during
   verification, before writing extraction code.
4. **Then build in Claude Code**, referencing the documented mapping.
5. Plan/interpret in Claude Chat → execute in Claude Code → paste script output back
   into Chat for interpretation. Duarte runs scripts locally.
6. Git workflow: commit → push as standard close-of-session action.

## Authentication

- Single-session tokens — each new login invalidates the previous.
- Weekly manual extraction ritual: open emulator → log into Zepp → capture
  `apptoken` via HTTP Toolkit → run script → restore phone session.
- Full automation not achievable without rooting (rejected) or abandoning the Zepp
  app (rejected). Weekly manual refresh is the accepted trade-off.
- Android cert setup: recent Android versions require manual CA install (Security
  settings → More security settings → Encryption & Credentials → Install a
  certificate → CA Certificate). System trust will show "disabled" even after
  install — this only breaks apps with certificate pinning. Zepp does NOT pin
  certs; user-trust-only interception works fine for it.

## Confirmed Field Mappings

**Known gap list (updated 2026-07-02):**
- **HRV** — confirmed to exist server-side (Zepp's official partner API
  exposes it to sanctioned integrations like Intervals.icu), but no
  client-fetch path has been found via the mobile app's endpoints yet.
  Official-partner-API access is noted as a possible future option, not an
  active task. See "Heart Rate Variability (HRV) — NOT YET FOUND" below.
- **Respiratory rate / hypopnea** — still fully uncharted. No leads from
  community projects, forums, or official partner integrations. See
  "Uncharted — require emulator session" below.
- **Sleep regularity %** — reclassified as not a real extractable metric
  (it's a user-configured setting, not a reported score). Removed from the
  active backlog. See "Not a retrievable metric — reclassified" below.

### Sleep (`query_type=detail` endpoint family)
- `ss` — sleep score
- `dp` — deep sleep
- `lt` — light sleep (CORRECTED — community repos, e.g. bentasker, map this to REM;
  verified wrong against the Zepp app)
- `dt` — REM sleep
- `wk` — awake
- `we` — wake events
- `st` / `ed` — Unix timestamps (start/end)
- `stage` array — blocks per transition; `4=Light, 5=Deep, 7=Awake, 8=REM`
- `rhr` — resting HR, single morning measurement
- `wc` — wake count
- `odd_stage` — nap blocks, same structure as `stage`

### Heart Rate Blob (`data_hr` in `query_type=detail`)
- 1440 single bytes, one per minute of the day (NOT 720 shorts as bentasker
  documented — that reflects a different device)
- `255` = unscheduled (band not worn — e.g. charging, off-wrist),
  `254` = failed reading (worn but bad signal/motion), all other
  values 1–253 = real BPM (heart rate can legitimately exceed 200
  during intense exercise, so do NOT filter on a <200 threshold)
- Coordinate system trap: sleep `start`/`stop` values are minutes from the
  *previous* day's midnight (1440–2880 range) — subtract 1440 to align with the
  blob's 0–1440 index
- Average sleep HR (excl. awake periods) implemented and confirmed within ~1–2 BPM
  of the Zepp app
- **Always slice by sleep_start/sleep_end before analyzing "sleep HR"** —
  the array itself has no concept of sleep vs. awake, it's just every
  minute of the day.

### Wake HybridCharge (CONFIRMED — verified via live capture AND extraction 2026-07-02)
- Endpoint: `GET /v2/users/me/events`
- Params: `eventType=Charge&subType=wake_data&from={ms}&to={ms}&limit=200&reverse=true`
- Response: `items[].value.samples[]` array
- Extraction implemented in `zepp_client.py`: `fetch_wake_hybridcharge()` and
  `decode_hybridcharge()`. Reuses the shared `_last_n_days_ms()` helper (now
  also used by `probe_sleep_subtypes`, which previously had its own
  duplicate/buggy inline copy of the same date logic — now deduplicated).
- **`wakeCharge`** — the score shown in-app (0–100). NOT present on every sample —
  earliest record in initial test capture had no `wakeCharge` key (only
  `mentalWake`/`physicalWake`). Code must handle missing key defensively, don't
  assume presence.
- Supporting fields in same payload (uncharted, log but don't build features on yet):
  - `mentalWake` / `physicalWake` — can go negative, presumably the two blended
    sub-components of HybridCharge
  - `exertionScore` — appears to be previous day's training load
  - `dailyFitnessScore` — unclear scale, roughly 0.4–0.9 range observed
  - `chronicWeightDaily` — unclear, possibly a smoothing/weighting factor
  - `algoVersion` — worth logging; Zepp iterates this over time (observed
    `1.1.4.3` → `1.1.5.3` → `1.1.5.3.1` → `1.1.5.3.3` across history)
- In-app terminology: UI shows "HybridCharge," internal analytics/telemetry still
  use "biocharge" as the codename (found in app's own analytics event stream,
  `tp":"biocharge"`), older docs/watch firmware may show "BioCharge."
- Source: verified via live HTTP Toolkit capture. Cross-checked against
  `m4ary/zepp-health-cli`, which guessed `subType=real_data` — directionally
  right (correct `eventType=Charge`) but wrong subType (`wake_data` is correct).
  Extraction cross-checked field-by-field against the app's 7-day view for
  2026-06-26 through 2026-07-02 — all 7 values matched exactly.
- `decode_sleep` (sleep extraction) is unaffected by the `_last_n_days_ms` fix
  or the timestamp/startTime skew below — it uses a separate ISO-date code
  path (`date.today()`/`.isoformat()`) and never calls `_last_n_days_ms`.

### Known Trap — timestamp/startTime skew
- `item.timestamp` and `value.startTime` are NOT aligned — `item.timestamp`
  runs ~22 hours behind the `startTime` that determines the display date.
- Any date-range filtering on this endpoint must filter on `startTime`, not
  `item.timestamp`, or the oldest day in a window can silently drop even with
  a correctly midnight-anchored `from_ts`.
- Handled in `fetch_wake_hybridcharge`: pads the query window by one extra day
  when using the default range, then filters results back down to the exact
  requested range by `startTime`. Explicit `from_ts`/`to_ts` passed by a
  caller are used as-is.

### Heart Rate Variability (HRV) — NOT YET FOUND
- Not present via `eventType=hrv`, `eventType=Charge`, `eventType=readiness`, or
  substring filters `hrv`, `biocharge`, `bio_charge` in HTTP Toolkit as of
  2026-07-02.
- `m4ary/zepp-health-cli` guesses: `hrv_sdnn`/`real_data` (unverified),
  `HRVRMSSD`/`real_data` (unverified).

**Confirmed negative results (live emulator + HTTP Toolkit session, 2026-07-02):**
- Tapping into the HRV detail screen (D/W/M/Y tabs, scrolling) fires ZERO
  dedicated API requests. Confirmed twice: once on an already-open/warm
  session, once on a fresh cold-start session opening HRV for the first time.
  The screen renders without any new network call.
- Confirmed via `debug_raw_slp.py` that HRV is NOT present anywhere in the
  sleep-summary `slp` dict (full unfiltered dump checked for 2026-07-02, no
  38ms value found anywhere). Rules out `band_data.json`/summary as the source.
- Confirmed via cold-start capture (38 requests, full list reviewed) that HRV
  is not part of the app's initial batch sync on launch either.
- `/users/{id}/heartRate` endpoint (the one m4ary's zepp-health-cli documents)
  returns empty `{"items": []}` — consistent with bentasker's finding for a
  different device years ago. Confirmed dead for the Helio Strap too.

**Conclusion:** HRV appears to be a server-side computed/synced value with no
client-triggered fetch path observed so far. Most likely candidates for where
it actually lives, untested:
1. A background/scheduled sync between watch and Zepp servers (not tied to any
   UI interaction) — would require a longer passive capture window (30+ min)
   to catch, not a quick tap-through.
2. A direct watch-to-phone Bluetooth sync path outside the HTTPS-proxied
   traffic entirely (Gadgetbridge's HRV support works this way, over BLE, not
   REST — different attack surface than everything else in this project).

- **Do not re-attempt** the "tap the HRV screen and watch for a new call"
  approach — this has been tried multiple times and is a confirmed dead end.
  Next real attempt should be a longer idle capture session or explicit BLE
  traffic inspection, not more screen-tapping.

### Official Zepp Partner API — context, not a direct lead
- Intervals.icu (a legitimate, Zepp-sanctioned integration partner) has a
  working "HRV (rMSSD)" option in their Amazfit sync settings, confirmed live
  as of April 2026. This is via Zepp's official OAuth partner API — a
  completely separate, sanctioned pipeline from the mobile-app endpoints this
  project reverse-engineers. As of June 2025 it was listed as "coming soon,"
  so Zepp enabled it for partners sometime in the following ~10 months.
- Confirms HRV data does exist and flows through Zepp's backend somewhere —
  it is not vaporware. But it does not hand us an endpoint; the official
  partner API uses different auth and likely different paths than the mobile
  app.
- Known gotcha if this data is ever sourced from Intervals.icu or a similar
  path: a reported bug (Intervals.icu forum, Sep 2025) has HRV values landing
  on the wrong day — assigned to the previous day instead of the correct one.
  Watch for date-offset issues if HRV data is ever pulled from a third-party
  synced source rather than directly from Zepp.
- Noted as a possible future path (not active): applying for Zepp's official
  developer/partner access, which would provide documented field names
  instead of continued guessing. Deliberately parked, not a current task.

### Average sleep HR — NOT a direct API field (confirmed 2026-07-12)
- The app displays a single "Avg sleep HR" number (e.g. "Avg 53 BPM") on the
  Sleep Heart Rate screen. Checked whether this is a field the API hands back
  directly, rather than something we compute ourselves from `data_hr`.
- **Full `slp` summary dict dumped and inspected (2026-07-12, unfiltered)** —
  every key present: `stage`, `odd_stage`, `spos`/`spol`/`spor`/`spob`, `st`,
  `ed`, `obt`, `ebt`, `dp`, `lt`, `wk`, `wc`, `supNap`, `supRem`, `is`, `lb`,
  `dt`, `rhr`, `ss`, `to`, `sleepSource`, `sleepScoreVersion`,
  `sleepVersion`, `napVersion`, `sleepAlgoVersion`. No average-HR field.
  `rhr` is a single resting-HR reading, not a sleep-window average.
- Confirmed this is identical whether pulled from the `query_type=summary`
  response or the embedded `summary` field inside the `query_type=detail`
  response — same `slp` structure both places.
- **`stp` block also dumped** — steps/activity data only (`ttl`, `dis`,
  `cal`, `stage` with step counts, `stepStageSummary`), no HR content.
- **Probed `/v2/users/me/events` with 11 plausible eventType/subType
  combinations** (`sleep`/`Sleep` × `heart_rate`/`sleep_hr`/`avg_hr`/
  `avg_heart_rate`, plus reversed `heart_rate`/`heartrate` × `sleep`, and
  `HeartRate`/`SleepHeartRate` × `sleep_summary`/`summary`) — all returned
  `200` with `0 items`. No dedicated sleep-HR-summary event type found.
- **Conclusion:** no direct field exists via any endpoint checked so far.
  The app's displayed average is being computed (client-side or
  server-side-but-unexposed) from the same minute-level `data_hr` blob this
  project already has access to — same category as sleep score (`ss`, which
  *is* returned pre-computed) but unlike sleep score, avg sleep HR is not
  exposed as a field. Continuing to compute it ourselves from `data_hr` is
  the correct approach, not a workaround for a missing integration.

### Unmapped — do not assume
- `trhr`, `is` — mapped incorrectly in early sessions, left explicitly unmapped
  until re-verified.

### Deprioritised (confirmed low-value)
- `supNap`, `supRem` — capability flags, not data
- `spos`/`spol`/`spor`/`spob` — always zero on Helio Strap
- `obt`, `ebt`, `lb` — not investigated, low priority

### Permanently out of scope
- Blood oxygen/SpO2, blood pressure, blood glucose — Helio Strap has no sensors
  for these (bentasker's repo has a working SpO2 endpoint but it's for other
  devices with the sensor).

### Uncharted — require emulator session
- Respiratory rate, hypopnea — not present in any community project. Requires
  targeted emulator capture, deprioritized until Supabase schema work is
  underway.
- Confirmed (2026-07-02 research pass): searched community projects, forums,
  and official Zepp partner integrations (Intervals.icu) — neither respiratory
  rate nor hypopnea appears anywhere as a retrievable field via any known path,
  official or reverse-engineered. These remain genuinely uncharted, not just
  under-documented. No new leads found.

### Not a retrievable metric — reclassified
- **Sleep regularity %** — removed from the uncharted/backlog list. Confirmed
  via Zepp's own support documentation: this is a user-configured *setting*
  (Sleep > Sleep Regularity > Sleep Schedule in the Zepp app), not a computed
  score the app reports back. There is no field to extract. Deprioritized
  permanently unless evidence emerges otherwise.

## Known Traps / Lessons Learned

0. **Never include "today" in the default upload window — end at yesterday.**
   Confirmed 2026-07-13: uploading `heart_rate_daily` for 2026-07-12 showed a
   759-minute continuous block of `254` (failed reading) from 11:21am through
   midnight, despite the band being worn nearly all day. Root cause: the
   watch's data only reaches Zepp's servers after the phone app does a
   watch -> phone -> cloud sync, which may not have completed for the most
   recent day(s) at the time the script runs — pulling a day too soon
   silently returns incomplete `data_hr`. Fixed: `decode_sleep()` and
   `sync_sleep_to_supabase()` now default `end_date` to yesterday
   (`date.today() - 1 day`), not today, so the rolling window is
   "yesterday back 7 days" rather than "today back 7 days." An explicit
   `end_date` passed by the caller still bypasses this default and is used
   as-is — this only protects the no-args default case. `decode_sleep` was
   already documented (see "Known Trap — timestamp/startTime skew" section)
   as an unrelated separate code path from `fetch_wake_hybridcharge`'s
   `_last_n_days_ms` helper; that helper was NOT changed by this fix and may
   have the same today-vs-yesterday exposure if HybridCharge data ever shows
   similar gaps — not yet confirmed as an actual problem there, just an
   unverified same-shape risk.

1. **Research before reverse-engineering.** Skipping the community-project audit
   step caused redundant work early in the project.
2. **Field mappings must be verified, not assumed.** Several initial interpretations
   (`lt`, `trhr`, `is`) were wrong. Only document confirmed mappings.
3. **Coordinate system traps.** HR blob indexing (0–1440) vs. stage timestamps
   (1440–2880) caused a real bug. Always verify the reference frame before slicing
   binary data.
4. **HR blob encoding is device-specific.** bentasker's docs (720 shorts) reflect a
   different device; Helio Strap uses 1440 single bytes. Read community code
   critically, don't adopt directly.
5. **Community repo guesses are hypotheses, not ground truth**, even when
   structurally close. Verify eventType AND subType/params via live capture before
   trusting a mapping — a repo can be right on one and wrong on the other (see
   HybridCharge: `Charge` correct, `real_data` wrong).
6. **UI naming ≠ internal API naming.** "HybridCharge" in the UI, `biocharge` in
   analytics telemetry, "BioCharge" on watch firmware/older docs, `Charge` as the
   API `eventType`. When hunting for an endpoint, try all naming variants.
7. **Not all app screens fire fresh API calls on cold start.** Some data (like
   HybridCharge/HRV detail screens) only loads its underlying request when you
   navigate into that specific screen, not on general app open. Isolate one screen
   at a time when hunting for an endpoint — clear the HTTP Toolkit log first.
8. **Full automation is not achievable** without rooting the phone or abandoning
   the Zepp app — both rejected. Weekly manual extraction is the accepted trade-off.
9. **A fix that looks structurally correct (e.g. midnight-anchoring a date
   boundary) can still fail if the real cause is elsewhere** (e.g. a
   field-level timestamp skew specific to one endpoint). When a fix doesn't
   resolve the symptom, inspect the raw API response directly rather than
   iterating on theory — don't assume the first plausible root cause is the
   actual one.
10. **HR sentinel filtering bug (fixed 2026-07-05):** code previously used
    `v < 200` as a proxy for "real reading," which would incorrectly discard
    legitimate high-intensity HR readings above 200 BPM. Correct filter is
    `v not in (254, 255) and v > 0` — only 254 and 255 are sentinel codes.
11. **`hr_values` is a full-day array, not a sleep-only array.** It's indexed
    by minute-of-day (0–1439), covering the whole 24h, not just the sleep
    window. Any sleep-HR analysis MUST slice it using `sleep_start`/`sleep_end`
    from `sleep_summary` first — treating the raw array as "sleep HR" without
    slicing will pick up daytime activity (workouts, walks) and produce
    false spikes. Confirmed bug 2026-07-13: a 140+ bpm "sleep spike" turned
    out to be a daytime event hours after wake time.

## Known Data Quality Issues

### Sleep start/end time reliability — UNRESOLVED

**Status: confirmed unresolved as of 2026-07-10, not just under-documented.**
Investigated via a disposable read-only diagnostic script (`check_tz_range.py`,
not committed to the pipeline) that pulled the `summary` blob's `tz`, `st`,
and `ed` fields across four separate historical periods and cross-checked
the resulting sleep_start/sleep_end against remembered actual bedtimes.

**1. Core finding — `tz` cannot be trusted as a conversion signal.** Results
were inconsistent across every period tested:

- **February 2026 (no travel, winter):** `tz=3600` (UTC+1/CET) — CONFIRMED
  CORRECT against memory.
- **April 2026, Korea trip (actual travel dates Apr 9–20):** the `tz` field
  switched to Korea's offset (`32400`) starting **April 4** — 5 days too
  early — and reverted **April 22–23** — 2–3 days too late. This lines up
  with a batch-sync/upload-delay artifact (see the `sync` field found during
  this investigation, which showed multiple distinct nights uploading in a
  single batch), not real-time location tracking. Worse: a raw timestamp
  check on **April 20** (a confirmed Korea night) only produced the correct
  bedtime when using **Spain's offset (3600)**, not Korea's own `tz` value
  (`32400`) for that same record — meaning even the raw `st`/`ed` timestamps
  don't behave as naively expected during travel.
- **June 2026, Portugal trip (actual travel dates June 13–27):** the `tz`
  field correctly tracked the Portugal date range — but by showing `0`, not
  Portugal's real UTC offset of `3600` — and was CONFIRMED CORRECT against
  memory for the entire month, including the Spain bookend dates (June 1–13,
  28–30), which showed `tz=3600` and were also confirmed correct.
- **July 2026 (no travel, current period):** `tz=3600` — the same value as
  the correct June Spain readings — but CONFIRMED INCORRECT against memory.
  Actual July 10 bedtime was 02:07; the data showed 01:07, a consistent
  1-hour-early error.

**2. Why this rules out simple theories.** June and July show the *identical*
`tz` value (`3600`) for Spain, but June is correct and July is wrong — so
this is **not** a simple stuck-on-winter-time DST bug (that would require the
`tz` value itself to be wrong, not just the resulting accuracy). A plausible
candidate — an Amazfit-acknowledged DST bug affecting Helio Strap
Exertion/Weather features after the Oct 2025 DST-to-standard-time switch —
was investigated as a possible explanation but does **not** cleanly fit this
data pattern. Noted here specifically so this theory isn't fruitlessly
re-investigated later without new evidence.

**3. No working formula exists.** No fixed offset, DST-calendar rule, or
travel-detection formula tested during this investigation reliably predicts
correct sleep start/end times across all four observed periods. This is
confirmed **unresolved**.

**4. Scope — `avg_sleep_hr` is NOT affected.** `avg_sleep_hr_incl_awake` and
`avg_sleep_hr_excl_awake` have been separately confirmed correct by the user
and are outside this issue. The problem applies specifically to
`sleep_start`/`sleep_end` display values (i.e. any use of `tz` to convert
`st`/`ed` to local wall-clock time).

**5. Recommended next steps (not yet started):**
- Do **not** build Supabase schema fields or AI features that assume `st`/`ed`
  are trustworthy without a disclaimer or spot-check step.
- A live emulator + HTTP Toolkit capture session, run in real time while
  manually noting actual bedtime, would likely give a cleaner signal than
  more historical data analysis — historical analysis alone produced
  contradictory results across all four test periods today.
- Consider whether app version, phone OS version, or a specific Zepp/Amazfit
  server-side change between late June and July 10 could be a lead, if this
  is ever revisited.

**6. Minute-level `data_hr` blob does NOT show the same 1-hour error (spot-checked
2026-07-10).** That night's `sleep.st` reported 01:07 (documented bug — actual
bedtime was 02:07, one hour early). The independently-decoded `data_hr` byte
array for the same night shows HR still elevated/variable (56–81 bpm) through
01:55, then settling into a stable resting baseline (54–60 bpm) starting right
at ~02:00–02:15 — matching the true 02:07 bedtime, not the buggy 01:07 sleep
summary value. `data_hr` is indexed as minute-of-day directly (see "Heart Rate
Blob" section above) and never passes through the `tz`-based epoch conversion
that `sleep.st`/`sleep.ed` use, so it appears to be a structurally separate,
more reliable path for inferring actual sleep onset than the reported
`sleep_start` field. Only spot-checked on one night so far — not exhaustively
verified across the Korea-trip period, but a promising cross-check signal if
this issue is revisited.

**7. The Zepp app's own displayed bedtime can differ from BOTH the raw `tz`
field AND from any offset we'd derived (spot-checked April 20, 2026 — a
Korea-trip night already flagged in point 1 above).** Three different values
now exist for this single night's `sleep.st`:
- Raw `tz` field (32400 = Korea/UTC+9) → 07:27 AM (nonsensical as a bedtime)
- "Spain" offset (3600), the value point 1 above found to check out against
  memory for this date → 23:27 (April 19)
- **What the Zepp app itself displays on the Sleep screen → 01:27 AM** — this
  corresponds to a +3h offset, which doesn't match the `tz` field value or
  any other field visible in the decoded summary payload. The app is not
  simply mis-happlying the `tz` field we can see; it's showing a number that
  can't be reproduced from the data this project has access to.
- Cross-checking against `data_hr`: HR is still elevated/variable (58–77 bpm)
  through 22:50, hits a ~60-minute sensor gap (254 failed-reading) from
  22:55–23:55, then is already stable and low (48–56 bpm) by 00:00 — a full
  87 minutes before the app's claimed 01:27 sleep onset. This contradicts
  01:27 as the real bedtime and instead supports the earlier ~23:27 estimate.
- **Implication:** the sleep-timing bug is not fully explained by a bad `tz`
  field alone — the app's own UI can diverge from its own underlying data by
  yet another, different amount. Any future fix attempt needs to account for
  this, not just "correct the tz offset" in the extraction code.

**8. The `data_hr` blob's own minute-of-day coordinate frame is NOT itself
shifted — only `slp.st`/`slp.ed` are wrong (confirmed 2026-07-11 and
2026-07-12, two consecutive days).** For both dates, the raw API `st`/`ed`
decoded to a window exactly 1 hour earlier than the app's own displayed
window (e.g. 2026-07-12: API gave 02:52→10:20, app showed 03:52→11:20).
Applying a **+1 hour shift to both `st` and `ed`** (window boundaries only —
`data_hr`'s own minute-index-to-clock-time mapping, via the existing
`tz`-derived midnight anchor, was left untouched) reproduced the app's
displayed window exactly on both dates, AND the resulting minute-by-minute
HR curve shape (deep-sleep dips, REM bumps, and — most tellingly — the exact
clock-time position and relative size of the largest awake/HR spike of the
night) matched the app's own sleep-HR chart closely on both dates. This only
works if the blob's minute→clock mapping was already correct; if the blob
itself were also shifted, correcting only `st`/`ed` would have misaligned
the extracted slice against the app's chart instead of matching it.
**Conclusion: the 1440-point `data_hr` array is captured/indexed correctly.
The bug is isolated to `slp.st`/`slp.ed` (and anything downstream that keys
off them, e.g. avg-sleep-HR window slicing).** This is consistent with — and
now more strongly evidenced than — point 6 above. Still only 2 consecutive
days confirmed with this specific +1h/both-boundaries pattern; not yet
proven as a universal rule (see point 7, where a single fixed offset did NOT
explain a Korea-trip night). Treat +1h-on-both-boundaries as a promising,
recently-recurring pattern to keep spot-checking day by day, not yet a
blanket correction to bake into the pipeline.

### `data_hr` resolution — per-minute, not per-second (confirmed 2026-07-12)

- The Zepp app's live Heart Rate chart appears to render per-second (or
  otherwise sub-minute) samples. `data_hr` only exposes one byte per minute
  (see "Heart Rate Blob" above), so it is a coarser signal by construction.
- Spot-checked 2026-07-11, 1:06 PM–2:06 PM: clock-time alignment between
  `data_hr` and the app's chart is confirmed correct (e.g. app showed 119 BPM
  at 2:06:20 PM; `data_hr` showed 116 BPM at 2:06 PM — a match once the
  20-second offset is accounted for). Overall curve shape also matched
  closely when plotted.
- However, the app displayed a ~145 BPM peak around 1:57–2:00 PM that
  `data_hr` did not capture — its per-minute values over the same window
  topped out at 139 BPM (1:57 PM). This is expected: a per-minute value
  (whatever sampling/aggregation Zepp uses internally to produce it) can
  legitimately miss a brief sub-minute spike that a per-second feed would
  catch.
- **Conclusion: this is a resolution limitation, not an alignment bug.**
  Don't re-investigate short peak-value mismatches between `data_hr` and the
  app as a timestamp issue — check whether the gap is consistent with normal
  minute-level smoothing first. This is a separate issue from the confirmed
  `slp.st`/`slp.ed` timezone bug above; `data_hr` clock alignment itself
  remains solid.

## Supabase Upload (`sync_sleep_to_supabase`)

- 6 tables exist: `dates`, `sleep_summary`, `sleep_stages`, `heart_rate_daily`,
  `wake_hybridcharge`, `naps`. `naps` is deliberately unused/deferred — never
  written to.
- `sleep_summary.total_minutes` is the full time-in-bed duration (sleep_end −
  sleep_start), which includes awake time. `sleep_summary.actual_sleep_time`
  (added 2026-07-13) is `deep_minutes + light_minutes + rem_minutes` — the
  actual time asleep, excluding awake periods. Both columns are kept; use
  `actual_sleep_time` for anything meant to represent real sleep duration.
- "Sleep duration" always means `actual_sleep_time` (excludes awake minutes).
  `total_minutes` is only the raw span from sleep_start to sleep_end and
  includes awake time — do not use it when the ask is for 'how long I
  slept.' Default to actual_sleep_time unless total_minutes is explicitly
  requested.
- **Catch-up syncs (missed a week or more, added 2026-07-13):**
  `decode_sleep()`'s default `start_date` is no longer a fixed "7 days
  ago" — it auto-detects the most recent date already in `sleep_summary`
  (via `_last_uploaded_date()`) and starts the day after it, through
  yesterday. A normal weekly run naturally covers "since last time"; a run
  after missing several weeks automatically backfills the whole gap with
  no manual date math. Falls back to `fallback_days` (default 7) days ago
  only if `sleep_summary` is completely empty (first-ever run). If the
  auto-detected start_date is already past end_date (nothing missing),
  `decode_sleep()` prints a message and returns `[]` without hitting the
  Zepp API. `skip_uploaded=True` (still the default) additionally skips
  any individual date within the resolved range that's already in
  `sleep_summary` — cheap insurance on top of the start_date
  auto-detection, in case a date was uploaded out of the normal
  day-by-day order. Pass `start_date` explicitly to override
  auto-detection, or `skip_uploaded=False` to force re-decoding of an
  already-uploaded date (e.g. to re-verify or re-confirm a correction).
  Note this only affects what's *printed/returned* by `decode_sleep()` —
  `sync_sleep_to_supabase()` was already idempotent per date before this
  change (upserts on `date` for `dates`/`sleep_summary`/
  `wake_hybridcharge`, delete-then-reinsert on `date` for
  `sleep_stages`/`heart_rate_daily`), so re-uploading an already-present
  date was never a duplicate-row risk, just wasted verification effort.
- Workflow: run `decode_sleep()` first, compare its printed sleep_start/
  sleep_end against the Zepp app (per the unresolved tz issue above), then
  call `sync_sleep_to_supabase(confirmed_dates, offsets=None, ...)`.
  `confirmed_dates` is an explicit allowlist — any date not listed is never
  uploaded. `offsets` is an optional `{"YYYY-MM-DD": minutes}` dict that
  shifts that date's sleep_start/sleep_end by ± minutes (matches the
  documented "+1h to both boundaries" correction pattern) before avg sleep
  HR and duration are recomputed. There is deliberately no in-script
  natural-language or interactive parsing of corrections — the human
  (Claude Chat + Duarte) agrees on `confirmed_dates`/`offsets` in
  conversation first, then Claude Code is told to run it with those exact
  values.
- `sleep_stages` rows are independent of any `offsets` correction — deleted
  and reinserted fresh per date, with `stage_start`/`stage_stop` = raw value
  − 1440 (per the documented 1440–2880 coordinate trap). Spot-checked
  2026-07-05: the first stage block's start (178 min → 02:58) matched the
  confirmed sleep_start exactly, confirming — consistent with the `data_hr`
  finding above — that the per-minute stage array was never affected by the
  `slp.st`/`slp.ed` bug, only the top-level summary fields were.
- **`sleep_start`/`sleep_end` are stored as naive timestamps (no UTC offset
  attribute)**, not offset-aware ones — a deliberate choice, not an
  oversight. Supabase Studio's table editor always renders `timestamptz`
  columns in UTC with no per-project override. Storing an offset-aware local
  time (e.g. `03:52+01:00`) gets correctly converted and stored as `02:52
  UTC`, which is technically correct but then requires mentally re-adding
  the offset every time the table is browsed — confusing for a personal-use
  single-timezone tool. Instead, the confirmed local wall-clock number
  (already timezone-corrected via `offsets`) is sent with no offset
  attached, so Postgres stores that literal number and Studio's UTC-labeled
  grid displays the exact confirmed digits with no mental conversion needed.
  If this project ever needs to support multiple timezones, this decision
  should be revisited.
- Needed a one-time Supabase-side fix during setup: tables created via the
  SQL editor lacked table-level GRANTs, causing `permission denied for table
  dates` (`42501`) on first upload attempt despite using the `sb_secret_...`
  key. Fixed with `GRANT SELECT, INSERT, UPDATE, DELETE ON public.dates,
  public.sleep_summary, public.sleep_stages, public.heart_rate_daily,
  public.wake_hybridcharge TO service_role;` run once in the SQL editor.
- Dependency: `supabase` (v2.31.0) added to `requirements.txt` (new file —
  none existed before).
- **`heart_rate_daily` restructured 2026-07-13 (one row per minute, not an
  array).** Previously this table stored one row per day with `hr_values`
  as an `int4[]` — the upload compacted that array by dropping
  sentinel/invalid readings (`254`/`255`/`0`), which silently destroyed the
  minute-of-day alignment (element `i` no longer meant minute `i` once
  entries were removed), making it impossible to filter by time of day or
  slice against `sleep_start`/`sleep_end`. Rather than fix alignment within
  the array, the table was restructured: primary key is now
  `(date, minute)`, with columns `minute` (int, 0–1439), `ts` (timestamptz,
  naive local wall-clock, same convention as `sleep_summary.sleep_start`/
  `sleep_end`), and `hr_value` (int, `NULL` for invalid/sentinel readings).
  `sync_sleep_to_supabase` now deletes+reinserts all ~1440 rows for a date
  on each upload (same pattern as `sleep_stages`), rather than upserting a
  single array row. **All 8 previously-uploaded dates were truncated as
  part of this migration** (they only had the old, already-known-stale
  compacted-array data) — everything needs a fresh
  `sync_sleep_to_supabase` run to be repopulated.

## Backlog (GitHub Issues)

- Respiratory rate
- Hypopnea
- Supabase schema design (next agreed step — map full data landscape: sleep,
  workouts, steps, stress, HRV — before writing any tables)
- Daily AI health brief

## Communication & Workflow Preferences

- Plain language first, technical detail second. Casual and direct. Concise over
  thorough.
- Push back firmly when explanations gloss over real-world constraints — don't
  repeat a surface answer when a concern is re-raised.
- Prefers ready-to-run Claude Code prompts over raw Python scripts.
- Update CLAUDE.md collaboratively in Chat after each new feature/discovery,
  before continuing in Claude Code.
