"""Qt model adapters over the core snapshots."""

from typing import TypeAlias

from PySide6.QtCore import QModelIndex, QPersistentModelIndex

Index: TypeAlias = QModelIndex | QPersistentModelIndex
"""What Qt may hand an override: the model methods are called with either."""
