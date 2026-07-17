"""
engines/watermark_engine.py — Watermark detection and removal engine.

Detection strategies:
  1. PDF-tagged artifact watermarks — pages whose content stream contains
     an /Artifact <</Subtype /Watermark>> BDC/EMC block, or documents that
     have an Optional Content Group (OCG) named "Watermark".  This is the
     most reliable signal and catches publisher watermarks like the NCERT
     diagonal copyright stamp.
  2. Full-page background images — images that cover ≥ 90 % of the page
     (or overflow the page rect), detected per-page without requiring
     cross-page repetition.  Repeating large images (appearing on ≥ 60 %
     of pages with ≥ 50 % coverage) are also detected.
  3. Text watermarks — large text spans that are light-grey, low-opacity,
     or rotated (diagonal), including centred stamp-style watermarks.
     Detection uses rawdict to capture transformation matrices and catches
     both horizontal and diagonal/rotated text.
  4. Vector watermarks — large filled paths (≥ 40 % of page area) that
     repeat across pages.

Removal is content-safe: uses fill=None redaction so only the target
element is removed; surrounding text and graphics are not whited out.
Artifact watermarks are removed by stripping the BDC/EMC block from the
page content stream.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from models.watermark_result import WatermarkResult, WatermarkType

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# An image is a watermark candidate if it covers this fraction of the page
_IMAGE_WM_MIN_COVERAGE = 0.50
# A single-page full-bleed image at this coverage is treated as a watermark
# without needing cross-page repetition (e.g. NCERT bleed-edge watermarks)
_IMAGE_FULLPAGE_COVERAGE = 0.90
# An image repeating on this fraction of pages is almost certainly a watermark
_IMAGE_REPEAT_THRESHOLD = 0.60
# Minimum font size for a text span to be a watermark candidate
_TEXT_WM_MIN_SIZE = 20.0
# Minimum brightness (0–1) for light-grey watermark text
_TEXT_WM_MIN_BRIGHTNESS = 0.70
# A vector path covering this fraction of the page is a watermark candidate
_VECTOR_AREA_FRACTION = 0.40
# Confidence values
_CONF_ARTIFACT = 0.98
_CONF_TEXT = 0.85
_CONF_TEXT_DIAGONAL = 0.90   # rotated/diagonal text is almost certainly a watermark
_CONF_IMAGE = 0.90
_CONF_VECTOR = 0.75

# Direction vector dot-product threshold to call a span "diagonal".
# A purely horizontal span has dir=(1,0); vertical=(0,1).
# If |dir.x| < this threshold the text is noticeably rotated.
_DIAGONAL_THRESHOLD = 0.85

# Regex matching an /Artifact Watermark BDC…EMC block in a content stream
_WM_ARTIFACT_RE = re.compile(
    r'/Artifact\s*<<[^>]*/Watermark[^>]*>>\s*BDC\s.*?EMC\s*',
    re.DOTALL,
)


# ── Detection ─────────────────────────────────────────────────────────────────

def detect_watermarks(
    path: Path,
    password: str = "",
    max_pages: Optional[int] = None,
) -> list[WatermarkResult]:
    """Scan a PDF for watermarks.

    Detects repeating background images, large light-grey text, and
    large-coverage vector paths.

    Args:
        path: Absolute path to the PDF.
        password: Password for encrypted PDFs.
        max_pages: If given, only scan this many pages.

    Returns:
        List of WatermarkResult objects. Empty on error or no findings.
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("detect_watermarks: cannot open %s — %s", path.name, exc)
        return []

    results: list[WatermarkResult] = []

    try:
        if doc.is_encrypted and not doc.authenticate(password):
            log.warning("detect_watermarks: wrong password for %s", path.name)
            return []

        scan_limit = min(max_pages, doc.page_count) if max_pages else doc.page_count

        # ── Pass 0: PDF-tagged artifact watermarks ────────────────────────
        # Strategy A: Optional Content Groups named "Watermark"
        ocg_watermark = _find_watermark_ocgs(doc)
        if ocg_watermark:
            for pno in range(scan_limit):
                page = doc[pno]
                hrect = _artifact_highlight_rect(doc, page)
                results.append(WatermarkResult(
                    source_path=path,
                    page_index=pno,
                    watermark_type=WatermarkType.ARTIFACT,
                    confidence=_CONF_ARTIFACT,
                    description=(
                        f"PDF watermark layer on page {pno + 1} "
                        f"(OCG: {', '.join(ocg_watermark)})"
                    ),
                    highlight_rect=hrect,
                    removable=True,
                ))
            log.info(
                "detect_watermarks: found watermark OCG(s) %s in %s",
                ocg_watermark, path.name,
            )

        # Strategy B: /Artifact <</Subtype /Watermark>> BDC/EMC in content stream
        artifact_pages: set[int] = {r.page_index for r in results
                                     if r.watermark_type == WatermarkType.ARTIFACT}
        for pno in range(scan_limit):
            if pno in artifact_pages:
                continue
            page = doc[pno]
            if _page_has_artifact_watermark(doc, page):
                hrect = _artifact_highlight_rect(doc, page)
                results.append(WatermarkResult(
                    source_path=path,
                    page_index=pno,
                    watermark_type=WatermarkType.ARTIFACT,
                    confidence=_CONF_ARTIFACT,
                    description=(
                        f"PDF-tagged watermark on page {pno + 1} "
                        f"(/Artifact Watermark content stream marker)"
                    ),
                    highlight_rect=hrect,
                    removable=True,
                ))
                artifact_pages.add(pno)

        # ── Pass 1: image watermarks ──────────────────────────────────────
        # Sub-pass 1a: full-page / bleed-edge images (no repeat required)
        reported_image_pages: set[int] = set()
        for pno in range(scan_limit):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            if page_area == 0:
                continue
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                for rect in page.get_image_rects(xref):
                    cov = (rect.width * rect.height) / page_area
                    if cov >= _IMAGE_FULLPAGE_COVERAGE:
                        results.append(WatermarkResult(
                            source_path=path,
                            page_index=pno,
                            watermark_type=WatermarkType.IMAGE,
                            confidence=_CONF_IMAGE,
                            description=(
                                f"Full-page background image watermark on page {pno + 1} "
                                f"(covers {cov:.0%})"
                            ),
                            highlight_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                            removable=True,
                        ))
                        reported_image_pages.add(pno)
                        break
                if pno in reported_image_pages:
                    break

        # Sub-pass 1b: collect per-page image xrefs for repeat detection
        xref_page_count: dict[int, int] = {}
        xref_coverage: dict[int, float] = {}

        for pno in range(scan_limit):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            if page_area == 0:
                continue
            seen_xrefs: set[int] = set()
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                rects = list(page.get_image_rects(xref))
                for rect in rects:
                    cov = (rect.width * rect.height) / page_area
                    xref_page_count[xref] = xref_page_count.get(xref, 0) + 1
                    xref_coverage[xref] = max(xref_coverage.get(xref, 0.0), cov)

        repeat_min_pages = max(1, int(scan_limit * _IMAGE_REPEAT_THRESHOLD))
        repeating_xrefs: set[int] = {
            xref
            for xref, count in xref_page_count.items()
            if count >= repeat_min_pages
            and xref_coverage.get(xref, 0) >= _IMAGE_WM_MIN_COVERAGE
        }

        # Sub-pass 1c: emit results for repeating images
        for pno in range(scan_limit):
            if pno in reported_image_pages:
                continue
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            if page_area == 0:
                continue
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in repeating_xrefs:
                    for rect in page.get_image_rects(xref):
                        cov = (rect.width * rect.height) / page_area
                        if cov >= _IMAGE_WM_MIN_COVERAGE:
                            results.append(WatermarkResult(
                                source_path=path,
                                page_index=pno,
                                watermark_type=WatermarkType.IMAGE,
                                confidence=_CONF_IMAGE,
                                description=(
                                    f"Background image watermark on page {pno + 1} "
                                    f"(repeats on {xref_page_count[xref]} pages, "
                                    f"covers {cov:.0%})"
                                ),
                                highlight_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                                removable=True,
                            ))
                            reported_image_pages.add(pno)
                            break
                if pno in reported_image_pages:
                    break

        # ── Pass 2: text and vector watermarks ───────────────────────────
        reported_text_pages: set[int] = set()
        reported_vector_pages: set[int] = set()

        for pno in range(scan_limit):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            if page_area == 0:
                continue
            if pno not in reported_text_pages:
                wm_spans = _find_text_wm_spans(page)
                if wm_spans:
                    titles = list(dict.fromkeys(s["text"] for s in wm_spans))
                    has_diagonal = any(s.get("diagonal") for s in wm_spans)
                    conf = _CONF_TEXT_DIAGONAL if has_diagonal else _CONF_TEXT
                    diag_note = " (diagonal/rotated)" if has_diagonal else ""
                    # Union of all span bboxes as the highlight region
                    all_bboxes = [s["bbox"] for s in wm_spans]
                    hx0 = min(b[0] for b in all_bboxes)
                    hy0 = min(b[1] for b in all_bboxes)
                    hx1 = max(b[2] for b in all_bboxes)
                    hy1 = max(b[3] for b in all_bboxes)
                    results.append(WatermarkResult(
                        source_path=path,
                        page_index=pno,
                        watermark_type=WatermarkType.TEXT,
                        confidence=conf,
                        description=(
                            f"Text watermark{diag_note} on page {pno + 1}: "
                            + ", ".join(repr(t) for t in titles[:3])
                        ),
                        highlight_rect=(hx0, hy0, hx1, hy1),
                        removable=True,
                    ))
                    reported_text_pages.add(pno)

            # Large-area vector paths
            if pno not in reported_vector_pages:
                for path_info in page.get_drawings():
                    rect = path_info.get("rect")
                    if rect is None:
                        continue
                    cov = (rect.width * rect.height) / page_area
                    if cov >= _VECTOR_AREA_FRACTION:
                        results.append(WatermarkResult(
                            source_path=path,
                            page_index=pno,
                            watermark_type=WatermarkType.VECTOR,
                            confidence=_CONF_VECTOR,
                            description=(
                                f"Vector watermark on page {pno + 1} "
                                f"(path covers {cov:.0%})"
                            ),
                            highlight_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                            removable=True,
                        ))
                        reported_vector_pages.add(pno)
                        break

    finally:
        doc.close()

    log.info("detect_watermarks: %d finding(s) in %s", len(results), path.name)
    return results


