"""Gate 3 tool: Executive aesthetic, visual density, typography, and tone audit."""

from __future__ import annotations

import re
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from pursuit_auditor.config.settings import get_settings
from pursuit_auditor.observability.logging import get_logger
from pursuit_auditor.observability.tracing import traced_span
from pursuit_auditor.schemas import (
    AestheticDensityAuditRequest,
    AestheticDensityReport,
    GateFinding,
    ToolErrorResponse,
)

logger = get_logger(__name__)

# Banned 4-color rainbow, pastel card fills, and off-palette navy colors
_BANNED_HEX_COLORS: dict[str, str] = {
    "#4285F4": "4-color rainbow blue (use single accent #1A73E8)",
    "#EA4335": "4-color rainbow red",
    "#FBBC05": "4-color rainbow yellow",
    "#34A853": "4-color rainbow green",
    "#E8F0FE": "tinted pastel blue card fill",
    "#F4F8FE": "tinted pastel blue card fill",
    "#E6F4EA": "tinted pastel green card fill",
    "#FEF7E0": "tinted pastel yellow card fill",
    "#FCE8E6": "tinted pastel red card fill",
    "#F3E8FD": "tinted pastel purple card fill",
    "#051C2C": "legacy dark navy",
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]",
    flags=re.UNICODE,
)

_GENERIC_ADVISORY_RE = re.compile(
    r"\b(?:general\s+advisory|advisory\s+support\s+only|ongoing\s+guidance)\b",
    re.IGNORECASE,
)


