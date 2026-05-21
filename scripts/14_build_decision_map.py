#!/usr/bin/env python3
"""Personal decision map (Letter portrait, opinionated visual hierarchy).

Top 10 CAs by composite_score get full saturation + bold labels.
The other 36 survivors get heavily-muted versions of their archetype
color + small italic labels. Non-survivors stay as ghost outlines only.
The top 3 get explicit annotation callouts with their rank, archetype,
and headline metric.

Purpose: a single map you can pin to a wall as "the shortlist" — your
eye is pulled to the top candidates first, the rest sit as context.

Output:
  output/chicago_decision_map.pdf / .png
"""

import sys
import warnings
from colorsys import rgb_to_hls, hls_to_rgb
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
    QgsRuleBasedRenderer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsRuleBasedLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutExporter,
    QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import Qt

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA = PROJECT_DIR / "data" / "processed"
OUTPUT = PROJECT_DIR / "output"
DPI = 300

PAGE_W, PAGE_H = 215.9, 279.4   # Letter portrait
MARGIN = 12

CHICAGO_BBOX_WGS84 = QgsRectangle(-87.94, 41.64, -87.52, 42.03)
PROJECT_CRS = "EPSG:3435"

CARTODB_POSITRON = (
    "type=xyz"
    "&url=https://a.basemaps.cartocdn.com/light_all/%7Bz%7D/%7Bx%7D/%7By%7D.png"
    "&zmax=20&zmin=0"
)

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


