#!/usr/bin/env python3
"""Export a print-ready shortlist map (PDF + PNG @ 300 DPI).

Reads output/chicago_neighborhoods.qgz (built by 10_build_project.py),
builds a QgsPrintLayout programmatically, exports to vector PDF + raster PNG.

Layout:
  - Title strip:        "Chicago Neighborhood Shortlist" + subtitle with survivor count
  - Main map:           Chicago extent, typology layer visible
  - Right legend:       archetype categories
  - Scale bar:          miles, 4 segments × 2 mi each
  - Attribution strip:  data sources + CRS

PyQGIS gotchas worked around here (see saved memory `reference_pyqgis_gotchas`):
  - QgsLayoutExporter is single-use: build a fresh one for PNG after PDF
  - Modify legend.model().rootGroup() IN PLACE, don't construct fresh
  - Export before project.write() (which would invalidate the Python wrapper)
  - QgsTextFormat for all label fonts (the deprecated setFont path is going away)
"""

import sys
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutExporter,
    QgsLegendStyle,
    QgsTextFormat,
    QgsUnitTypes,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import Qt

PROJECT_DIR = Path(__file__).resolve().parent.parent
DPI = 300
MARGIN_MM = 10
PAGE_W, PAGE_H = 279.4, 215.9      # Letter landscape

CHICAGO_BBOX_WGS84 = QgsRectangle(-87.94, 41.64, -87.52, 42.03)

# Scale bar: Chicago is ~16 mi east-west, ~25 mi north-south.
# 4 segments × 2 mi = 8 mi total — readable at print scale.
SCALE_MI_PER_SEG = 2


def _text_fmt(family="Sans", size=11, bold=False, color="#222222") -> QgsTextFormat:
    fmt = QgsTextFormat()
    f = QFont(family); f.setBold(bold)
    fmt.setFont(f); fmt.setSize(size); fmt.setColor(QColor(color))
    return fmt


def _legend_style(size=9, bold=False) -> QgsLegendStyle:
    style = QgsLegendStyle()
    style.setTextFormat(_text_fmt(size=size, bold=bold, color="#111111"))
    return style


def _add_text(layout, text, x, y, fmt, align=None) -> QgsLayoutItemLabel:
    lbl = QgsLayoutItemLabel(layout)
    lbl.setText(text); lbl.setTextFormat(fmt)
    if align is not None:
        lbl.setHAlign(align)
    lbl.adjustSizeToText()
    lbl.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(lbl)
    return lbl


