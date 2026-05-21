#!/usr/bin/env python3
"""Editorial-grade typology map (Letter portrait, magazine-quality).

Design decisions different from 11_export_print_map.py:
  - CartoDB Positron basemap (clean light gray) instead of satellite + hillshade
  - Muted color palette (warm-cool natural tones) instead of saturated ColorBrewer Set1
  - Neighborhood names labeled on the CAs themselves (not CTA stops)
  - CTA L stations as small dots only, no labels (just shape recognition)
  - Non-survivor CAs as ghost outlines (visible context, no fill)
  - Typography hierarchy: serif title, italic subtitle, restrained body
  - Subtle "Lake Michigan" italic annotation

Output:
  output/chicago_editorial.pdf      (vector)
  output/chicago_editorial.png      (300 DPI raster)
"""

import sys
import warnings
from pathlib import Path

import geopandas as gpd
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsSingleSymbolRenderer,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutExporter,
    QgsLegendStyle,
    QgsUnitTypes,
)
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import Qt

warnings.filterwarnings("ignore")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA = PROJECT_DIR / "data" / "processed"
OUTPUT = PROJECT_DIR / "output"
DPI = 300

# Letter portrait — taller than wide, matches Chicago's N-S elongation
PAGE_W, PAGE_H = 215.9, 279.4
MARGIN = 12

CHICAGO_BBOX_WGS84 = QgsRectangle(-87.94, 41.64, -87.52, 42.03)
PROJECT_CRS = "EPSG:3435"

CARTODB_POSITRON = (
    "type=xyz"
    "&url=https://a.basemaps.cartocdn.com/light_all/%7Bz%7D/%7Bx%7D/%7By%7D.png"
    "&zmax=20&zmin=0"
)

# Muted, hand-balanced palette — warmth on the active/lively archetypes,
# cool muted tones on the residential/quiet ones, gold on the cultural ones.
ARCHETYPE_COLORS = {
    "young-prof-nightlife":    "#d96f5b",   # coral
    "established-lakefront":   "#6a8caf",   # dusty slate-blue
    "established-mainstreet":  "#5a7ba0",   # darker slate-blue
    "gentrifying-edge":        "#e8a87c",   # peach
    "quiet-artsy":             "#85a594",   # sage
    "industrial-cool":         "#9b7eb5",   # muted plum
    "latino-cultural":         "#d4b569",   # warm gold
    "asian-cultural":          "#e0a07c",   # muted apricot
    "lgbtq-anchor":            "#d691b5",   # rose
    "family-residential":      "#a3c47a",   # soft green
    "university-adjacent":     "#7eb1c6",   # pale teal
    "transit-bedroom":         "#a8a566",   # khaki olive
    "diverse-bohemian":        "#7eb5b8",   # muted aqua
    "insufficient-data":       "#d8d8d8",   # very pale gray
}

# Stops to label as termini (rest of L stops shown as unlabeled dots)
TERMINUS_NAMES = {
    "O'Hare", "Forest Park", "Howard", "95th/Dan Ryan", "Kimball",
    "Midway", "Harlem-Lake", "Cottage Grove", "Ashland/63rd",
    "Linden", "Dempster-Skokie", "54th/Cermak",
}


def _text_fmt(family="Sans", size=10, bold=False, italic=False,
              color="#222222", halo=True) -> QgsTextFormat:
    fmt = QgsTextFormat()
    f = QFont(family); f.setBold(bold); f.setItalic(italic)
    fmt.setFont(f); fmt.setSize(size); fmt.setColor(QColor(color))
    if halo:
        buf = QgsTextBufferSettings()
        buf.setEnabled(True); buf.setSize(0.6)
        buf.setColor(QColor(255, 255, 255, 220))
        fmt.setBuffer(buf)
    return fmt


def _legend_style(size=8, bold=False) -> QgsLegendStyle:
    s = QgsLegendStyle()
    s.setTextFormat(_text_fmt(family="Sans", size=size, bold=bold,
                              color="#1a1a1a", halo=False))
    return s


def make_basemap() -> QgsRasterLayer:
    layer = QgsRasterLayer(CARTODB_POSITRON, "Carto Positron", "wms")
    layer.setOpacity(0.85)   # slight wash so the thematic fills sit cleanly
    return layer


