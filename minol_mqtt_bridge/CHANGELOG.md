# Changelog

## 1.2.0

### Breaking

- **`state_class` now defaults to `total_increasing`** (was `total`). Minol
  reports a billing-period-to-date total that drops to 0 at the period rollover:
  `total_increasing` reads that as a meter reset and keeps the accumulated sum,
  while `total` books it as a negative delta and loses a whole period from the
  long-term statistics. Set `state_class: total` explicitly to keep the old
  behaviour. Home Assistant may ask you to fix the statistics once after the
  change.

### Added

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
- A meter row that carries no consumption field is no longer published as `0`.
  It used to be indistinguishable from a meter that genuinely consumed nothing,
  and under `total_increasing` a spurious `0` reads as a meter reset, so the
  recovery back to the real figure is booked as fresh consumption and the
  billing period is counted twice.

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

Hot water changes unit from `kWh` to `m³`. Home Assistant will flag a unit
mismatch against the recorded long-term statistics. Fix it once in
**Developer Tools → Statistics**: find `sensor.minol_hot_water_*` and use
*Fix issue* (or delete the statistics). The underlying numbers do not change —
only the unit they are labelled with.

## 1.1.0

- TLS support for the MQTT connection (`mqtt_tls`, `mqtt_tls_insecure`).
- Configurable `state_class` for the consumption sensors.
- Per-meter units taken from the portal instead of a fixed default.
- Retries for transient network / 5xx / non-JSON responses when fetching data.