def build_layout(project: QgsProject, survivor_count: int) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Chicago Print")
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(PAGE_W, PAGE_H, QgsUnitTypes.LayoutMillimeters)
    )

    title_h, bottom_h, legend_w = 18, 12, 60
    map_x = MARGIN_MM
    map_y = MARGIN_MM + title_h
    map_w = PAGE_W - 2 * MARGIN_MM - legend_w - 5
    map_h = PAGE_H - 2 * MARGIN_MM - title_h - bottom_h - 5

    # ---- Title ----
    _add_text(layout, "Chicago Neighborhood Shortlist",
              x=MARGIN_MM, y=MARGIN_MM,
              fmt=_text_fmt(size=22, bold=True, color="#111111"))
    _add_text(layout,
              f"{survivor_count} of 77 Community Areas pass safety + transit + housing filters "
              f"· colored by qualitative typology archetype",
              x=MARGIN_MM, y=MARGIN_MM + 11,
              fmt=_text_fmt(size=10, color="#555555"))

    # ---- Main map ----
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
    m.setBackgroundColor(QColor("white"))
    m.setFrameEnabled(True)
    proj_crs = project.crs()
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), proj_crs, project
    )
    m.setExtent(xform.transformBoundingBox(CHICAGO_BBOX_WGS84))
    m.setCrs(proj_crs)
    layout.addLayoutItem(m)

    # ---- Legend (typology only) ----
    # Modify the legend's own root group in place — fresh QgsLayerTree
    # from Python segfaults when GC frees the wrapper (see PyQGIS gotchas memo).
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Archetype")
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    for child in list(root.children()):
        root.removeChildNode(child)
    for lyr in project.mapLayers().values():
        if lyr.name().startswith("Survivors — typology"):
            root.addLayer(lyr)
    legend.setStyle(QgsLegendStyle.Title,       _legend_style(size=11, bold=True))
    legend.setStyle(QgsLegendStyle.Subgroup,    _legend_style(size=9))
    legend.setStyle(QgsLegendStyle.SymbolLabel, _legend_style(size=9))
    legend.attemptMove(QgsLayoutPoint(map_x + map_w + 5, map_y, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(legend_w, map_h, QgsUnitTypes.LayoutMillimeters))
    legend.setFrameEnabled(True)
    legend.setBackgroundColor(QColor(255, 255, 255, 220))
    layout.addLayoutItem(legend)

    # ---- Scale bar ----
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Single Box")
    sb.setLinkedMap(m)
    sb.setUnits(QgsUnitTypes.DistanceMiles)
    sb.setUnitLabel(" mi")
    sb.setUnitsPerSegment(float(SCALE_MI_PER_SEG))
    sb.setNumberOfSegments(4)
    sb.setNumberOfSegmentsLeft(0)
    sb.setHeight(2.5)
    sb.update()
    sb.attemptResize(QgsLayoutSize(60, 8, QgsUnitTypes.LayoutMillimeters))
    sb.attemptMove(QgsLayoutPoint(map_x, map_y + map_h + 1, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(sb)

    # ---- Attribution ----
    attr_text = (
        "Data: Chicago Data Portal (crime, licenses, violations, CAs) · "
        "CTA GTFS · Census ACS 5-yr 2023 · Zillow ZORI · OpenStreetMap · "
        "Esri imagery + hillshade · "
        f"Projection: {proj_crs.authid()}"
    )
    attr = _add_text(layout, attr_text,
                     x=PAGE_W - MARGIN_MM, y=PAGE_H - MARGIN_MM - 5,
                     fmt=_text_fmt(size=8, color="#555555"),
                     align=Qt.AlignRight)
    # Right-align via reposition after adjustSizeToText
    attr.attemptMove(QgsLayoutPoint(
        PAGE_W - MARGIN_MM - attr.rect().width(),
        PAGE_H - MARGIN_MM - 6,
        QgsUnitTypes.LayoutMillimeters,
    ))

    return layout


def main():
    qgz = PROJECT_DIR / "output" / "chicago_neighborhoods.qgz"
    if not qgz.exists():
        sys.exit(f"[11] Missing project file: {qgz}\nRun 10_build_project.py first.")

    qgs = QgsApplication([], False)
    QgsApplication.setPrefixPath("/usr", True)
    qgs.initQgis()

    try:
        project = QgsProject.instance()
        if not project.read(str(qgz)):
            sys.exit(f"[11] Failed to read project: {qgz}")

        # Survivor count for subtitle
        import geopandas as gpd
        survivors = gpd.read_file(PROJECT_DIR / "data/processed/survivors.gpkg")
        n_survivors = len(survivors)

        # Idempotency: clobber any prior layout of the same name
        layout_name = "Chicago Print"
        mgr = project.layoutManager()
        for existing in list(mgr.printLayouts()):
            if existing.name() == layout_name:
                mgr.removeLayout(existing)

        layout = build_layout(project, n_survivors)
        mgr.addLayout(layout)

        # PDF FIRST — project.write() invalidates the Python wrapper
        pdf_path = PROJECT_DIR / "output" / "chicago_neighborhoods_shortlist.pdf"
        pdf_settings = QgsLayoutExporter.PdfExportSettings()
        pdf_settings.dpi = DPI
        pdf_settings.rasterizeWholeImage = False
        pdf_exporter = QgsLayoutExporter(layout)
        res = pdf_exporter.exportToPdf(str(pdf_path), pdf_settings)
        if res != QgsLayoutExporter.Success:
            sys.exit(f"[11] PDF export returned status {res}")
        print(f"[11] {pdf_path}", flush=True)

        # PNG — fresh exporter (reusing the PDF one segfaults)
        png_path = PROJECT_DIR / "output" / "chicago_neighborhoods_shortlist.png"
        img_settings = QgsLayoutExporter.ImageExportSettings()
        img_settings.dpi = DPI
        png_exporter = QgsLayoutExporter(layout)
        res = png_exporter.exportToImage(str(png_path), img_settings)
        if res != QgsLayoutExporter.Success:
            sys.exit(f"[11] PNG export returned status {res}")
        print(f"[11] {png_path}", flush=True)

    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