def make_outline_ghosts() -> QgsVectorLayer:
    """All 77 CAs as faint outlines — visible context for what got filtered."""
    layer = QgsVectorLayer(str(DATA / "community_areas.gpkg"),
                           "All CAs (ghost outlines)", "ogr")
    sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",
        "outline_color": "150,150,150",
        "outline_width": "0.15",
        "outline_style": "dot",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    return layer


def make_typology_layer() -> QgsVectorLayer:
    uri = f"{DATA / 'typology.gpkg'}|layername=typology"
    layer = QgsVectorLayer(uri, "Survivors — typology", "ogr")

    categories = []
    for arche, color in ARCHETYPE_COLORS.items():
        sym = QgsFillSymbol.createSimple({
            "color": color,
            "outline_color": "70,70,70",
            "outline_width": "0.25",
            "color_opacity": "0.78",
        })
        categories.append(QgsRendererCategory(arche, sym, arche))
    renderer = QgsCategorizedSymbolRenderer("archetype", categories)
    layer.setRenderer(renderer)

    # Labels: small, centered on CA, with halo.
    # Polygon placement: Horizontal + centroidWhole/centroidInside =
    # "horizontal text at the polygon's interior centroid". (OverPoint is
    # for point layers; trying it on polygons fails with an enum mismatch
    # in QGIS 3.40+.)
    label_settings = QgsPalLayerSettings()
    label_settings.fieldName = "community"
    label_settings.placement = QgsPalLayerSettings.Horizontal
    label_settings.centroidWhole = True
    label_settings.centroidInside = True
    label_settings.setFormat(_text_fmt(size=6.5, bold=False, color="#1a1a1a"))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
    layer.setLabelsEnabled(True)
    return layer


def make_rail_layer() -> QgsVectorLayer:
    """L stops: small dots, only termini get labels."""
    uri = f"{DATA / 'cta_rail_stops.gpkg'}|layername=cta_rail_stops"
    layer = QgsVectorLayer(uri, "CTA L stops", "ogr")

    sym = QgsMarkerSymbol.createSimple({
        "name": "circle", "size": "1.4",
        "color": "30,30,30", "outline_color": "255,255,255",
        "outline_width": "0.25",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))

    # Label only termini — expression filter (escape single quotes for SQL/QGIS)
    ls = QgsPalLayerSettings()
    ls.isExpression = True
    def _q(s):
        return "'" + s.replace("'", "''") + "'"
    termini_list = ", ".join(_q(t) for t in TERMINUS_NAMES)
    ls.fieldName = f'if("stop_name" IN ({termini_list}), "stop_name", \'\')'
    ls.placement = QgsPalLayerSettings.OrderedPositionsAroundPoint
    ls.setFormat(_text_fmt(size=6, italic=True, color="#444444"))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(ls))
    layer.setLabelsEnabled(True)
    return layer


