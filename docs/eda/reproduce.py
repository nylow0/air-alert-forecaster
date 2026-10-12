"""Recreate A1's descriptive figures and print the facts used in docs/eda.md.

Run after `uv run airalert episodes` with `uv run python docs/eda/reproduce.py`.
This computes descriptive base rates, not model scores or split assignments.
"""

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import dates as mdates  # noqa: E402

from airalert.config import HORIZONS, KYIV_TZ, TRAIN_START  # noqa: E402
from airalert.data import (  # noqa: E402
    Snapshot,
    daily_level_counts,
    detect_regime_switch,
    load_episodes,
    load_raions,
    load_records,
    load_snapshot,
    load_source,
)


def _save(figure: plt.Figure, name: str) -> None:
    """Export black-background figures next to this reproduction script."""
    figure.savefig(Path(__file__).parent / name, dpi=160, facecolor="#000000")
    plt.close(figure)


def onset_rates(
    episodes: pd.DataFrame, raions: pd.DataFrame, snapshot: Snapshot
) -> dict[str, dict[str, dict[str, float | int]]]:
    """Count starts in (t,t+H] for every universe raion and complete hourly window."""
    hours = pd.date_range(TRAIN_START, snapshot.snapshot, freq="h").as_unit("us")
    times = hours.asi8
    old = hours < snapshot.regime_switch
    totals: dict[str, dict[str, dict[str, float | int]]] = {}
    groups = {key: group.start.array.asi8 for key, group in episodes.groupby("raion_id")}
    for horizon in HORIZONS:
        delta = horizon * 60 * 60 * 1_000_000
        complete = hours + pd.Timedelta(hours=horizon) <= snapshot.snapshot
        labels = np.zeros(len(hours), dtype=np.int64)
        for raion in raions.raion_id:
            starts = groups.get(raion, np.array([], dtype=np.int64))
            labels += (
                np.searchsorted(starts, times + delta, side="right")
                > np.searchsorted(starts, times, side="right")
            )
        rates: dict[str, dict[str, float | int]] = {}
        for regime, mask in [("oblast", old & complete), ("raion", ~old & complete)]:
            denominator = int(mask.sum()) * len(raions)
            positives = int(labels[mask].sum())
            rates[regime] = {
                "positive_rows": positives,
                "forecast_rows": denominator,
                "rate": positives / denominator,
            }
        totals[str(horizon)] = rates
    return totals


def coverage_quality(records: pd.DataFrame, episodes: pd.DataFrame) -> dict[str, int]:
    """Count mixed-level unions and actual cross-level overlaps without row loops."""
    membership = np.empty(len(records), dtype=np.int64)
    overlapping = np.zeros(len(records), dtype=bool)
    episode_groups = {key: group for key, group in episodes.groupby("raion_id")}
    for raion, group in records.groupby("raion_id", sort=False):
        targets = episode_groups[raion]
        starts = group.started_at.array.asi8
        ends = group.finished_at.array.asi8
        local = np.searchsorted(targets.start.array.asi8, starts, side="right") - 1
        membership[group.index] = targets.index.to_numpy()[local]
        levels = group.level.to_numpy()
        active = np.zeros(len(group), dtype=bool)
        for level in ["oblast", "raion", "hromada"]:
            running = np.maximum.accumulate(
                np.where(levels == level, ends, np.iinfo(np.int64).min)
            )
            previous = np.roll(running, 1)
            previous[0] = np.iinfo(np.int64).min
            active |= (levels != level) & (starts < previous) & (ends > starts)
        overlapping[group.index] = active
    mixed = records.assign(episode=membership).groupby("episode")["level"].nunique().gt(1)
    return {
        "episodes_with_multiple_levels": int(mixed.sum()),
        "records_starting_during_other_level_coverage": int(overlapping.sum()),
        "episodes_with_cross_level_overlap": int(np.unique(membership[overlapping]).size),
    }


