# Methodology

This document is the **source of truth** for what `ptal-gtfs` computes. Code
implements this document; tests verify it. Changes to the method are made here first.

It has two parts: the baseline TfL PTAL method (which the `default` profile must
reproduce exactly), and the Indian adaptations (the `india` profile).

Reference: Transport for London (2015), *Assessing transport connectivity in London*
(PTAL practitioner guide).

---

## 1. The TfL PTAL method

PTAL measures the *density and frequency of public transport service* reachable on
foot from a location. It does **not** measure where you can travel to, how fast, or
whether you can board (no destinations, no crowding).

### 1.1 Points of Interest (POIs)

The study area is covered by a regular grid of points (TfL: 100 m spacing). Each
grid point is a POI for which PTAL is computed independently.

### 1.2 Service Access Points (SAPs)

For each POI, find all stops/stations reachable within the mode's maximum walk
time along the **walking network**:

| Mode (TfL) | Max walk time | Max distance @ 80 m/min |
| --- | --- | --- |
| Bus | 8 min | 640 m |
| Rail / Underground / Tram | 12 min | 960 m |

Walk time:

```
WT = network_walk_distance / walk_speed        (TfL: walk_speed = 80 m/min)
```

### 1.3 Route de-duplication

A route may be reachable at several SAPs. Each **route** (TfL: each route+direction
for bus; each station for rail) is counted **once**, at the SAP giving the highest
EDF (in practice, the shortest total access time).

### 1.4 Waiting time

For each (POI, route) pair, the scheduled frequency `f` (vehicles/hour) is measured
in the AM peak (TfL: 08:15–09:15, average of a normal weekday).

```
SWT = 0.5 × (60 / f)                 # average wait, assuming random arrival
AWT = SWT + K                        # K = reliability factor
```

TfL reliability factors: **K = 2.0 min** for bus, **K = 0.75 min** for rail/tube/tram.

### 1.5 Equivalent Doorstep Frequency (EDF)

```
TAT = WT + AWT                       # total access time, minutes
EDF = 30 / TAT                       # the frequency a service at the doorstep
                                     # would need to give the same access time
```

### 1.6 Accessibility Index (AI)

Within each **mode**, the most attractive route counts fully and the others at half
weight (they are partial substitutes):

```
AI_mode  = EDF_max + 0.5 × Σ EDF_others
AI_total = Σ AI_mode  over modes
```

### 1.7 PTAL bands

| Band | AI range |
| --- | --- |
| 0 | 0 |
| 1a | 0.01 – 2.50 |
| 1b | 2.51 – 5.00 |
| 2 | 5.01 – 10.00 |
| 3 | 10.01 – 15.00 |
| 4 | 15.01 – 20.00 |
| 5 | 20.01 – 25.00 |
| 6a | 25.01 – 40.00 |
| 6b | > 40.00 |

> **Verification note:** all numeric values above must be confirmed against the TfL
> practitioner guide before the golden tests are written (Phase 2). Treat this
> table as the citation target, not yet the verified citation.

---

## 2. Computation from GTFS + OSM

How the abstract method maps onto the input data:

- **Stops & routes** — GTFS `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`.
  Frequency per (route, direction, stop) = departures within the peak window on the
  selected service date (resolved via `calendar.txt`/`calendar_dates.txt`;
  `frequencies.txt` honoured when present).
- **Modes** — GTFS `route_type` mapped to PTAL mode classes (bus, BRT, metro,
  suburban rail, tram, ferry, IPT). The mapping is part of the config profile, since
  Indian feeds use `route_type` inconsistently.
- **Walking network** — OSM pedestrian-permitted ways; network distances from every
  grid point to every candidate stop computed with a high-performance shortest-path
  engine. Crow-fly distance with a detour factor is available
  as a fallback/QA mode, never the default.
- **Grid** — generated from a user boundary, a named place (geocoded), or the GTFS
  convex hull; default spacing 100 m, configurable.

---

## 3. Indian adaptations (the `india` profile)

Each adaptation is a *parameterisation or extension* of the TfL framework — the
formulas in §1 are unchanged unless stated. Final parameter values are tracked as
open decisions D1–D6 in the project plan and will be fixed with citations here.

### 3.1 Intermediate Public Transport (IPT) as a mode

Shared autos, e-rickshaws, vikrams, and private minibuses carry a major share of
trips but have no GTFS. The package accepts an **IPT layer**: stops or corridors
(CSV/GeoJSON) with observed or assumed headways, treated as one or more additional
modes with their own walk threshold and reliability factor. Corridor-type IPT
(hail anywhere along a road) is modelled by sampling virtual stops along the
corridor at a configurable spacing.

### 3.2 Walking environment

- **Walk speed** is profile-configurable globally and per mode (D1).
- **Max access times** are per mode, including new modes (metro, BRT, IPT) (D2).
- Optional **crossing/barrier penalty**: a fixed time penalty per crossing of a
  major road (OSM `primary`/`trunk` and configurable classes) on the walking path,
  reflecting the real cost of crossing in Indian conditions (D6).

### 3.3 Reliability

Headway adherence on Indian bus systems is generally worse than London's, which
raises true average waits above SWT. The reliability factor `K` is per mode in the
profile; where observed headway data (AVL/GPS) is available, an irregularity-based
AWT — `AWT = (E[h]/2) × (1 + CV(h)²) ` — can be enabled instead of `SWT + K` (D3).

### 3.4 Peak window

Configurable per profile/city (D4). Indian peaks are often broader and later than
08:15–09:15; the engine also supports computing PTAL for multiple windows
(AM/PM/off-peak) in one run.

### 3.5 Bands and comparability

The TfL band thresholds are retained by default so results remain comparable with
international PTAL maps, and the raw AI is always exported alongside the band (D5).

### 3.6 Explicitly out of scope (for now)

Crowding/capacity, fares, destination-based accessibility, and safety/comfort
weighting. These matter in India but change the nature of the indicator; they are
candidates for *companion indicators*, not modifications of PTAL.