def detect_watermarks_quick(
    path: Path,
    password: str = "",
    max_pages: Optional[int] = None,
) -> list[WatermarkResult]:
    """Fast first-pass scan: only OCG layer names and /Artifact Watermark tags.

    Returns in milliseconds on most documents.  Use this to give the user
    immediate results; if it returns findings you can offer a "Detect more
    types" action that runs the full :func:`detect_watermarks` scan.

    Args:
        path: Absolute path to the PDF.
        password: Password for encrypted PDFs.
        max_pages: If given, only scan this many pages.

    Returns:
        List of WatermarkResult objects (only ARTIFACT type). Empty if none
        found or on error.
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("detect_watermarks_quick: cannot open %s — %s", path.name, exc)
        return []

    results: list[WatermarkResult] = []
    try:
        if doc.is_encrypted and not doc.authenticate(password):
            log.warning("detect_watermarks_quick: wrong password for %s", path.name)
            return []

        scan_limit = min(max_pages, doc.page_count) if max_pages else doc.page_count

        # Strategy A: OCG named "Watermark"
        ocg_watermark = _find_watermark_ocgs(doc)
        if ocg_watermark:
            for pno in range(scan_limit):
                page = doc[pno]
                hrect = _artifact_highlight_rect(doc, page)
                results.append(WatermarkResult(
                    source_path=path,
                    page_index=pno,
                    watermark_type=WatermarkType.ARTIFACT,
                    confidence=_CONF_ARTIFACT,
                    description=(
                        f"PDF watermark layer on page {pno + 1} "
                        f"(OCG: {', '.join(ocg_watermark)})"
                    ),
                    highlight_rect=hrect,
                    removable=True,
                ))
            log.info(
                "detect_watermarks_quick: found watermark OCG(s) %s in %s",
                ocg_watermark, path.name,
            )

        # Strategy B: /Artifact Watermark content-stream marker
        artifact_pages: set[int] = {r.page_index for r in results}
        for pno in range(scan_limit):
            if pno in artifact_pages:
                continue
            page = doc[pno]
            if _page_has_artifact_watermark(doc, page):
                hrect = _artifact_highlight_rect(doc, page)
                results.append(WatermarkResult(
                    source_path=path,
                    page_index=pno,
                    watermark_type=WatermarkType.ARTIFACT,
                    confidence=_CONF_ARTIFACT,
                    description=(
                        f"PDF-tagged watermark on page {pno + 1} "
                        f"(/Artifact Watermark content stream marker)"
                    ),
                    highlight_rect=hrect,
                    removable=True,
                ))

    finally:
        doc.close()

    log.info("detect_watermarks_quick: %d finding(s) in %s", len(results), path.name)
    return results


# ── Artifact / OCG detection helpers ─────────────────────────────────────────

def _artifact_highlight_rect(
    doc: fitz.Document, page: fitz.Page
) -> Optional[tuple]:
    """Return the (x0, y0, x1, y1) bounding rect of the watermark Form XObject.

    Inspects the page content stream for the XObject name used inside the
    ``/Artifact Watermark`` BDC block, then reads that Form XObject's BBox.
    Falls back to the visible page rect if the specific bbox cannot be found.
    """
    import re as _re

    try:
        page_obj = doc.xref_object(page.xref)

        # Build name→xref map for XObjects on this page
        xobj_map: dict[str, int] = {}
        xobj_section = _re.search(r'/XObject\s*<<([^>]+)>>', page_obj)
        if xobj_section:
            for m in _re.finditer(r'/(\w+)\s+(\d+)\s+0\s+R', xobj_section.group(1)):
                xobj_map[m.group(1)] = int(m.group(2))

        # Find which XObject name is invoked inside the Watermark BDC block
        wm_xobj_name: Optional[str] = None
        for content_xref in page.get_contents():
            try:
                stream = doc.xref_stream(content_xref).decode("latin-1", errors="replace")
            except Exception:
                continue
            m = _re.search(
                r'/Artifact\s*<<[^>]*/Watermark[^>]*>>\s*BDC\s.*?/([\w]+)\s+Do',
                stream, _re.DOTALL,
            )
            if m:
                wm_xobj_name = m.group(1)
                break

        if wm_xobj_name and wm_xobj_name in xobj_map:
            xref = xobj_map[wm_xobj_name]
            obj_str = doc.xref_object(xref)
            bbox_m = _re.search(
                r'/BBox\s*\[\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*\]',
                obj_str,
            )
            if bbox_m:
                x0, y0, x1, y1 = (float(bbox_m.group(i)) for i in range(1, 5))
                # Clip to visible page rect
                pr = page.rect
                x0 = max(x0, pr.x0)
                y0 = max(y0, pr.y0)
                x1 = min(x1, pr.x1)
                y1 = min(y1, pr.y1)
                if x1 > x0 and y1 > y0:
                    return (x0, y0, x1, y1)

    except Exception:
        pass

    # Fallback: full page rect
    r = page.rect
    return (r.x0, r.y0, r.x1, r.y1)


def _find_watermark_ocgs(doc: fitz.Document) -> list[str]:
    """Return names of Optional Content Groups that look like watermarks."""
    try:
        ocgs = doc.get_ocgs()
    except Exception:
        return []
    names = []
    for _xref, info in ocgs.items():
        name = info.get("name", "")
        if "watermark" in name.lower():
            names.append(name)
    return names


def _page_has_artifact_watermark(doc: fitz.Document, page: fitz.Page) -> bool:
    """Return True if the page content stream has an /Artifact Watermark marker."""
    try:
        for content_xref in page.get_contents():
            stream_bytes = doc.xref_stream(content_xref)
            try:
                stream = stream_bytes.decode("latin-1")
            except Exception:
                stream = stream_bytes.decode("utf-8", errors="replace")
            if _WM_ARTIFACT_RE.search(stream):
                return True
    except Exception:
        pass
    return False


# ── Text watermark detection ──────────────────────────────────────────────────

def _find_text_wm_spans(page: fitz.Page) -> list[dict]:
    """Return spans that look like text watermarks.

    Detects three patterns:
    - Large, light-grey horizontal text (classic stamp watermarks).
    - Any large text that is rotated / diagonal (the "dir" vector is not
      close to horizontal), regardless of colour — diagonal text in a PDF
      is almost always a watermark.
    - Large centred text that occupies a significant fraction of the page
      even if not obviously light-coloured.
    - Semi-transparent text (alpha < 1.0) at large font size.

    Uses ``get_text("rawdict")`` which preserves the per-span direction
    vector and alpha so we can identify rotated/transparent text.
    """
    candidates: list[dict] = []
    page_w = page.rect.width
    page_h = page.rect.height
    page_area = page_w * page_h
    if page_area == 0:
        return candidates

    page_cx = page_w / 2.0
    page_cy = page_h / 2.0

    try:
        blocks = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    except Exception:
        blocks = page.get_text("dict")["blocks"]

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            # dir is a unit vector (dx, dy) giving text baseline direction.
            # Horizontal LTR → (1, 0).  45° diagonal → (~0.707, ~0.707).
            dir_vec = line.get("dir", (1.0, 0.0))
            dx = dir_vec[0]

            # A span is "diagonal" when |dx| < threshold (noticeably rotated).
            is_diagonal = abs(dx) < _DIAGONAL_THRESHOLD

            for span in line.get("spans", []):
                # rawdict uses "chars" list; dict uses "text" string
                chars = span.get("chars")
                if chars is not None:
                    text = "".join(ch.get("c", "") for ch in chars).strip()
                else:
                    text = span.get("text", "").strip()

                if not text:
                    continue

                size = span.get("size", 0.0)
                if size < _TEXT_WM_MIN_SIZE:
                    continue

                bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                span_w = bbox[2] - bbox[0]
                span_h = bbox[3] - bbox[1]
                span_area = span_w * span_h
                if span_area <= 0:
                    continue

                color = span.get("color", 0)
                r = (color >> 16) & 0xFF
                g = (color >> 8) & 0xFF
                b = color & 0xFF
                brightness = (r + g + b) / (3 * 255)

                # alpha in rawdict is 0–255 (integer); normalise to 0–1
                raw_alpha = span.get("alpha", 255)
                alpha = raw_alpha / 255.0 if isinstance(raw_alpha, int) and raw_alpha > 1 else raw_alpha
                is_transparent = alpha < 0.85

                is_light_grey = brightness >= _TEXT_WM_MIN_BRIGHTNESS and r == g == b
                is_light_any = brightness >= 0.80
                is_light = is_light_grey or is_light_any

                # Span centre proximity to page centre (normalised 0–1)
                span_cx = (bbox[0] + bbox[2]) / 2.0
                span_cy = (bbox[1] + bbox[3]) / 2.0
                near_centre = (
                    abs(span_cx - page_cx) / page_w < 0.35
                    and abs(span_cy - page_cy) / page_h < 0.35
                )

                area_fraction = span_area / page_area

                # Accept span if:
                #  a) Diagonal/rotated (any colour, any position)
                #  b) Semi-transparent at large font
                #  c) Light-coloured AND large enough area
                #  d) Large font, near page centre, reasonable area
                is_large_enough = area_fraction > 0.003   # at least 0.3 % of page

                if is_diagonal and size >= _TEXT_WM_MIN_SIZE and is_large_enough:
                    candidates.append({
                        "text": text, "bbox": bbox, "size": size, "diagonal": True,
                    })
                elif is_transparent and size >= _TEXT_WM_MIN_SIZE and is_large_enough:
                    candidates.append({
                        "text": text, "bbox": bbox, "size": size, "diagonal": False,
                    })
                elif is_light and is_large_enough:
                    candidates.append({
                        "text": text, "bbox": bbox, "size": size, "diagonal": False,
                    })
                elif near_centre and size >= 36 and area_fraction > 0.005:
                    candidates.append({
                        "text": text, "bbox": bbox, "size": size, "diagonal": False,
                    })

    return candidates


# ── Removal ───────────────────────────────────────────────────────────────────

class RemovalError(Exception):
    """Raised when watermark removal cannot be completed safely."""


def remove_watermarks(
    source_path: Path,
    results: list[WatermarkResult],
    output_path: Path,
    password: str = "",
) -> int:
    """Remove detected watermarks and write a clean copy to *output_path*.

    Only processes the pages referenced by *results*.  The source file is
    never modified.

    Args:
        source_path: Original PDF path.
        results: WatermarkResult objects specifying what and where to remove.
        output_path: Destination path for the cleaned PDF.
        password: Password for encrypted source PDFs.

    Returns:
        Number of pages where removals were applied.

    Raises:
        RemovalError: If the source cannot be opened or saved.
    """
    try:
        doc = fitz.open(str(source_path))
    except Exception as exc:
        raise RemovalError(f"Cannot open {source_path.name}: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(password):
            raise RemovalError(f"Wrong password for {source_path.name}.")

        pages_cleaned = 0
        # Group results by page
        by_page: dict[int, list[WatermarkResult]] = {}
        for r in results:
            by_page.setdefault(r.page_index, []).append(r)

        for pno, page_results in by_page.items():
            if pno >= doc.page_count:
                continue
            page = doc[pno]
            changed = False

            for wm in page_results:
                if not wm.removable:
                    continue
                if wm.watermark_type == WatermarkType.ARTIFACT:
                    changed |= _remove_artifact_watermark(doc, page)
                elif wm.watermark_type == WatermarkType.TEXT:
                    changed |= _remove_text_watermark(page)
                elif wm.watermark_type == WatermarkType.IMAGE:
                    changed |= _remove_image_watermarks(page)
                elif wm.watermark_type == WatermarkType.VECTOR:
                    changed |= _remove_vector_watermarks(page)

            if changed:
                pages_cleaned += 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            doc.save(
                str(output_path),
                garbage=4,
                deflate=True,
                clean=True,
            )
        except Exception as exc:
            raise RemovalError(f"Cannot save to {output_path}: {exc}") from exc

        log.info(
            "remove_watermarks: cleaned %d page(s) → %s",
            pages_cleaned,
            output_path.name,
        )
        return pages_cleaned

    finally:
        doc.close()


def _remove_artifact_watermark(doc: fitz.Document, page: fitz.Page) -> bool:
    """Remove PDF-tagged /Artifact Watermark blocks from the page content stream.

    Strips every BDC/EMC block whose /Artifact dictionary carries
    ``/Subtype /Watermark``.  This is the correct removal method for
    publisher-stamped PDFs (e.g. NCERT diagonal watermarks) that embed the
    watermark as a Form XObject inside a marked-content sequence.
    """
    removed = False
    try:
        for content_xref in page.get_contents():
            stream_bytes = doc.xref_stream(content_xref)
            try:
                stream = stream_bytes.decode("latin-1")
            except Exception:
                stream = stream_bytes.decode("utf-8", errors="replace")
            new_stream, n = _WM_ARTIFACT_RE.subn("", stream)
            if n > 0:
                doc.update_stream(content_xref, new_stream.encode("latin-1"))
                removed = True
    except Exception as exc:
        log.warning("_remove_artifact_watermark: %s", exc)
    return removed


def _remove_text_watermark(page: fitz.Page) -> bool:
    """Remove watermark text spans without affecting other page content.

    For diagonal spans the reported bbox is the axis-aligned bounding box
    of the rotated glyphs which may be tight.  We expand it slightly to
    ensure the full rotated text is covered by the redaction rect.
    """
    wm_spans = _find_text_wm_spans(page)
    if not wm_spans:
        return False

    for span in wm_spans:
        bbox = fitz.Rect(span["bbox"])
        if span.get("diagonal"):
            # Inflate the rect by 10 % on each side to catch rotated extremities
            dx = bbox.width * 0.10
            dy = bbox.height * 0.10
            bbox = fitz.Rect(
                bbox.x0 - dx, bbox.y0 - dy,
                bbox.x1 + dx, bbox.y1 + dy,
            )
        page.add_redact_annot(bbox, fill=None, text="")

    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=0)
    return True


def _remove_image_watermarks(page: fitz.Page) -> bool:
    """Remove large-coverage images without affecting text or other content.

    Marks the image bounding rect for redaction with fill=None so the
    page background shows through, then removes only the image XObject.
    """
    page_area = page.rect.width * page.rect.height
    if page_area == 0:
        return False

    removed = False
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        rects = list(page.get_image_rects(xref))
        for rect in rects:
            coverage = (rect.width * rect.height) / page_area
            if coverage > 0.15:
                page.add_redact_annot(rect, fill=None, text="")
                removed = True
                break

    if removed:
        # Remove image pixels; graphics=0 keeps vector content intact
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_REMOVE,
            graphics=0,
        )
    return removed


def _remove_vector_watermarks(page: fitz.Page) -> bool:
    """Remove large-area vector paths without affecting text.

    Uses graphics=1 to erase vector paths inside the redact rect while
    keeping text operators (fill=None means no white box is drawn).
    """
    page_area = page.rect.width * page.rect.height
    if page_area == 0:
        return False

    removed = False
    for path_info in page.get_drawings():
        rect = path_info.get("rect")
        if rect is None:
            continue
        coverage = (rect.width * rect.height) / page_area
        if coverage >= _VECTOR_AREA_FRACTION:
            page.add_redact_annot(rect, fill=None, text="")
            removed = True
            break

    if removed:
        # graphics=1 removes vector paths; images=0 keeps images; text=0 keeps text
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=1,
        )
    return removed


# ── Preview rendering (in-memory, no file writes) ─────────────────────────────

def extract_watermark_text(
    path: Path,
    wm: "WatermarkResult",
    password: str = "",
) -> str:
    """Extract human-readable text from a watermark element.

    For ARTIFACT watermarks reads the text from the Form XObject stream.
    For TEXT watermarks returns the span text(s).
    Returns an empty string if no text can be extracted.
    """
    try:
        doc = fitz.open(str(path))
    except Exception:
        return ""
    try:
        if doc.is_encrypted and not doc.authenticate(password):
            return ""

        page = doc[wm.page_index]

        if wm.watermark_type == WatermarkType.ARTIFACT:
            # Find the Form XObject used in the Watermark BDC block
            page_obj = doc.xref_object(page.xref)
            xobj_section = re.search(r'/XObject\s*<<([^>]+)>>', page_obj)
            xobj_map: dict[str, int] = {}
            if xobj_section:
                for m in re.finditer(r'/(\w+)\s+(\d+)\s+0\s+R', xobj_section.group(1)):
                    xobj_map[m.group(1)] = int(m.group(2))

            wm_xobj_name: Optional[str] = None
            for content_xref in page.get_contents():
                try:
                    stream = doc.xref_stream(content_xref).decode("latin-1", errors="replace")
                except Exception:
                    continue
                m = re.search(
                    r'/Artifact\s*<<[^>]*/Watermark[^>]*>>\s*BDC\s.*?/([\w]+)\s+Do',
                    stream, re.DOTALL,
                )
                if m:
                    wm_xobj_name = m.group(1)
                    break

            if wm_xobj_name and wm_xobj_name in xobj_map:
                xref = xobj_map[wm_xobj_name]
                # Render the Form XObject as a temporary page to extract text
                try:
                    tmp_doc = fitz.open()
                    tmp_page = tmp_doc.new_page(width=595, height=842)
                    # Insert the Form XObject
                    tmp_doc.copy_page(doc, wm.page_index)
                    # Extract text from the xobject stream directly
                    stream_bytes = doc.xref_stream(xref)
                    if stream_bytes:
                        stream_text = stream_bytes.decode("latin-1", errors="replace")
                        # Extract literal strings from PDF stream: (text) Tj / Tj
                        texts = re.findall(r'\(([^)]+)\)\s*Tj', stream_text)
                        # Also check nested Form XObjects one level deep
                        nested_section = re.search(r'/XObject\s*<<([^>]+)>>', doc.xref_object(xref))
                        if nested_section:
                            for nm in re.finditer(r'/(\w+)\s+(\d+)\s+0\s+R', nested_section.group(1)):
                                n_xref = int(nm.group(2))
                                try:
                                    n_stream = doc.xref_stream(n_xref)
                                    if n_stream:
                                        n_text = n_stream.decode("latin-1", errors="replace")
                                        texts.extend(re.findall(r'\(([^)]+)\)\s*Tj', n_text))
                                except Exception:
                                    pass
                        if texts:
                            tmp_doc.close()
                            return " ".join(t for t in texts if t.strip())
                    tmp_doc.close()
                except Exception:
                    pass

        elif wm.watermark_type == WatermarkType.TEXT:
            spans = _find_text_wm_spans(page)
            texts = list(dict.fromkeys(s["text"] for s in spans if s.get("text")))
            if texts:
                return " · ".join(texts[:4])

    except Exception:
        pass
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return ""


def render_page_without_watermark(
    path: Path,
    wm: "WatermarkResult",
    password: str = "",
    width: int = 600,
    height: int = 840,
) -> Optional[bytes]:
    """Render a page with the specified watermark removed, in-memory only.

    Opens the PDF, applies the appropriate removal to an in-memory copy of
    the page, renders it, then discards the modified document — the source
    file is never touched.

    Returns:
        PNG bytes of the rendered page, or None on failure.
    """
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        log.warning("render_page_without_watermark: cannot open %s — %s", path, exc)
        return None

    try:
        if doc.is_encrypted and not doc.authenticate(password):
            return None

        page = doc[wm.page_index]

        if wm.watermark_type == WatermarkType.ARTIFACT:
            _remove_artifact_watermark(doc, page)
        elif wm.watermark_type == WatermarkType.TEXT:
            _remove_text_watermark(page)
        elif wm.watermark_type == WatermarkType.IMAGE:
            _remove_image_watermarks(page)
        elif wm.watermark_type == WatermarkType.VECTOR:
            _remove_vector_watermarks(page)

        # Reload the page object after modifications
        page = doc[wm.page_index]

        scale_x = width / page.rect.width if page.rect.width else 1.0
        scale_y = height / page.rect.height if page.rect.height else 1.0
        scale = min(scale_x, scale_y)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")

    except Exception as exc:
        log.warning("render_page_without_watermark: error — %s", exc)
        return None
    finally:
        doc.close()
