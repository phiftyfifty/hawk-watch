"""Render findings as GitHub-flavoured markdown for the job summary."""


def _escape(text):
    return str(text).replace("|", r"\|")


def render_summary(snapshot, findings):
    host = snapshot.get("host", "unknown host")
    captured = snapshot.get("captured_at", "unknown time")
    lines = [f"## Hawk data status: {host}", "", f"Captured {captured}.", ""]

    if not findings:
        lines.append("All checks healthy. No degradation against the previous snapshot.")
        return "\n".join(lines) + "\n"

    plural = "finding" if len(findings) == 1 else "findings"
    lines.append(f"**{len(findings)} {plural}.**")
    lines.append("")
    lines.append("| Rule | Subject | Detail |")
    lines.append("| --- | --- | --- |")
    for finding in findings:
        lines.append(
            f"| {_escape(finding.rule)} | {_escape(finding.subject)} "
            f"| {_escape(finding.message)} |")
    return "\n".join(lines) + "\n"