def build_layout(project: QgsProject) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Editorial")
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(PAGE_W, PAGE_H, QgsUnitTypes.LayoutMillimeters)
    )

    # Geometry: title band, map, legend band, attribution.
    # 32mm bottom band fits a 4-column legend + scale bar + attribution.
    title_h = 22
    bottom_h = 32
    map_x = MARGIN
    map_y = MARGIN + title_h
    map_w = PAGE_W - 2 * MARGIN
    map_h = PAGE_H - 2 * MARGIN - title_h - bottom_h

    # ---- Title (serif, restrained) ----
    title = QgsLayoutItemLabel(layout)
    title.setText("Chicago by Archetype")
    title.setTextFormat(_text_fmt(family="Serif", size=24, bold=True,
                                  color="#111111", halo=False))
    title.adjustSizeToText()
    title.attemptMove(QgsLayoutPoint(MARGIN, MARGIN,
                                     QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(title)

    # ---- Subtitle (italic, gray) ----
    subtitle = QgsLayoutItemLabel(layout)
    subtitle.setText(
        "46 of 77 Community Areas, characterized by qualitative archetype "
        "after safety, transit, and housing filters"
    )
    subtitle.setTextFormat(_text_fmt(family="Sans", size=9.5, italic=True,
                                     color="#555555", halo=False))
    subtitle.adjustSizeToText()
    subtitle.attemptMove(QgsLayoutPoint(MARGIN, MARGIN + 11,
                                       QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(subtitle)

    # ---- Main map ----
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
    m.setBackgroundColor(QColor("white"))
    m.setFrameEnabled(False)
    proj_crs = project.crs()
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), proj_crs, project
    )
    m.setExtent(xform.transformBoundingBox(CHICAGO_BBOX_WGS84))
    m.setCrs(proj_crs)
    layout.addLayoutItem(m)

    # ---- Lake Michigan label (italic, restrained) ----
    lake_lbl = QgsLayoutItemLabel(layout)
    lake_lbl.setText("Lake Michigan")
    lake_lbl.setTextFormat(_text_fmt(family="Serif", size=11, italic=True,
                                     color="#6a8caf", halo=False))
    lake_lbl.adjustSizeToText()
    # Bottom-right of map = lake area on a Chicago map
    lake_lbl.attemptMove(QgsLayoutPoint(
        map_x + map_w - 50,
        map_y + map_h - 35,
        QgsUnitTypes.LayoutMillimeters,
    ))
    layout.addLayoutItem(lake_lbl)

    # ---- Legend (bottom-left, horizontal-feeling, no frame) ----
    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("")
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    for child in list(root.children()):
        root.removeChildNode(child)
    for lyr in project.mapLayers().values():
        if lyr.name().startswith("Survivors"):
            root.addLayer(lyr)
    legend.setColumnCount(4)
    legend.setSplitLayer(True)          # let categories flow across columns
    legend.setEqualColumnWidth(True)
    legend.setStyle(QgsLegendStyle.Title, _legend_style(size=9, bold=True))
    legend.setStyle(QgsLegendStyle.Subgroup, _legend_style(size=8))
    legend.setStyle(QgsLegendStyle.SymbolLabel, _legend_style(size=7.2))
    legend.attemptMove(QgsLayoutPoint(
        map_x, map_y + map_h + 3, QgsUnitTypes.LayoutMillimeters
    ))
    legend.attemptResize(QgsLayoutSize(map_w - 38, 24,
                                       QgsUnitTypes.LayoutMillimeters))
    legend.setFrameEnabled(False)
    layout.addLayoutItem(legend)

    # ---- Scale bar (subtle, bottom-right) ----
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Line Ticks Up")
    sb.setLinkedMap(m)
    sb.setUnits(QgsUnitTypes.DistanceMiles)
    sb.setUnitLabel(" mi")
    sb.setUnitsPerSegment(2.0)
    sb.setNumberOfSegments(2)
    sb.setNumberOfSegmentsLeft(0)
    sb.setHeight(2.0)
    sb.setFont(QFont("Sans", 7))
    sb.setFontColor(QColor("#444444"))
    sb.update()
    sb.attemptResize(QgsLayoutSize(30, 6, QgsUnitTypes.LayoutMillimeters))
    sb.attemptMove(QgsLayoutPoint(
        map_x + map_w - 32, map_y + map_h + 4,
        QgsUnitTypes.LayoutMillimeters
    ))
    layout.addLayoutItem(sb)

    # ---- Attribution (bottom, full-width, very subtle) ----
    attr = QgsLayoutItemLabel(layout)
    attr.setText(
        "Data: Chicago Data Portal · Census ACS 2023 · Zillow ZORI · "
        "CTA GTFS · OpenStreetMap · Basemap: © CARTO, © OpenStreetMap contributors · "
        "Typology source citations in data/processed/typology.yaml"
    )
    attr.setTextFormat(_text_fmt(family="Sans", size=6.5, color="#888888",
                                 halo=False))
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

        # Add layers bottom → top
        basemap = make_basemap()
        if basemap.isValid():
            project.addMapLayer(basemap)
        project.addMapLayer(make_outline_ghosts())
        project.addMapLayer(make_typology_layer())
        project.addMapLayer(make_rail_layer())

        # Build & export layout
        layout = build_layout(project)

        pdf = OUTPUT / "chicago_editorial.pdf"
        pdf_settings = QgsLayoutExporter.PdfExportSettings()
        pdf_settings.dpi = DPI
        pdf_settings.rasterizeWholeImage = False
        res = QgsLayoutExporter(layout).exportToPdf(str(pdf), pdf_settings)
        if res != QgsLayoutExporter.Success:
            sys.exit(f"[12] PDF export failed: {res}")
        print(f"[12] {pdf}", flush=True)

        png = OUTPUT / "chicago_editorial.png"
        img_settings = QgsLayoutExporter.ImageExportSettings()
        img_settings.dpi = DPI
        res = QgsLayoutExporter(layout).exportToImage(str(png), img_settings)
        if res != QgsLayoutExporter.Success:
            sys.exit(f"[12] PNG export failed: {res}")
        print(f"[12] {png}", flush=True)

    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
