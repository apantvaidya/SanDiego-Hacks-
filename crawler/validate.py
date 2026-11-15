"""Checks on the crawl branch before swapping into main. Empty list means pass.
Row counts, null prices, hours parse, coordinates present, no big drop vs main. Log to crawl_runs."""


def validate(branch_conn, main_conn) -> list[str]:
    raise NotImplementedError