def audit_executive_aesthetic_and_density(
    deliverable_type: str,
    slides: list[dict[str, Any]],
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run Gate 3 audit for executive aesthetic discipline, density, typography, and tone.

    Audits every slide or section against monochrome executive design invariants:
    1. Palette: Monochrome Graphite + single focal accent `#1A73E8` (blocks
       4-color rainbow bars, pastel card grids, and legacy navy).
    2. Geometry: Sharp 0px corners (flags rounded cards/pills and horizontal
       divider lines under Action Titles).
    3. Typography & Kickers: Enforces `10-24 pt` font scale for non-footnote
       elements and all-or-nothing top-left kicker consistency across slides.
    4. Density & Zero-Emoji Discipline: Flags table cells > 15 words (wall of
       text), emojis in executive surfaces, and generic advisory filler.

    Args:
        deliverable_type: Deliverable category (e.g., `"services_strategy_deck"`).
        slides: List of per-slide visual metadata dictionaries matching
            `SlideVisualMetadata` (`slide_number`, `title`, `has_top_left_kicker`,
            `has_title_divider_line`, `uses_rounded_corners`, `hex_colors_used`,
            `font_sizes_pt`, `table_cells`, `body_text`).

    Returns:
        A JSON-serializable `AestheticDensityReport` dictionary (`passed`,
        `slides_audited`, `blocking_count`, `polish_count`, `findings`), or
        `ToolErrorResponse` with recovery instructions if schema validation fails.
    """
    with traced_span(
        "audit.tool.audit_executive_aesthetic_and_density",
        {"deliverable.type": deliverable_type, "slide.count": len(slides) if isinstance(slides, list) else 0},
    ):
        try:
            req = AestheticDensityAuditRequest.model_validate(
                {"deliverable_type": deliverable_type, "slides": slides}
            )
        except ValidationError as exc:
            return ToolErrorResponse(
                error_code="AESTHETIC_AUDIT_SCHEMA_ERROR",
                recoverable=True,
                message=f"Aesthetic audit input failed validation ({len(exc.errors())} issue(s)).",
                missing_fields=[".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()],
                recovery_instructions=[
                    "Supply a non-empty `slides` list where each item matches `SlideVisualMetadata`.",
                    "Ensure `slide_number >= 1` and `font_sizes_pt` contains numeric point sizes.",
                ],
                retry_example={
                    "deliverable_type": "services_strategy_deck",
                    "slides": [
                        {
                            "slide_number": 1,
                            "title": "Control Tower accelerates 18-month migration",
                            "has_top_left_kicker": True,
                            "has_title_divider_line": False,
                            "uses_rounded_corners": False,
                            "hex_colors_used": ["#FFFFFF", "#202124", "#1A73E8"],
                            "font_sizes_pt": [10.0, 11.0, 18.0],
                            "table_cells": ["Trained AI migration patterns for 15 target workloads"],
                            "body_text": "",
                        }
                    ],
                },
            ).model_dump()

        settings = get_settings()
        findings: list[GateFinding] = []
        seq = 1

        def add_finding(location: str, issue: str, exact_fix: str, severity: str = "BLOCKING") -> None:
            nonlocal seq
            findings.append(
                GateFinding(
                    finding_id=f"G3-{seq:02d}",
                    location=location,
                    gate="Gate 3",
                    severity="BLOCKING" if severity == "BLOCKING" else "POLISH",
                    issue=issue,
                    exact_fix=exact_fix,
                )
            )
            seq += 1

        # All-or-nothing kicker consistency across multi-slide decks
        kicker_flags = [s.has_top_left_kicker for s in req.slides]
        if len(req.slides) > 1 and any(kicker_flags) and not all(kicker_flags):
            missing_slides = [str(s.slide_number) for s in req.slides if not s.has_top_left_kicker]
            add_finding(
                location=f"Slides {', '.join(missing_slides)}",
                issue="Top-left uppercase kickers are inconsistent across slides (violates All-or-Nothing rule).",
                exact_fix="Add matching 10pt borderless uppercase graphite kickers to every content slide (or remove from all).",
            )

        for slide in req.slides:
            loc = f"Slide {slide.slide_number} ({slide.title[:36]})"

            # 1. Banned colors check
            for raw_hex in slide.hex_colors_used:
                normalized = raw_hex.strip().upper()
                if normalized in _BANNED_HEX_COLORS:
                    add_finding(
                        location=loc,
                        issue=f"Banned color {normalized} detected ({_BANNED_HEX_COLORS[normalized]}).",
                        exact_fix="Replace with pure white #FFFFFF canvas, graphite #202124/#5F6368 text, and single #1A73E8 focal accent.",
                    )

            # 2. Sharp geometry & no title divider line
            if slide.uses_rounded_corners:
                add_finding(
                    location=loc,
                    issue="Rounded cards/pills detected; executive standard requires sharp 0px rectangles.",
                    exact_fix="Convert all rounded shapes/cards to 0px sharp rectangles or open hairline grids.",
                )

            if slide.has_title_divider_line:
                add_finding(
                    location=loc,
                    issue="Redundant horizontal divider line below Action Title.",
                    exact_fix="Delete the rule below the Action Title and rely on whitespace above exhibit top hairlines.",
                    severity="POLISH",
                )

            # 3. Font scale discipline (10-24 pt)
            out_of_range = [
                pt
                for pt in slide.font_sizes_pt
                if pt < settings.min_slide_font_pt or pt > settings.max_slide_font_pt
            ]
            if out_of_range:
                add_finding(
                    location=loc,
                    issue=(
                        f"Non-footnote font sizes {out_of_range} pt fall outside locked "
                        f"{settings.min_slide_font_pt:g}-{settings.max_slide_font_pt:g} pt scale."
                    ),
                    exact_fix=(
                        "Tighten copy instead of shrinking text; lock body/tables to 10-12 pt "
                        "and titles/headers to 14-24 pt."
                    ),
                )

            # 4. Table cell word-count ceiling (<= 15 words)
            for idx, cell in enumerate(slide.table_cells, start=1):
                word_count = len(cell.split())
                if word_count > settings.max_table_cell_words:
                    add_finding(
                        location=f"{loc} — Table Cell #{idx}",
                        issue=(
                            f"Table cell has {word_count} words (exceeds {settings.max_table_cell_words}-word "
                            "1-2 line wall-of-text ceiling)."
                        ),
                        exact_fix=(
                            f"Compress cell #{idx} to <= {settings.max_table_cell_words} words starting "
                            "with a bold deliverable lead-in phrase."
                        ),
                    )

            # 5. Emoji check
            combined_text = " ".join([slide.title, slide.body_text, *slide.table_cells])
            if _EMOJI_RE.search(combined_text):
                add_finding(
                    location=loc,
                    issue="Emoji characters detected in executive deliverable surface.",
                    exact_fix="Remove all emojis and replace status markers with crisp text or 6px square graphite/ink indicators.",
                )

            # 6. Generic advisory wording polish check
            if _GENERIC_ADVISORY_RE.search(combined_text):
                add_finding(
                    location=loc,
                    issue="Generic 'advisory' phrasing weakens outcome clarity.",
                    exact_fix="Replace generic advisory labels with concrete deliverables (e.g., golden GCP patterns, parity QA gates).",
                    severity="POLISH",
                )

        blocking_count = sum(1 for f in findings if f.severity == "BLOCKING")
        polish_count = sum(1 for f in findings if f.severity == "POLISH")
        report = AestheticDensityReport(
            passed=blocking_count == 0,
            slides_audited=len(req.slides),
            blocking_count=blocking_count,
            polish_count=polish_count,
            findings=findings,
        )

        if tool_context is not None:
            tool_context.state["gate3_blocking_count"] = blocking_count

        logger.info(
            "gate3_aesthetic_audit_completed",
            slides_audited=len(req.slides),
            passed=report.passed,
            blocking_count=blocking_count,
            polish_count=polish_count,
        )
        return report.model_dump()
