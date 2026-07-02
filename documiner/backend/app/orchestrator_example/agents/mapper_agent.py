"""
Mapper agent — Pydantic model for map configuration.

MapConfig is used by ui_agent.py's render_map tool for validation before
streaming a map widget to the frontend.
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class MapConfig(BaseModel):
    """Map configuration output."""
    id: str = Field(description="Map ID (use provided map_id)")
    table: str = Field(description="Table name to get data from")
    type: Literal["points", "heatmap", "choropleth", "hex"] = Field(description="Map type")
    title: str = Field(description="Title for the map")
    label: str = Field(description="Column to be used to label the points/regions")
    weight_column: str = Field(description="Numeric column for intensity/coloring (required for all types)")
    latitude_column: Optional[str] = Field(default=None, description="Latitude column (required for points and heatmap; omit for choropleth and hex)")
    longitude_column: Optional[str] = Field(default=None, description="Longitude column (required for points and heatmap; omit for choropleth and hex)")
    category_column: Optional[str] = Field(default=None, description="Categorical column for color-grouping points (points only, omit for heatmap/choropleth/hex)")
    boundary_type: Optional[Literal["zone", "planning_area", "hex"]] = Field(default=None, description="Boundary type (required for choropleth: zone, planning_area, or hex; omit for points/heatmap/hex)")
    hex_resolution: Optional[Literal[7, 8, 9]] = Field(default=8, description="H3 resolution for hex maps: 7=district (~5 km²), 8=neighbourhood (~0.7 km², default), 9=street (~0.1 km²). hex type only.")
    colors: Optional[Dict[str, str]] = Field(default=None, description="Category→hex for points; {low,medium,high}→hex gradient for heatmap/choropleth/hex. Omit unless user requests.")

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "MapConfig":
        if self.type in ("points", "heatmap"):
            if not self.latitude_column:
                raise ValueError(f"'{self.type}' map requires 'latitude_column'")
            if not self.longitude_column:
                raise ValueError(f"'{self.type}' map requires 'longitude_column'")
        if self.type == "choropleth":
            if not self.boundary_type:
                raise ValueError(
                    "choropleth requires 'boundary_type' — one of: zone, planning_area, hex"
                )
        return self