#!/usr/bin/env python3
"""Assemble the interactive QGIS project (.qgz) for browsing results.

Layers (bottom → top):
  1. Esri World Imagery basemap (XYZ)
  2. Esri World Hillshade overlay (XYZ, multiply blend, 55% opacity)
  3. All-Chicago CA outlines — gray, no fill, context for filtered-out CAs
  4. Survivors (typology) — categorical fill by archetype, default visible
  5. Survivors (composite score) — graduated fill by composite_score, off by default
  6. CTA rail stops — labeled point markers

Project CRS: EPSG:3435 (Illinois State Plane East, feet) — correct for
distance/area in Chicago. Web Mercator would distort the city's north-south
extent visibly.

Initial extent: Chicago city bbox.

Output:
  output/chicago_neighborhoods.qgz
"""

import sys
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsReferencedRectangle,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsSingleSymbolRenderer,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsRendererCategory,
    QgsRendererRange,
    QgsClassificationQuantile,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsLayerTreeLayer,
)
from qgis.PyQt.QtGui import QFont, QColor, QPainter

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA = PROJECT_DIR / "data" / "processed"
OUTPUT = PROJECT_DIR / "output"

# Chicago bounding box in WGS84 (matches the actual CA extent we loaded)
CHICAGO_BBOX_WGS84 = QgsRectangle(-87.94, 41.64, -87.52, 42.03)
PROJECT_CRS = "EPSG:3435"

# Esri XYZ basemaps (no API key, OK for personal use)
ESRI_IMAGERY = (
    "type=xyz"
    "&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"
    "&zmax=19&zmin=0"
)
ESRI_HILLSHADE = (
    "type=xyz"
    "&url=https://services.arcgisonline.com/arcgis/rest/services/Elevation/World_Hillshade/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"
    "&zmax=18&zmin=0"
)

# Archetype → color mapping (ColorBrewer-ish, distinct hues, decent on satellite)
ARCHETYPE_COLORS = {
    "young-prof-nightlife":    "#e41a1c",   # red
    "established-lakefront":   "#377eb8",   # blue
    "gentrifying-edge":        "#ff7f00",   # orange
    "quiet-artsy":             "#4daf4a",   # green
    "industrial-cool":         "#984ea3",   # purple
    "latino-cultural":         "#ffff33",   # yellow
    "lgbtq-anchor":            "#f781bf",   # pink
    "family-residential":      "#a6d854",   # light green
    "university-adjacent":     "#80b1d3",   # light blue
    "transit-bedroom":         "#bcbd22",   # olive
    "diverse-bohemian":        "#17becf",   # teal
    "insufficient-data":       "#bbbbbb",   # gray
}


