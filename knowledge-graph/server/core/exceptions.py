"""Custom exceptions for knowledge graph operations."""


class KGError(Exception):
    """Base exception for knowledge graph operations."""
    pass


class NodeNotFoundError(KGError):
    """Raised when a node is not found."""
    def __init__(self, level: str, node_id: str):
        self.level = level
        self.node_id = node_id
        super().__init__(f"Node '{node_id}' not found in {level} graph")


class SessionNotFoundError(KGError):
    """Raised when a session is not found."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Unknown session: {session_id}")


class NodeConflictError(KGError):
    """A write built on a stale or partial view of a node was refused.

    Carries the node as it stands, so the caller can merge and write again.
    """
    def __init__(self, level: str, node_id: str, node: dict, reason: str):
        self.level = level
        self.node_id = node_id
        self.node = node
        self.reason = reason
        super().__init__(f"Node '{node_id}' not written: {reason}")


class NodeNotArchivedError(KGError):
    """Raised when trying to recall a non-archived node."""
    def __init__(self, level: str, node_id: str):
        self.level = level
        self.node_id = node_id
        super().__init__(f"Node '{node_id}' is not archived in {level} graph")
