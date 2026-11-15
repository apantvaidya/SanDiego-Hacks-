"""Swap validated locations into main in one transaction. People texting during the swap
see the old data until commit."""


def swap_into_main(branch_conn, main_conn) -> int:
    """Copy main.locations to locations_prev, delete, insert branch rows, commit. Return row count."""
    raise NotImplementedError


def rollback(main_conn) -> None:
    """Restore locations from locations_prev."""
    raise NotImplementedError