def _make_outline_layer(path: Path, name: str) -> QgsVectorLayer:
    """All-CA outline: no fill, dark gray edge."""
    layer = QgsVectorLayer(str(path), name, "ogr")
    sym = QgsFillSymbol.createSimple({
        "color": "0,0,0,0",            # transparent fill
        "outline_color": "60,60,60",
        "outline_width": "0.3",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    layer.setOpacity(0.85)
    return layer


def _make_typology_layer(path: Path) -> QgsVectorLayer:
    """Survivors colored categorically by archetype."""
    uri = f"{path}|layername=typology"
    layer = QgsVectorLayer(uri, "Survivors — typology", "ogr")
    if not layer.isValid():
        return layer

    categories = []
    for arche, color in ARCHETYPE_COLORS.items():
        sym = QgsFillSymbol.createSimple({
            "color": color,
            "outline_color": "20,20,20",
            "outline_width": "0.2",
            "color_opacity": "0.75",
        })
        categories.append(QgsRendererCategory(arche, sym, arche))

    renderer = QgsCategorizedSymbolRenderer("archetype", categories)
    layer.setRenderer(renderer)
    layer.setOpacity(0.85)
    return layer


def _make_composite_layer(path: Path) -> QgsVectorLayer:
    """Survivors colored graduated by composite_score (quantile 5 classes)."""
    uri = f"{path}|layername=survivors"
    layer = QgsVectorLayer(uri, "Survivors — composite score", "ogr")
    if not layer.isValid():
        return layer

    # Build a quantile renderer in code (avoids needing a QML file)
    score_colors = ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"]  # YlOrRd-ish
    ranges = []
    import pandas as pd
    # Compute breakpoints from the data
    import geopandas as gpd
    df = gpd.read_file(path, layer="survivors")
    scores = df["composite_score"].dropna()
    quantiles = scores.quantile([0.0, 0.2, 0.4, 0.6, 0.8, 1.0]).values
    for i, color in enumerate(score_colors):
        lo, hi = quantiles[i], quantiles[i + 1]
        sym = QgsFillSymbol.createSimple({
            "color": color,
            "outline_color": "60,60,60",
            "outline_width": "0.2",
            "color_opacity": "0.85",
        })
        ranges.append(QgsRendererRange(lo, hi, sym, f"{lo:.0f} – {hi:.0f}"))
    renderer = QgsGraduatedSymbolRenderer("composite_score", ranges)
    layer.setRenderer(renderer)
    return layer


def _make_rail_layer(path: Path) -> QgsVectorLayer:
    """CTA rail platforms — small white circles with labels."""
    uri = f"{path}|layername=cta_rail_stops"
    layer = QgsVectorLayer(uri, "CTA L stops", "ogr")
    if not layer.isValid():
        return layer

    sym = QgsMarkerSymbol.createSimple({
        "name": "circle",
        "size": "2.4",
        "color": "255,255,255",
        "outline_color": "0,0,0",
        "outline_width": "0.4",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(sym))

    label_fmt = QgsTextFormat()
    f = QFont("Sans"); f.setBold(True)
    label_fmt.setFont(f); label_fmt.setSize(8)
    label_fmt.setColor(QColor("white"))
    buf = QgsTextBufferSettings(); buf.setEnabled(True); buf.setSize(0.8)
    buf.setColor(QColor(0, 0, 0, 220))
    label_fmt.setBuffer(buf)
    ls = QgsPalLayerSettings()
    ls.fieldName = "stop_name"
    ls.placement = QgsPalLayerSettings.OrderedPositionsAroundPoint
    ls.setFormat(label_fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(ls))
    layer.setLabelsEnabled(True)
    return layer


def main():
    qgs = QgsApplication([], False)
    QgsApplication.setPrefixPath("/usr", True)
    qgs.initQgis()

    try:
        project = QgsProject.instance()
        project.clear()
        proj_crs = QgsCoordinateReferenceSystem(PROJECT_CRS)
        project.setCrs(proj_crs)

        # 1. Basemaps
        imagery = QgsRasterLayer(ESRI_IMAGERY, "Esri World Imagery", "wms")
        if imagery.isValid():
            project.addMapLayer(imagery)
        hillshade = QgsRasterLayer(ESRI_HILLSHADE, "Esri World Hillshade", "wms")
        if hillshade.isValid():
            hillshade.setBlendMode(QPainter.CompositionMode_Multiply)
            hillshade.setOpacity(0.55)
            project.addMapLayer(hillshade)

        # 2. All-CA outlines for context (shows what got filtered out)
        all_cas = _make_outline_layer(DATA / "community_areas.gpkg", "All Community Areas (outline)")
        project.addMapLayer(all_cas)

        # 3. Survivors — composite score (off by default)
        composite = _make_composite_layer(DATA / "survivors.gpkg")
        if composite.isValid():
            project.addMapLayer(composite, addToLegend=True)
            # Find its tree node and toggle off
            node = project.layerTreeRoot().findLayer(composite.id())
            if node:
                node.setItemVisibilityChecked(False)

        # 4. Survivors — typology (default visible)
        typology = _make_typology_layer(DATA / "typology.gpkg")
        if typology.isValid():
            project.addMapLayer(typology)

        # 5. CTA rail stops on top
        rail = _make_rail_layer(DATA / "cta_rail_stops.gpkg")
        if rail.isValid():
            project.addMapLayer(rail)

        # 6. Initial extent
        src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(src_crs, proj_crs, project)
        extent = xform.transformBoundingBox(CHICAGO_BBOX_WGS84)
        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, proj_crs))

        # 7. Save
        OUTPUT.mkdir(parents=True, exist_ok=True)
        out = OUTPUT / "chicago_neighborhoods.qgz"
        if not project.write(str(out)):
            sys.exit(f"[10] Project write failed: {out}")
        print(f"[10] Done → {out}")
        print(f"     Open with: qgis {out}")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
