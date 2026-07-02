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
- Traffic interception: Android Studio emulator (Pixel 9a, Android 16, though
  recent sessions show Android 17 in captures — verify emulator image version if
  behavior differs) + HTTP Toolkit
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
- `255` = unscheduled, `254` = failed, values under 200 = BPM
- Coordinate system trap: sleep `start`/`stop` values are minutes from the
  *previous* day's midnight (1440–2880 range) — subtract 1440 to align with the
  blob's 0–1440 index
- Average sleep HR (excl. awake periods) implemented and confirmed within ~1–2 BPM
  of the Zepp app

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
- Next step: isolate by clearing HTTP Toolkit log, tapping directly into the
  "HEART RATE VARIABILITY (ms)" detail screen (the ">" arrow), and inspecting
  fresh traffic. May use a dedicated endpoint outside the generic `events` path,
  similar to how `heartRate` and `band_data.json` are special-cased for HR.

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
- Respiratory rate, hypopnea, sleep regularity % — not present in any community
  project. Requires targeted emulator capture, deprioritized until Supabase schema
  work is underway.

## Known Traps / Lessons Learned

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

## Backlog (GitHub Issues)

- Respiratory rate
- Hypopnea
- Sleep regularity %
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
