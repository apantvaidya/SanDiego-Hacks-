"""Neon branch per crawl. Load the crawl into a fresh branch, validate there, then
crawler/swap.py copies rows into main. NEVER promote the branch (it would erase user data
written to main after the branch was created)."""


def create_branch(name: str) -> tuple[str, str]:
    """Return (branch_id, connection_string)."""
    raise NotImplementedError


def delete_branch(branch_id: str) -> None:
    raise NotImplementedError
