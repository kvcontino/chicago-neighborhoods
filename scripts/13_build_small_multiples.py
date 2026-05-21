#!/usr/bin/env python3
"""Small-multiples panel map (Letter landscape, 2x2 grid).

Four mini-maps side by side showing the same 46 survivors viewed through
four lenses, so tradeoffs become visible at a glance:

  ┌──────────────────────┬──────────────────────┐
  │ 1. Typology          │ 2. Composite Score   │
  │    (categorical)     │    (high = better)   │
  ├──────────────────────┼──────────────────────┤
  │ 3. Median Rent       │ 4. Violent Crime /1k │
  │    (low = better)    │    (low = better)    │
  └──────────────────────┴──────────────────────┘

All four panels use:
  - CartoDB Positron basemap (clean light gray)
  - Same Chicago extent
  - All-CA ghost outlines for context

Quantitative panels (2, 3, 4) use diverging RdYlGn color ramp on
quantile-binned 5 classes. Green always means 'better' to keep
the visual language consistent across panels.

Output:
  output/chicago_small_multiples.pdf / .png
"""

import sys
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsFillSymbol,
    QgsSingleSymbolRenderer,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsRendererCategory,
    QgsRendererRange,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutMeasurement,
    QgsLayoutExporter,
    QgsTextFormat,
    QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import Qt

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA = PROJECT_DIR / "data" / "processed"
OUTPUT = PROJECT_DIR / "output"
DPI = 300

# Letter landscape
PAGE_W, PAGE_H = 279.4, 215.9
MARGIN = 10
TITLE_H = 18
BOTTOM_H = 8
PANEL_GAP = 3

CHICAGO_BBOX_WGS84 = QgsRectangle(-87.94, 41.64, -87.52, 42.03)
PROJECT_CRS = "EPSG:3435"

CARTODB_POSITRON = (
    "type=xyz"
    "&url=https://a.basemaps.cartocdn.com/light_all/%7Bz%7D/%7Bx%7D/%7By%7D.png"
    "&zmax=20&zmin=0"
)

# Same muted palette as the editorial map for the typology panel
ARCHETYPE_COLORS = {
    "young-prof-nightlife":    "#d96f5b",
    "established-lakefront":   "#6a8caf",
    "established-mainstreet":  "#5a7ba0",
    "gentrifying-edge":        "#e8a87c",
    "quiet-artsy":             "#85a594",
    "industrial-cool":         "#9b7eb5",
    "latino-cultural":         "#d4b569",
    "asian-cultural":          "#e0a07c",
    "lgbtq-anchor":            "#d691b5",
    "family-residential":      "#a3c47a",
    "university-adjacent":     "#7eb1c6",
    "transit-bedroom":         "#a8a566",
    "diverse-bohemian":        "#7eb5b8",
    "insufficient-data":       "#d8d8d8",
}

# RdYlGn diverging ramp, 5 classes
RAMP_RDYLGN = ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"]
RAMP_RDYLGN_R = list(reversed(RAMP_RDYLGN))


def _text_fmt(family="Sans", size=10, bold=False, italic=False,
              color="#222222") -> QgsTextFormat:
    fmt = QgsTextFormat()
    f = QFont(family); f.setBold(bold); f.setItalic(italic)
    fmt.setFont(f); fmt.setSize(size); fmt.setColor(QColor(color))
    return fmt


def make_basemap() -> QgsRasterLayer:
    layer = QgsRasterLayer(CARTODB_POSITRON, "Carto Positron", "wms")
    layer.setOpacity(0.85)
    return layer


def make_outline_ghosts() -> QgsVectorLayer:
    layer = QgsVectorLayer(str(DATA / "community_areas.gpkg"),
                           "All CAs outline", "ogr")
    sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "outline_color": "150,150,150",
        "outline_width": "0.15", "outline_style": "dot",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    return layer


def make_typology_layer() -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{DATA / 'typology.gpkg'}|layername=typology",
                           "Typology", "ogr")
    cats = []
    for arche, color in ARCHETYPE_COLORS.items():
        sym = QgsFillSymbol.createSimple({
            "color": color, "outline_color": "70,70,70",
            "outline_width": "0.2", "color_opacity": "0.85",
        })
        cats.append(QgsRendererCategory(arche, sym, arche))
    layer.setRenderer(QgsCategorizedSymbolRenderer("archetype", cats))
    return layer


