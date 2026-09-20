"""Spectrum and image views.

Large spectra are decimated for display and the panel says so, because a
silently thinned trace is worse than a slow one.
"""

from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from milonga.core.model import AttributeSpec, AttributeValue
from milonga.ui.theme import Tokens, set_role, theme_signals

pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)

MAX_POINTS = 4000
COLORMAPS = ("viridis", "CET-L16", "CET-L9")


def _colormap() -> Any:
    for name in COLORMAPS:
        try:
            colormap = pg.colormap.get(name)
        except (KeyError, ValueError, FileNotFoundError):
            continue
        if colormap is not None:
            return colormap
    return None


class _PlotBase(QWidget):
    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tokens = tokens
        self.title = QLabel(self)
        set_role(self.title, "role", "title")
        self.detail = QLabel(self)
        set_role(self.detail, "role", "muted")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.detail)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addLayout(header)

    def set_header(self, title: str, detail: str = "") -> None:
        self.title.setText(title)
        self.detail.setText(detail)


class SpectrumView(_PlotBase):
    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=False, y=True, alpha=0.15)
        self.curve = self.plot.plot()
        self._layout.addWidget(self.plot, 1)
        self._spec: AttributeSpec | None = None
        self.restyle()
        theme_signals.changed.connect(self.restyle)

    def restyle(self) -> None:
        tokens = self.tokens
        self.plot.setBackground(tokens.panel)
        for axis in ("left", "bottom"):
            item = self.plot.getAxis(axis)
            item.setPen(pg.mkPen(tokens.line_2))
            item.setTextPen(pg.mkPen(tokens.ink_3))
        self.curve.setPen(pg.mkPen(tokens.accent, width=1.4))

    def set_attribute(self, spec: AttributeSpec) -> None:
        self._spec = spec
        self.plot.setLabel("left", spec.title, units=spec.unit or None)
        self.plot.setLabel("bottom", "index")
        self.set_header(spec.name)
        self.curve.setData([], [])

    def set_value(self, value: AttributeValue) -> None:
        array = np.asarray(value.value, dtype=float).reshape(-1)
        step = max(1, array.size // MAX_POINTS)
        shown = array[::step]
        self.curve.setData(np.arange(0, array.size, step), shown)
        detail = f"{array.size} points"
        if step > 1:
            detail += f" · shown 1:{step}"
        if array.size:
            detail += f" · min {array.min():.4g} · max {array.max():.4g}"
        self.set_header(value.name, detail)

    def clear(self) -> None:
        self.curve.setData([], [])
        self.set_header("", "")


class ImageView(_PlotBase):
    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(tokens, parent)
        self.canvas = pg.GraphicsLayoutWidget()
        self.canvas.setBackground(tokens.panel)
        # a method, not a lambda: Qt drops it when this widget is destroyed
        theme_signals.changed.connect(self.restyle)
        self.view = self.canvas.addViewBox(lockAspect=True, enableMenu=False)
        self.image = pg.ImageItem()
        self.view.addItem(self.image)
        self.bar: Any = None
        colormap = _colormap()
        if colormap is not None:
            self.image.setColorMap(colormap)
            self.bar = pg.ColorBarItem(colorMap=colormap, interactive=False)
            self.bar.setImageItem(self.image)
            self.canvas.addItem(self.bar, row=0, col=1)
        self._layout.addWidget(self.canvas, 1)

    def restyle(self) -> None:
        self.canvas.setBackground(self.tokens.panel)

    def set_attribute(self, spec: AttributeSpec) -> None:
        self.set_header(spec.name)

    def set_value(self, value: AttributeValue) -> None:
        array = np.asarray(value.value, dtype=float)
        if array.ndim != 2:
            array = array.reshape(value.dim_y or 1, -1)
        low, high = float(array.min()), float(array.max())
        if high <= low:
            high = low + 1.0
        self.image.setImage(array, levels=(low, high))
        if self.bar is not None:
            self.bar.setLevels(values=(low, high))
        self.view.autoRange(padding=0)
        self.set_header(
            value.name,
            f"{array.shape[0]} × {array.shape[1]} · "
            f"min {array.min():.4g} · max {array.max():.4g}",
        )

    def clear(self) -> None:
        self.image.clear()
        self.set_header("", "")
