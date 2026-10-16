"""Explicit, operator-actionable failures shared by dispatch and bridge encoding."""

class CommandRejected(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
