"""Names this code uses that Python 3.10 does not have.

``StrEnum`` is defined here rather than taken from :mod:`enum` on the versions
that ship it, so that every supported version runs the same class: a mixed-in
``str`` enum formats as ``Class.MEMBER`` unless ``__str__`` and ``__format__``
come from ``str``, and enum values end up in user-visible text throughout.
"""

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return str.__str__(self)

    def __format__(self, format_spec: str) -> str:
        return str.__format__(self, format_spec)
