"""Row painting: a state dot, the label, and a dimmed detail on the right."""

from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from milonga.core.enums import StateCategory
from milonga.ui.models import Index
from milonga.ui.models.tree import CATEGORY_ROLE, DETAIL_ROLE
from milonga.ui.theme import Tokens, category_color

DOT_SIZE = 9
DOT_MARGIN = 7


def category_of(index: Index) -> StateCategory | None:
    """Qt carries a str-valued enum as a plain string, so read it back by value."""
    try:
        return StateCategory(index.data(CATEGORY_ROLE))
    except ValueError:
        return None


class NodeDelegate(QStyledItemDelegate):
    def __init__(self, tokens: Tokens, parent: Any = None) -> None:
        super().__init__(parent)
        self.tokens = tokens

    def sizeHint(self, option: QStyleOptionViewItem, index: Index) -> QSize:
        size = super().sizeHint(option, index)
        extra = DOT_SIZE + DOT_MARGIN if category_of(index) is not None else 0
        detail = str(index.data(DETAIL_ROLE) or "")
        if detail:
            extra += QFontMetrics(option.font).horizontalAdvance(detail) + 12
        return QSize(size.width() + extra, max(size.height(), 22))

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: Index
    ) -> None:
        self.initStyleOption(option, index)
        widget = option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            option.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, widget)

        painter.save()
        rect = option.rect
        left = rect.left() + 2
        category = category_of(index)
        if category is not None:
            colour = category_color(self.tokens, category)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(colour)
            painter.setPen(
                QPen(QColor(self.tokens.line_2), 1)
                if category is StateCategory.UNKNOWN
                else Qt.PenStyle.NoPen
            )
            top = rect.top() + (rect.height() - DOT_SIZE) // 2
            painter.drawEllipse(left, top, DOT_SIZE, DOT_SIZE)
            left += DOT_SIZE + DOT_MARGIN

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        metrics = QFontMetrics(option.font)
        detail = str(index.data(DETAIL_ROLE) or "")
        detail_width = metrics.horizontalAdvance(detail) + 10 if detail else 0

        label_rect = QRect(
            left, rect.top(), rect.width() - (left - rect.left()) - detail_width, rect.height()
        )
        painter.setPen(QColor(self.tokens.accent if selected else self.tokens.ink))
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            metrics.elidedText(
                str(index.data() or ""), Qt.TextElideMode.ElideMiddle, label_rect.width()
            ),
        )

        if detail:
            painter.setPen(QColor(self.tokens.ink_3))
            painter.drawText(
                QRect(label_rect.right(), rect.top(), detail_width, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                detail,
            )
        painter.restore()
