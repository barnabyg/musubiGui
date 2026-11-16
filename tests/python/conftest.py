"""Adds `--shard INDEX/TOTAL` so CI can split the suite across runners."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", metavar="INDEX/TOTAL", help="run only share INDEX (from 1) of TOTAL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = config.getoption("--shard")
    if not shard:
        return
    try:
        index, total = (int(part) for part in shard.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--shard must be INDEX/TOTAL, not {shard!r}") from None
    if not 1 <= index <= total:
        raise pytest.UsageError(f"--shard index must be from 1 to {total}, not {index}")
    # Collection order is the same on every runner and xdist worker. Dealing tests out in turn
    # spreads each file's similar-cost tests across all shards.
    shares: tuple[list[pytest.Item], list[pytest.Item]] = ([], [])
    for position, item in enumerate(items):
        shares[position % total == index - 1].append(item)
    config.hook.pytest_deselected(items=shares[False])
    items[:] = shares[True]