def figures(source: pd.DataFrame, episodes: pd.DataFrame, snapshot: Snapshot) -> None:
    """Plot raw starts, local hours, level shares, durations and raion counts."""
    plt.rcParams.update({
        "figure.facecolor": "#000000", "axes.facecolor": "#000000",
        "savefig.facecolor": "#000000", "text.color": "#eeeeee",
        "axes.labelcolor": "#dddddd", "xtick.color": "#bbbbbb",
        "ytick.color": "#bbbbbb", "axes.edgecolor": "#444444",
        "grid.color": "#333333", "font.size": 10,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    switch = snapshot.regime_switch
    daily = daily_level_counts(source)
    episode_days = episodes.groupby(episodes.start.dt.floor("D")).size().reindex(
        daily.index, fill_value=0
    )
    fig, ax = plt.subplots(figsize=(12, 4.5), layout="constrained")
    ax.plot(daily.index, episode_days, color="#70d8c1", lw=.6, alpha=.8,
            label="Merged raion episode starts")
    ax.plot(daily.index, daily.sum(axis=1), color="#b4a0ff", lw=.6, alpha=.75,
            label="Source record starts, duplicates retained")
    ax.axvline(switch, color="#ffffff", ls="--", lw=1,
               label=f"Raion majority rule: {switch.date()}")
    empty_days = daily.index[daily.sum(axis=1).eq(0)]
    for index, day in enumerate(empty_days):
        ax.axvspan(day, day + pd.Timedelta(days=1), color="#eead61", alpha=.4,
                   label="Days with no source starts" if index == 0 else None)
    ax.set(title="Alert starts per UTC day", ylabel="Starts", xlabel="UTC date")
    ax.grid(axis="y", alpha=.5)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    _save(fig, "daily-starts.png")

    fig, ax = plt.subplots(figsize=(12, 4.5), layout="constrained")
    local_hours = episodes.start.dt.tz_convert(KYIV_TZ).dt.hour
    for name, mask, color, shift in [
        ("Oblast regime", episodes.start.lt(switch), "#b4a0ff", -.2),
        ("Raion regime", episodes.start.ge(switch), "#70d8c1", .2),
    ]:
        profile = local_hours[mask].value_counts().reindex(range(24), fill_value=0)
        ax.bar(np.arange(24) + shift, profile / profile.sum() * 100, width=.4,
               color=color, label=name)
    ax.set(title="Episode start hour in Europe/Kyiv", xlabel="Local hour",
           ylabel="Share of starts, %")
    ax.set_xticks(range(24))
    ax.grid(axis="y", alpha=.5)
    ax.legend(frameon=False)
    _save(fig, "kyiv-hours.png")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), layout="constrained")
    monthly = daily.resample("MS").sum()
    share = monthly.div(monthly.sum(axis=1), axis=0)
    colors = ["#b4a0ff", "#70d8c1", "#547387"]
    axes[0].stackplot(share.index, *[share[level] * 100 for level in daily.columns],
                      labels=daily.columns, colors=colors, step="post")
    axes[0].axvline(switch, color="#ffffff", ls="--", lw=1)
    axes[0].set(title="Source level mix by UTC month", ylabel="Source rows, %", ylim=(0, 100))
    axes[0].legend(loc="lower left", ncol=3, frameon=False)
    axes[0].text(.99, .97, f"Switch {switch.date()}\nMarch 2022 / September 2026 are partial",
                 transform=axes[0].transAxes, ha="right", va="top", fontsize=9)
    rolling = daily.rolling(14, min_periods=14).sum()
    for level, color in zip(daily.columns, colors, strict=True):
        axes[1].plot(rolling.index, rolling[level] / rolling.sum(axis=1) * 100,
                     color=color, label=level)
    axes[1].axhline(50, color="#666666", ls=":")
    axes[1].axvline(switch, color="#ffffff", ls="--", lw=1)
    axes[1].set(title="Trailing 14-day shares confirm sustained raion majority",
                ylabel="Source rows, %", xlabel="UTC date", ylim=(0, 100),
                xlim=(pd.Timestamp("2025-07-01", tz="UTC"), pd.Timestamp("2025-12-15", tz="UTC")))
    axes[1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axes[1].legend(ncol=3, frameon=False)
    _save(fig, "level-mix.png")

    minutes = (episodes.end - episodes.start).dt.total_seconds() / 60
    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    for y, label, mask, color in [
        (1, "Oblast regime", episodes.start.lt(switch), "#b4a0ff"),
        (0, "Raion regime", episodes.start.ge(switch), "#70d8c1"),
    ]:
        quantiles = minutes[mask & ~episodes.censored].quantile([.1, .5, .9]).to_numpy()
        low, median, high = quantiles
        ax.plot([low, high], [y, y], color=color, lw=3)
        ax.scatter(quantiles, [y] * 3, color=color, s=[40, 90, 40], zorder=3)
        for value, caption in zip(quantiles, ["P10", "Median", "P90"], strict=True):
            ax.annotate(f"{caption}\n{value:.1f} min", (value, y), xytext=(0, 12),
                        textcoords="offset points", ha="center", fontsize=10)
        ax.text(1, y, label, ha="left", va="center", color=color)
    ax.set(title="Merged episode durations by start regime", xlabel="Minutes, logarithmic scale",
           xscale="log", xlim=(1, max(minutes.quantile(.99), 1000)), ylim=(-.5, 1.65), yticks=[])
    ax.grid(axis="x", alpha=.5)
    _save(fig, "duration-regimes.png")

    raions = load_raions()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), layout="constrained")
    for ax, label, subset, color in [
        (axes[0], "Whole CSV", episodes, "#b4a0ff"),
        (axes[1], "Raion regime", episodes.loc[episodes.start.ge(switch)], "#70d8c1"),
    ]:
        counts = subset.raion_id.value_counts().reindex(raions.raion_id, fill_value=0)
        ax.hist(counts, bins=18, color=color, edgecolor="#000000")
        ax.set(title=f"Episodes per raion: {label}", xlabel="Episode count", ylabel="Raions")
        caption = f"Min {counts.min():,}\nMedian {counts.median():,.0f}\nMax {counts.max():,}"
        ax.text(.98, .96, caption,
                transform=ax.transAxes, ha="right", va="top")
        ax.grid(axis="y", alpha=.5)
    _save(fig, "episodes-per-raion.png")