def desaturate(hex_color: str, sat_factor: float = 0.15,
                lighten: float = 0.78) -> str:
    """Push a hex color toward gray/light. Used for non-top-10 fills."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255
    h, l, s = rgb_to_hls(r, g, b)
    s *= sat_factor
    l = l + (1 - l) * lighten
    r, g, b = hls_to_rgb(h, l, s)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _q(s: str) -> str:
    """Quote a string for SQL/QGIS expression with embedded apostrophes."""
    return "'" + s.replace("'", "''") + "'"


def _text_fmt(family="Sans", size=10, bold=False, italic=False,
              color="#222", halo=True, halo_color="#fff", halo_size=0.6):
    fmt = QgsTextFormat()
    f = QFont(family); f.setBold(bold); f.setItalic(italic)
    fmt.setFont(f); fmt.setSize(size); fmt.setColor(QColor(color))
    if halo:
        buf = QgsTextBufferSettings()
        buf.setEnabled(True); buf.setSize(halo_size)
        c = QColor(halo_color); c.setAlpha(220)
        buf.setColor(c)
        fmt.setBuffer(buf)
    return fmt


def make_basemap() -> QgsRasterLayer:
    layer = QgsRasterLayer(CARTODB_POSITRON, "Carto Positron", "wms")
    layer.setOpacity(0.85)
    return layer


def make_outline_ghosts() -> QgsVectorLayer:
    layer = QgsVectorLayer(str(DATA / "community_areas.gpkg"),
                           "All CAs (ghost outlines)", "ogr")
    sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "outline_color": "150,150,150",
        "outline_width": "0.15", "outline_style": "dot",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    return layer


def make_decision_layer(top10_names: list) -> QgsVectorLayer:
    """Rule-based renderer: bright fill for top 10, muted for rest."""
    layer = QgsVectorLayer(f"{DATA / 'typology.gpkg'}|layername=typology",
                           "Decision shortlist", "ogr")

    root = QgsRuleBasedRenderer.Rule(None)
    top10_filter = ", ".join(_q(n) for n in top10_names)

    # Bright rules — one per archetype, applied only to top 10
    for arche, color in ARCHETYPE_COLORS.items():
        sym = QgsFillSymbol.createSimple({
            "color": color, "outline_color": "#222",
            "outline_width": "0.5", "color_opacity": "0.9",
        })
        rule = QgsRuleBasedRenderer.Rule(sym, label=f"★ {arche}")
        rule.setFilterExpression(
            f'"archetype" = {_q(arche)} AND "community" IN ({top10_filter})'
        )
        root.appendChild(rule)

    # Muted rules — same archetypes, applied to the rest
    for arche, color in ARCHETYPE_COLORS.items():
        muted = desaturate(color)
        sym = QgsFillSymbol.createSimple({
            "color": muted, "outline_color": "#bbbbbb",
            "outline_width": "0.2", "color_opacity": "0.85",
        })
        rule = QgsRuleBasedRenderer.Rule(sym, label=f"{arche} (other survivors)")
        rule.setFilterExpression(
            f'"archetype" = {_q(arche)} AND NOT ("community" IN ({top10_filter}))'
        )
        root.appendChild(rule)

    layer.setRenderer(QgsRuleBasedRenderer(root))
    return layer


def add_labels(layer: QgsVectorLayer, top10_names: list) -> None:
    """Two-tier rule-based labeling: top 10 bold, others tiny + faded."""
    top_filter = ", ".join(_q(n) for n in top10_names)

    # Top 10: bold, larger, dark text with strong halo
    top_settings = QgsPalLayerSettings()
    top_settings.fieldName = "community"
    top_settings.placement = QgsPalLayerSettings.Horizontal
    top_settings.centroidWhole = True
    top_settings.centroidInside = True
    top_settings.setFormat(_text_fmt(size=9, bold=True, color="#111",
                                       halo=True, halo_size=1.2))
    top_rule = QgsRuleBasedLabeling.Rule(top_settings)
    top_rule.setFilterExpression(f'"community" IN ({top_filter})')

    # Others: tiny, italic, light gray, minimal halo — recede into background
    other_settings = QgsPalLayerSettings()
    other_settings.fieldName = "community"
    other_settings.placement = QgsPalLayerSettings.Horizontal
    other_settings.centroidWhole = True
    other_settings.centroidInside = True
    other_settings.setFormat(_text_fmt(size=5, italic=True, color="#888",
                                         halo=True, halo_size=0.3))
    other_rule = QgsRuleBasedLabeling.Rule(other_settings)
    other_rule.setFilterExpression(f'NOT ("community" IN ({top_filter}))')

    root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
    root.appendChild(top_rule)
    root.appendChild(other_rule)

    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)


def build_layout(project: QgsProject, top3_records: list) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("DecisionMap")
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(PAGE_W, PAGE_H, QgsUnitTypes.LayoutMillimeters)
    )

    title_h = 22
    callout_h = 50
    bottom_h = 12
    map_x = MARGIN
    map_y = MARGIN + title_h
    map_w = PAGE_W - 2 * MARGIN
    map_h = PAGE_H - 2 * MARGIN - title_h - callout_h - bottom_h

    # ---- Title ----
    title = QgsLayoutItemLabel(layout)
    title.setText("The Shortlist")
    title.setTextFormat(_text_fmt(family="Serif", size=24, bold=True,
                                    color="#111", halo=False))
    title.adjustSizeToText()
    title.attemptMove(QgsLayoutPoint(MARGIN, MARGIN,
                                      QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(title)

    n_top = 10
    decision_layers = project.mapLayersByName("Decision shortlist")
    n_total = decision_layers[0].featureCount() if decision_layers else 0
    n_top_actual = min(n_top, n_total)
    n_others = max(0, n_total - n_top)
    subtitle_text = (
        f"Top {n_top_actual} Community Areas by composite score, in full color. "
    )
    if n_others > 0:
        subtitle_text += f"Other {n_others} survivor{'s' if n_others != 1 else ''} muted; "
    subtitle_text += "filtered-out CAs as ghost outlines only."
    subtitle = QgsLayoutItemLabel(layout)
    subtitle.setText(subtitle_text)
    subtitle.setTextFormat(_text_fmt(size=9, italic=True, color="#555",
                                       halo=False))
    subtitle.adjustSizeToText()
    subtitle.attemptMove(QgsLayoutPoint(MARGIN, MARGIN + 11,
                                          QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(subtitle)

    # ---- Main map ----
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(map_w, map_h,
                                    QgsUnitTypes.LayoutMillimeters))
    m.setBackgroundColor(QColor("white"))
    m.setFrameEnabled(False)
    proj_crs = project.crs()
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), proj_crs, project
    )
    m.setExtent(xform.transformBoundingBox(CHICAGO_BBOX_WGS84))
    m.setCrs(proj_crs)
    layout.addLayoutItem(m)

    # ---- Lake Michigan label ----
    lake = QgsLayoutItemLabel(layout)
    lake.setText("Lake Michigan")
    lake.setTextFormat(_text_fmt(family="Serif", size=11, italic=True,
                                    color="#6a8caf", halo=False))
    lake.adjustSizeToText()
    lake.attemptMove(QgsLayoutPoint(
        map_x + map_w - 50, map_y + map_h - 35,
        QgsUnitTypes.LayoutMillimeters,
    ))
    layout.addLayoutItem(lake)

    # ---- Top-3 annotation callouts (below map) ----
    callout_y = map_y + map_h + 4
    callout_w = (map_w - 6) / 3
    for i, rec in enumerate(top3_records):
        cx = map_x + i * (callout_w + 3)
        # Title line: "#1 Lake View" in serif bold
        rank_lbl = QgsLayoutItemLabel(layout)
        rank_lbl.setText(f"#{rec['rank']}  {rec['community'].title()}")
        rank_lbl.setTextFormat(_text_fmt(family="Serif", size=12, bold=True,
                                          color="#111", halo=False))
        rank_lbl.adjustSizeToText()
        rank_lbl.attemptMove(QgsLayoutPoint(cx, callout_y,
                                              QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(rank_lbl)

        # Body: archetype + score + key metric
        body_lbl = QgsLayoutItemLabel(layout)
        body_lbl.setText(
            f"{rec['archetype']}\n"
            f"composite {rec['composite_score']:.1f} · "
            f"rent ${rec['median_rent_current']:.0f} · "
            f"walk {rec['walk_score_proxy']:.0f} · "
            f"transit {rec['transit_headroom']:.0f}"
        )
        body_lbl.setTextFormat(_text_fmt(size=7.5, color="#444", halo=False))
        body_lbl.adjustSizeToText()
        body_lbl.attemptResize(QgsLayoutSize(callout_w, 30,
                                               QgsUnitTypes.LayoutMillimeters))
        body_lbl.attemptMove(QgsLayoutPoint(cx, callout_y + 6,
                                              QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(body_lbl)

        # Notable_for snippet (1 line)
        notable = (rec.get("notable_for") or "").split(".")[0][:120]
        if notable:
            note_lbl = QgsLayoutItemLabel(layout)
            note_lbl.setText(notable + "…")
            note_lbl.setTextFormat(_text_fmt(size=6.5, italic=True,
                                              color="#666", halo=False))
            note_lbl.adjustSizeToText()
            note_lbl.attemptResize(QgsLayoutSize(callout_w, 16,
                                                  QgsUnitTypes.LayoutMillimeters))
            note_lbl.attemptMove(QgsLayoutPoint(cx, callout_y + 22,
                                                  QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(note_lbl)

    # ---- Attribution ----
    attr = QgsLayoutItemLabel(layout)
    attr.setText(
        "Top 10 ranked by equal-weighted composite of walkability + transit + "
        "safety + cost · Data: CDP, Census ACS, Zillow ZORI, CTA, OSM · "
        "Typology: data/processed/typology.yaml"
    )
    attr.setTextFormat(_text_fmt(size=6.5, color="#888", halo=False))
    attr.adjustSizeToText()
    attr.attemptResize(QgsLayoutSize(map_w, 8,
                                       QgsUnitTypes.LayoutMillimeters))
    attr.attemptMove(QgsLayoutPoint(
        MARGIN, PAGE_H - MARGIN - 4, QgsUnitTypes.LayoutMillimeters,
    ))
    layout.addLayoutItem(attr)

    return layout


def main():
    # Compute top 10 + top 3 from typology.gpkg before initializing QGIS
    df = gpd.read_file(DATA / "typology.gpkg", layer="typology")
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    top10_names = df.head(10)["community"].tolist()
    top3_records = df.head(3).to_dict(orient="records")

    print(f"[14] Top 10 by composite:")
    for _, r in df.head(10).iterrows():
        print(f"     #{r['rank']:>2} {r['community']:<22} "
              f"({r['archetype']}, composite {r['composite_score']:.1f})")

    qgs = QgsApplication([], False)
    QgsApplication.setPrefixPath("/usr", True)
    qgs.initQgis()

    try:
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem(PROJECT_CRS))

        basemap = make_basemap()
        outline = make_outline_ghosts()
        decision = make_decision_layer(top10_names)
        add_labels(decision, top10_names)

        for lyr in (decision, outline, basemap):
            project.addMapLayer(lyr)

        layout = build_layout(project, top3_records)

        pdf = OUTPUT / "chicago_decision_map.pdf"
        s = QgsLayoutExporter.PdfExportSettings()
        s.dpi = DPI; s.rasterizeWholeImage = False
        if QgsLayoutExporter(layout).exportToPdf(str(pdf), s) != QgsLayoutExporter.Success:
            sys.exit("[14] PDF export failed")
        print(f"[14] {pdf}", flush=True)

        png = OUTPUT / "chicago_decision_map.png"
        i = QgsLayoutExporter.ImageExportSettings()
        i.dpi = DPI
        if QgsLayoutExporter(layout).exportToImage(str(png), i) != QgsLayoutExporter.Success:
            sys.exit("[14] PNG export failed")
        print(f"[14] {png}", flush=True)

    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
