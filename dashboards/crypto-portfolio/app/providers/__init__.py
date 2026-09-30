"""Balance providers. Each one turns a wallet into a list of Positions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# (coingecko platform id, contract address) -> coingecko coin id, or None if unknown/spam
Resolver = Callable[[str, str], "str | None"]


@dataclass
class Position:
    wallet: str
    chain: str
    coin_id: str
    amount: float
