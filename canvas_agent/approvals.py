"""Approval gate. Anything that would CHANGE the Canvas account must go through here.

Today nothing in the codebase performs such actions (CanvasClient is read-only and there is no
submit function). If you add one later, it must call `require_approval` and execute only when the
human has approved that exact payload.
"""
from __future__ import annotations

import json

from .storage import Store


class ApprovalRequired(Exception):
    def __init__(self, approval_id: int):
        super().__init__(f"Action needs your approval (id={approval_id}). Run: canvas-agent approve {approval_id}")
        self.approval_id = approval_id


def require_approval(store: Store, action: str, payload: dict, approval_id: int | None = None) -> None:
    if approval_id is not None:
        row = store.get_approval(approval_id)
        if row and row["status"] == "approved" and row["action"] == action and json.loads(row["payload"]) == payload:
            return
    raise ApprovalRequired(store.add_approval(action, payload))
