"""
Charter agent — Pydantic models for chart configuration.

ChartConfig and ChartLayoutConfig are used by ui_agent.py's render_chart tool
for validation before streaming a chart widget to the frontend.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChartLayoutConfig(BaseModel):
    """Chart layout configuration."""
    title: str = Field(description="Chart title")
    xaxis: Dict[str, str] = Field(default_factory=lambda: {"title": "X Axis"}, description="X-axis configuration")
    yaxis: Dict[str, str] = Field(default_factory=lambda: {"title": "Y Axis"}, description="Y-axis configuration")
    fill: bool = Field(default=False, description="Fill area under line (for line charts)")
    smooth: bool = Field(default=False, description="Smooth or straight lines (for line charts)")


class ChartConfig(BaseModel):
    """Chart configuration output."""
    id: str = Field(description="Chart ID (use provided chart_id)")
    type: Literal[
        "bar", "groupedBar", "line", "scatter", "bubble", "pie",
        "doughnut", "nightingale", "radar", "boxplot", "treemap", "heatmap"
    ] = Field(description="Chart type")
    table: str = Field(description="Table name to get data from")
    x: str = Field(description="X-axis field name")
    y: str = Field(description="Y-axis field name")
    colors: Optional[Dict[str, str]] = Field(default=None, description="Map of category/series names to hex colors. Use key 'all' for single color (optional, only upon request)")
    series: Optional[str] = Field(default=None, description="Series field for multi-series charts (strictly categorical columns only)")
    size: Optional[str] = Field(default=None, description="Size field (only for bubble charts)")
    value: Optional[str] = Field(default=None, description="Value field (for heatmap)")
    stacked: bool = Field(default=False, description="Whether to stack bars/areas")
    sizeRange: List[int] = Field(default=[8, 42], description="Size range for bubble charts [min, max]")
    sort: Optional[Literal[
        "default",
        "alphabetical_asc", "alphabetical_desc",
        "numerical_asc", "numerical_desc",
        "date_asc", "date_desc",
        "time_asc", "time_desc",
        "month",
        "day_of_week",
    ]] = Field(default="default", description="Sort order for x-axis values. Use 'default' to keep data order. Choose based on x column type: alphabetical for names/categories, numerical for numeric codes, date/time for timestamps, month for month names, day_of_week for weekday names.")
    layout: ChartLayoutConfig = Field(description="Chart layout configuration")

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "ChartConfig":
        if self.type == "groupedBar" and not self.series:
            raise ValueError(
                "groupedBar requires 'series' — the categorical column that defines each bar within a group"
            )
        if self.type == "bubble" and not self.size:
            raise ValueError(
                "bubble requires 'size' — the numeric column for bubble sizing"
            )
        if self.type == "heatmap" and not self.value:
            raise ValueError(
                "heatmap requires 'value' — the numeric column for color intensity. "
                "'y' is the categorical vertical axis, not the value."
            )
        return self