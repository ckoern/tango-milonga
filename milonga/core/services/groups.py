"""Host grouping, the equivalent of Astor's tree branches.

Groups live in a Tango free property so every client of the control system
sees the same tree. Each entry is ``<group>:<host glob>``; the first matching
entry wins, and unmatched hosts fall into :data:`DEFAULT_GROUP`.
"""

import fnmatch
from collections.abc import Sequence
from dataclasses import dataclass

from milonga.core.backend.protocol import TangoBackend
from milonga.core.enums import PropertyScope
from milonga.core.errors import TangoError
from milonga.core.model import PropertyEntry

GROUP_OBJECT = "Milonga"
GROUP_PROPERTY = "HostGroups"
DEFAULT_GROUP = "Hosts"


@dataclass(frozen=True, slots=True)
class GroupRule:
    group: str
    pattern: str

    @classmethod
    def parse(cls, text: str) -> "GroupRule | None":
        group, separator, pattern = text.partition(":")
        if not separator or not group.strip() or not pattern.strip():
            return None
        return cls(group.strip(), pattern.strip())

    def __str__(self) -> str:
        return f"{self.group}:{self.pattern}"


class HostGroups:
    def __init__(self, rules: Sequence[GroupRule] = ()) -> None:
        self.rules = tuple(rules)

    def group_of(self, host: str) -> str:
        for rule in self.rules:
            if fnmatch.fnmatch(host, rule.pattern):
                return rule.group
        return DEFAULT_GROUP

    def arrange(self, hosts: Sequence[str]) -> dict[str, list[str]]:
        """Group hosts, keeping rule order and sorting hosts inside each group."""
        grouped: dict[str, list[str]] = {rule.group: [] for rule in self.rules}
        for host in sorted(hosts):
            grouped.setdefault(self.group_of(host), []).append(host)
        return {group: members for group, members in grouped.items() if members}

    def as_property(self) -> PropertyEntry:
        return PropertyEntry(
            GROUP_PROPERTY,
            tuple(str(rule) for rule in self.rules),
            PropertyScope.FREE,
            GROUP_OBJECT,
        )


async def load_host_groups(backend: TangoBackend) -> HostGroups:
    try:
        entries = await backend.get_properties(GROUP_OBJECT, [GROUP_PROPERTY])
    except TangoError:
        return HostGroups()
    values = entries[0].values if entries else ()
    rules = [GroupRule.parse(value) for value in values]
    return HostGroups([rule for rule in rules if rule is not None])
