from __future__ import annotations

import uuid


class UuidIdFactory:
    def new(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"
