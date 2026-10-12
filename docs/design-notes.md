# Design notes for the dashboard (lead-owned)

These are distilled from the `dataviz` and `frontend-design` skills for this one app. Full references:
- `[private home path removed]`
- dataviz skill (read-only copy, may be absent later): `[private home path removed]` (`anti-patterns.md`, `marks-and-anatomy.md`, `interaction.md`)

## Direction

A calm, precise instrument, not a war-room screen. Pure `#000` page with near-black surfaces, and one warm data hue. Typography carries the hierarchy, not boxes. Onest variable for everything, including big numbers (proportional figures). Use `tabular-nums` only in tables and axis ticks.

| Role | Value |
|---|---|
| Page | `#000` |
| Surface (panels) | `#0b0b0c` with a `rgba(255,255,255,0.08)` hairline border |
| Primary ink | `#f0efec` |
| Secondary ink | `#c3c2b7` |
| Muted (axes, captions) | `#898781` |
| Gridline | `#1f1f1d`, solid hairline, never dashed |
| Focus / selection | `#f0efec`, 2px |

## Map encoding

- **Fill = P(alert starts within H)** for the selected horizon, on a **single-hue sequential scale** (warm amber/orange). Low P stays dark and recedes toward the black page; high P is bright. Suggested endpoints: `oklch(0.26 0.05 55)` for 0, `oklch(0.86 0.17 72)` for 1. Interpolate in OKLCH (or Lab), never RGB, and never a rainbow. Show a gradient legend with 0, 0.25, 0.5, 0.75 and 1 ticks, captioned with the horizon ("P(alert starts within 3 h)").
- **Raions with no data** (`raion_id: null` or no forecast row): fill `#111`, hairline `#2c2c2a`, and "No data" in the tooltip. Excluded regions (Crimea, Luhansk) look the same, and the legend says why.
- **An alert really started within H** (replay only): a **white 1.5px outline** on top. The legend item reads "Alert actually started within H".
- **Active now**: a small `#d03b3b` dot at the raion centroid (status: critical), always paired with a text label in the panel ("Alert active since 02:14"). The legend explains the dot, so colour never carries meaning alone.
- **Selected raion**: a 2px `#f0efec` outline, plus a raised paint order.
- Polygon borders: `#000` at 0.5px, so neighbours separate by a surface gap and not by drawn borders.
- Hover: a tooltip with the raion name and oblast, the value first (`0.61`), then the label (`P within 3 h`). Keyboard focus shows the same tooltip. The hit target is the polygon itself.

## Charts

- **Raion history (7 days):** one axis (probability 0–1). A 2px line for P(H) of the selected horizon, with actual alert episodes as translucent bands (`rgba(208,59,59,0.22)`) behind it. Add a crosshair with one tooltip showing P1/P3/P12 at that hour and whether an alert was active. Never use a dual axis.
- **Reliability diagrams (report page):** x = mean predicted, y = observed frequency, both 0–1, with a hairline diagonal reference. One line per model, at most 3 series, using the first three dark categorical slots in this fixed order: B0 `#3987e5`, B1 `#d95926`, M1 `#199e70`. Each model keeps its colour on every chart. Show a legend plus direct labels at line ends. Show markers ≥ 8px only for bins with count > 0.
- **Skill score table:** the numbers are the chart. Use a table with models as rows and horizons as columns (BSS, Brier, log-loss, PR-AUC), with `tabular-nums` and the best value per column in primary ink (the others secondary). A model that lost still appears.
- Every chart has its numbers reachable without hover (a table or labels).

## Layout

- ≥ 1024 px: the map on the left (flexible), a 360px panel on the right (selected raion), and the controls in one row above the map (mode, horizon, then on the replay scrubber row: play/pause, time readout in Kyiv local time with the UTC offset).
- < 1024 px: controls, then the map (full width), then the panel stacked below. At 390 px nothing overflows horizontally, and the scrubber stays usable by touch (44px targets).
- While data refetches during scrubbing, keep the previous render at reduced opacity. No skeleton flash and no layout jump.
