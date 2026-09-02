# Changelog

## 1.2.0

### Breaking

- **Consumption is now measured from a fixed anchor instead of a sliding
  12-month window.** The portal reports consumption for exactly the window
  requested — `anfangsstand` is the meter reading at the window start and
  `consumption` the difference to the end — with no billing-period anchoring of
  any kind. Asking for "the last 12 months" on every run therefore produced a
  rolling sum that *falls* whenever a high-consumption month drops out of the
  window. The window now starts far in the past and the portal clamps it to the
  earliest reading it holds, so the totals only ever grow. **Sensor values step
  up once**, from consumption over the trailing 12 months to consumption since
  the first reading the portal holds for the meter. `scan_interval_hours` is
  unaffected, and the `monthly_data` attribute still covers the last 12 months.
- **`state_class` now defaults to `total_increasing`** (was `total`), which is
  correct for the monotonic totals above and is what the Energy Dashboard wants.
  Set `state_class: total` explicitly to keep the old behaviour. Home Assistant
  may ask you to fix the statistics once after the change.

### Added

- A **dry run** mode (`--dry-run`, `DRY_RUN=true`, or the `dry_run` option) that
  fetches from the portal but opens no MQTT connection, writes every message it
  would have published to a JSON file, and exits after one cycle. Needs no
  broker settings, so a sensor can be investigated against a live account
  without touching the broker. Output path is `dry_run_output`.
- An availability topic (`minol/status`) published with an MQTT Last Will, and
  referenced by every discovered sensor. If the add-on stops or loses the
  broker, the sensors go **unavailable** instead of leaving the last retained
  reading looking current — no template sensors needed to detect staleness.

### Fixed

- **Heating sensors stayed empty on installations with a kWh heat meter.**
  The portal keys heating by metering technology: `HEIZUNG` for heat cost
  allocators, `HZKWH` for heat meters reporting kWh. Only `HEIZUNG` was ever
  requested, and for a heat meter the portal answered `200 OK` with an empty
  table and a chart holding nothing but the DIN reference curve — which looked
  like "no data" rather than "wrong key". Both keys are now tried and the one
  that actually returns meters is used.
- **Hot water was published as `kWh` when the value was really `m³`.** Asking
  the portal for kWh converts the chart and the row's `consumptionBew`, but
  leaves the row's `consumption` in the meter's own unit while relabelling it
  `KWH` — so the sensor's state and its own `monthly_data` were in different
  units. Water is now requested in native m³.
- Responses for an unrecognised consumption key are now discarded. The portal
  does not reject an unknown key; it silently returns *every* category at once,
  which would have folded water meters into the heating total.
- A category total is no longer published when the response is missing a meter
  that earlier cycles reported. The total is a sum over whatever came back, so
  one absent meter lowered it, and `total_increasing` reads that as a meter
  reset and books the rest of the category as fresh consumption. Seen meters are
  remembered in `minol_known_meters.json` (in `/data` for the add-on); delete it
  if a meter is genuinely gone. Per-meter sensors were never affected.
- Months the portal has no reading for yet are published as `null` in
  `monthly_data` instead of `0`, which claimed the meter consumed nothing in a
  month that simply has not been read.
- The total sensors carry `total_period`, `monthly_data_period` and
  `monthly_data_sum`, so it is clear why the state does not equal the sum of the
  monthly breakdown beside it (the state covers everything on record, the
  breakdown only the recent window).
- A meter row that carries no consumption field is no longer published as `0`.
  It used to be indistinguishable from a meter that genuinely consumed nothing,
  and under `total_increasing` a spurious `0` reads as a meter reset, so the
  recovery back to the real figure is booked as fresh consumption and counted
  twice.

### Changed

- The total sensors carry a `cons_type` attribute naming the portal key the
  data came from, to make a missing category diagnosable from the UI.
- Debug HTML dumps (`current_page.html`) are only written at `log_level: DEBUG`
  and no longer abort the sync if the working directory is read-only.
- Dropped the `valuesInKWH=false` retry-on-empty-table. It was added on the
  assumption that heat cost allocators only expose the room table in native
  units; the portal returns byte-identical empty results either way, so it
  never helped, and after the water fix it could have reintroduced the
  mislabelled-unit bug.

### Upgrading

Three things change at once for existing installations, and Home Assistant will
flag each of them against the recorded long-term statistics:

1. **Every total steps up**, from consumption over the trailing 12 months to
   consumption since the first reading the portal holds.
2. **Hot water changes unit** from `kWh` to `m³`. Here the number really is
   unchanged in substance — it was always a volume, just labelled as energy.
3. **`state_class` changes** from `total` to `total_increasing`.

Fix them in one pass: **Developer Tools → Statistics**, find the
`sensor.minol_*` entities and use *Fix issue* (or delete the statistics). Doing
all three together is much less work than meeting them one at a time.

Any Home Assistant template sensors that were filtering zeros or deriving
availability for these entities should no longer be needed.

## 1.1.0

- TLS support for the MQTT connection (`mqtt_tls`, `mqtt_tls_insecure`).
- Configurable `state_class` for the consumption sensors.
- Per-meter units taken from the portal instead of a fixed default.
- Retries for transient network / 5xx / non-JSON responses when fetching data.