def make_graduated_layer(field: str, colors: list, label: str,
                         fmt_value=lambda v: f"{v:.1f}") -> QgsVectorLayer:
    """Build a survivors layer with a quantile-binned graduated renderer."""
    layer = QgsVectorLayer(f"{DATA / 'survivors.gpkg'}|layername=survivors",
                           label, "ogr")
    df = gpd.read_file(DATA / "survivors.gpkg", layer="survivors")
    series = df[field].dropna()
    breaks = series.quantile(
        [i / len(colors) for i in range(len(colors) + 1)]
    ).values
    ranges = []
    for i, color in enumerate(colors):
        lo, hi = breaks[i], breaks[i + 1]
        sym = QgsFillSymbol.createSimple({
            "color": color, "outline_color": "70,70,70",
            "outline_width": "0.2", "color_opacity": "0.88",
        })
        ranges.append(QgsRendererRange(
            lo, hi, sym, f"{fmt_value(lo)} – {fmt_value(hi)}"
        ))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
    return layer


def add_panel(layout, project, x, y, w, h, title, layers, color_swatches=None):
    """One panel = title label + map + optional inline color-ramp strip."""
    proj_crs = project.crs()
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), proj_crs, project
    )

    # Panel title (above map)
    title_lbl = QgsLayoutItemLabel(layout)
    title_lbl.setText(title)
    title_lbl.setTextFormat(_text_fmt(family="Sans", size=10, bold=True,
                                       color="#111111"))
    title_lbl.adjustSizeToText()
    title_lbl.attemptMove(QgsLayoutPoint(x, y,
                                         QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(title_lbl)

    map_h_eff = h - 6   # leave 6mm at top for title
    map_y = y + 6

    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(x, map_y, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(w, map_h_eff,
                                   QgsUnitTypes.LayoutMillimeters))
    m.setBackgroundColor(QColor("white"))
    m.setFrameEnabled(True)
    m.setFrameStrokeColor(QColor("#cccccc"))
    m.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, QgsUnitTypes.LayoutMillimeters))
    m.setExtent(xform.transformBoundingBox(CHICAGO_BBOX_WGS84))
    m.setCrs(proj_crs)
    # setLayers expects TOP-TO-BOTTOM order (first = topmost). Caller passes
    # bottom-to-top to match human convention; we reverse here.
    m.setLayers(list(reversed(layers)))
    layout.addLayoutItem(m)

    # Inline color ramp (bottom-left corner of map) for graduated panels
    if color_swatches:
        swatch_w = 2.5
        swatch_h = 2.5
        sw_x = x + 1.5
        sw_y = map_y + map_h_eff - swatch_h - 1.5
        for i, color in enumerate(color_swatches):
            # We'd want filled boxes here; use small labels with background as a hack
            box = QgsLayoutItemLabel(layout)
            box.setText(" ")
            box.setBackgroundEnabled(True)
            box.setBackgroundColor(QColor(color))
            box.attemptMove(QgsLayoutPoint(sw_x + i * swatch_w, sw_y,
                                            QgsUnitTypes.LayoutMillimeters))
            box.attemptResize(QgsLayoutSize(swatch_w, swatch_h,
                                             QgsUnitTypes.LayoutMillimeters))
            box.setFrameEnabled(True)
            box.setFrameStrokeColor(QColor("#888"))
            box.setFrameStrokeWidth(QgsLayoutMeasurement(0.1, QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(box)
        # "low" / "high" labels
        lbl_lo = QgsLayoutItemLabel(layout)
        lbl_lo.setText("low")
        lbl_lo.setTextFormat(_text_fmt(size=6, italic=True, color="#444"))
        lbl_lo.adjustSizeToText()
        lbl_lo.attemptMove(QgsLayoutPoint(sw_x, sw_y - 3,
                                           QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(lbl_lo)
        lbl_hi = QgsLayoutItemLabel(layout)
        lbl_hi.setText("high")
        lbl_hi.setTextFormat(_text_fmt(size=6, italic=True, color="#444"))
        lbl_hi.adjustSizeToText()
        lbl_hi.attemptMove(QgsLayoutPoint(
            sw_x + (len(color_swatches) - 1) * swatch_w - 1, sw_y - 3,
            QgsUnitTypes.LayoutMillimeters,
        ))
        layout.addLayoutItem(lbl_hi)


def build_layout(project: QgsProject, layers: dict) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("SmallMultiples")
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(PAGE_W, PAGE_H, QgsUnitTypes.LayoutMillimeters)
    )

    # ---- Title strip ----
    title = QgsLayoutItemLabel(layout)
    title.setText("Chicago, Four Ways")
    title.setTextFormat(_text_fmt(family="Serif", size=20, bold=True,
                                   color="#111111"))
    title.adjustSizeToText()
    title.attemptMove(QgsLayoutPoint(MARGIN, MARGIN,
                                      QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(title)

    subtitle = QgsLayoutItemLabel(layout)
    subtitle.setText(
        "46 surviving Community Areas viewed through four lenses · "
        "green always means \"better\" on the quantitative panels"
    )
    subtitle.setTextFormat(_text_fmt(family="Sans", size=8.5, italic=True,
                                      color="#555"))
    subtitle.adjustSizeToText()
    subtitle.attemptMove(QgsLayoutPoint(MARGIN, MARGIN + 9,
                                         QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(subtitle)

    # ---- 2x2 panel grid ----
    grid_y = MARGIN + TITLE_H
    grid_h = PAGE_H - 2 * MARGIN - TITLE_H - BOTTOM_H
    grid_w = PAGE_W - 2 * MARGIN

    panel_w = (grid_w - PANEL_GAP) / 2
    panel_h = (grid_h - PANEL_GAP) / 2

    base_layers = layers["base"]   # basemap + outlines (used in all panels)
    panels = [
        ("1 — Typology",            layers["typology"], None),
        ("2 — Composite Score",     layers["composite"], RAMP_RDYLGN),
        ("3 — Median Rent",         layers["rent"],     RAMP_RDYLGN_R),
        ("4 — Violent Crime / 1k",  layers["crime"],    RAMP_RDYLGN_R),
    ]
    for idx, (title_text, thematic_layer, ramp) in enumerate(panels):
        col = idx % 2
        row = idx // 2
        x = MARGIN + col * (panel_w + PANEL_GAP)
        y = grid_y + row * (panel_h + PANEL_GAP)
        add_panel(layout, project, x, y, panel_w, panel_h,
                  title_text, base_layers + [thematic_layer], ramp)

    # ---- Attribution ----
    attr = QgsLayoutItemLabel(layout)
    attr.setText(
        "Data: Chicago Data Portal · Census ACS · Zillow ZORI · CTA · OSM · "
        "Basemap © CARTO, © OpenStreetMap contributors"
    )
    attr.setTextFormat(_text_fmt(family="Sans", size=6.5, color="#888"))
    attr.adjustSizeToText()
    attr.attemptMove(QgsLayoutPoint(
        MARGIN, PAGE_H - MARGIN - 4, QgsUnitTypes.LayoutMillimeters
    ))
    layout.addLayoutItem(attr)

    return layout


def main():
    qgs = QgsApplication([], False)
    QgsApplication.setPrefixPath("/usr", True)
    qgs.initQgis()

    try:
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem(PROJECT_CRS))

        basemap = make_basemap()
        outline = make_outline_ghosts()
        typology = make_typology_layer()
        composite = make_graduated_layer(
            "composite_score", RAMP_RDYLGN, "Composite Score",
            fmt_value=lambda v: f"{v:.0f}",
        )
        rent = make_graduated_layer(
            "median_rent_current", RAMP_RDYLGN_R, "Median Rent",
            fmt_value=lambda v: f"${v:.0f}",
        )
        crime = make_graduated_layer(
            "violent_per_1k", RAMP_RDYLGN_R, "Violent Crime per 1k",
            fmt_value=lambda v: f"{v:.1f}",
        )

        for lyr in (basemap, outline, typology, composite, rent, crime):
            project.addMapLayer(lyr)

        layers = {
            "base":      [basemap, outline],
            "typology":  typology,
            "composite": composite,
            "rent":      rent,
            "crime":     crime,
        }

        layout = build_layout(project, layers)

        pdf = OUTPUT / "chicago_small_multiples.pdf"
        s = QgsLayoutExporter.PdfExportSettings()
        s.dpi = DPI; s.rasterizeWholeImage = False
        if QgsLayoutExporter(layout).exportToPdf(str(pdf), s) != QgsLayoutExporter.Success:
            sys.exit("[13] PDF export failed")
        print(f"[13] {pdf}", flush=True)

        png = OUTPUT / "chicago_small_multiples.png"
        i = QgsLayoutExporter.ImageExportSettings()
        i.dpi = DPI
        if QgsLayoutExporter(layout).exportToImage(str(png), i) != QgsLayoutExporter.Success:
            sys.exit("[13] PNG export failed")
        print(f"[13] {png}", flush=True)

    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