def main() -> None:
    source, records = load_source(), load_records()
    episodes, raions, snapshot = load_episodes(), load_raions(), load_snapshot()
    switch = snapshot.regime_switch
    minutes = (episodes.end - episodes.start).dt.total_seconds() / 60
    summary: dict[str, object] = {
        "regime_switch": str(switch.date()), "snapshot": snapshot.snapshot.isoformat(),
        "source_rows": len(source), "universe": len(raions), "records": len(records),
        "episodes": len(episodes), "source_duplicates": int(source.duplicated().sum()),
        "unique_source_rows": len(source.drop_duplicates()),
        "expanded_duplicates": int(records.duplicated().sum()),
        "zero_length_source_rows": int(source.finished_at.eq(source.started_at).sum()),
        "negative_source_rows": int(source.finished_at.lt(source.started_at).sum()),
        "zero_length_episodes": int(minutes.eq(0).sum()),
        "episodes_under_10min": int(minutes.lt(10).sum()),
        "episodes_under_10min_share": float(minutes.lt(10).mean()),
        "censored": int(episodes.censored.sum()),
        "cross_level": coverage_quality(records, episodes),
        "onset_rates": onset_rates(episodes, raions, snapshot),
    }
    regimes: dict[str, object] = {}
    for label, mask in [
        ("oblast", episodes.start.lt(switch)), ("raion", episodes.start.ge(switch)),
    ]:
        counts = episodes.loc[mask].raion_id.value_counts().reindex(raions.raion_id, fill_value=0)
        regimes[label] = {
            "episodes": int(mask.sum()),
            "duration_minutes": minutes[mask].quantile([.1, .5, .9]).to_dict(),
            "episodes_per_raion_min_median_max": [
                int(counts.min()), float(counts.median()), int(counts.max()),
            ],
            "zero_episode_raions": counts.index[counts.eq(0)].tolist(),
            "min_raions": counts.index[counts.eq(counts.min())].tolist(),
            "max_raions": counts.index[counts.eq(counts.max())].tolist(),
            "under_10min_share": float(minutes[mask].lt(10).mean()),
        }
    summary["regimes"] = regimes
    counts = episodes.raion_id.value_counts().reindex(raions.raion_id, fill_value=0)
    summary["overall_episodes_per_raion_min_median_max"] = [
        int(counts.min()), float(counts.median()), int(counts.max()),
    ]
    daily = daily_level_counts(source)
    summary["days_without_source_starts"] = daily.index[
        daily.sum(axis=1).eq(0)
    ].strftime("%Y-%m-%d").tolist()
    rolling = daily.rolling(14, min_periods=14).sum()
    shares = rolling.div(rolling.sum(axis=1), axis=0)
    evidence = shares.loc[switch - pd.Timedelta(days=1):switch].copy()
    evidence.index = evidence.index.astype(str)
    summary["switch_evidence"] = evidence.to_dict("index")
    summary["minimum_post_switch_14_day_raion_share"] = float(shares.loc[switch:].raion.min())
    low_oblast = shares.oblast.lt(.5)
    sustained_low = low_oblast.iloc[::-1].cummin().iloc[::-1]
    summary["sustained_oblast_below_half_date"] = str(sustained_low.index[sustained_low][0].date())
    no_duplicates = source.drop_duplicates()
    summary["deduplicated_switch"] = str(detect_regime_switch(no_duplicates).date())
    print(json.dumps(summary, default=str, indent=2))
    figures(source, episodes, snapshot)
    print("Saved five figures in docs/eda/")


if __name__ == "__main__":
    main()
