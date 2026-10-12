"""Print report tables from frozen metrics without training or scoring any model."""

from airalert.config import METRICS_PATH
from airalert.schemas import Report


def render_tables(report: Report) -> str:
    """Keep every model and row set; use stored BSS against the matching B0 rows."""
    sections: list[str] = []
    for split, title in (("val", "Validation"), ("test", "Test")):
        row_sets = sorted({score.row_set for score in report.onset if score.split == split})
        for row_set in row_sets:
            scores = sorted(
                (s for s in report.onset if s.split == split and s.row_set == row_set),
                key=lambda s: (s.model, s.horizon),
            )
            suffix = "" if row_set == "full" else f" / row set `{row_set}`"
            lines = [
                f"### {title} onset{suffix}",
                "",
                "| Model | H, hours | n | Brier | BSS vs B0 | Log-loss | PR-AUC |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            for score in scores:
                bss = "n/a" if score.bss is None else f"{score.bss:.6f}"
                lines.append(
                    f"| {score.model} | {score.horizon} | {score.n:,} | {score.brier:.6f} "
                    f"| {bss} | {score.logloss:.6f} | {score.pr_auc:.6f} |"
                )
            sections.append("\n".join(lines))

    if report.duration:
        lines = [
            "### Duration",
            "",
            "| Model | Split | n | Pinball P10, min | Pinball P50, min | Pinball P90, min "
            "| P50 mean absolute error, min | P10-P90 coverage |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for split in ("val", "test"):
            scores = sorted(
                (s for s in report.duration if s.split == split), key=lambda s: s.model
            )
            for score in scores:
                lines.append(
                    f"| {score.model} | {split} | {score.n:,} | {score.pinball_q10:.6f} "
                    f"| {score.pinball_q50:.6f} | {score.pinball_q90:.6f} "
                    f"| {score.mae_q50_min:.6f} | {score.coverage_80:.2%} |"
                )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def main() -> None:
    """Validate the entire metrics contract before emitting Markdown to stdout."""
    report = Report.model_validate_json(METRICS_PATH.read_text(encoding="utf-8"))
    print(render_tables(report))


if __name__ == "__main__":
    main()
