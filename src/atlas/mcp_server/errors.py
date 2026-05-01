"""Atlas MCP server error types.

AtlasTaskStateError disambiguates the 0-row outcome of atlas.tasks state-transition
UPDATEs (complete/fail) into 4 actionable kinds: not_found / wrong_status /
wrong_owner / race. Callers can branch on `kind` field for actionable error
handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AtlasTaskStateError(Exception):
    """Raised when an atlas.tasks state transition fails to update any row.

    `kind` discriminator: one of {not_found, wrong_status, wrong_owner, race}.
    Other fields populated based on disambiguation context.
    """

    kind: str  # not_found | wrong_status | wrong_owner | race
    message: str
    task_id: Optional[str] = None
    current_status: Optional[str] = None
    expected_status: Optional[str] = None
    actual_owner: Optional[str] = None
    caller_endpoint: Optional[str] = None

    def __str__(self) -> str:
        return (
            f"AtlasTaskStateError[{self.kind}]: {self.message} "
            f"(task_id={self.task_id})"
        )

    def to_dict(self) -> dict:
        """Serializable form for MCP error response."""
        d: dict = {"kind": self.kind, "message": self.message}
        for k in (
            "task_id",
            "current_status",
            "expected_status",
            "actual_owner",
            "caller_endpoint",
        ):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d
