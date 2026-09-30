"""Settings (from env / .env) and wallet list (from wallets.json)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# EVM chains we can read. `alchemy` is the Alchemy network slug, `platform` is
# CoinGecko's asset-platform id (used to map contract -> coin), `native` is the
# CoinGecko id of the gas token, `public_rpc` is a keyless fallback.
EVM_CHAINS: dict[str, dict[str, str]] = {
    "ethereum": {"alchemy": "eth-mainnet", "platform": "ethereum", "native": "ethereum",
                 "public_rpc": "https://ethereum-rpc.publicnode.com"},
    "base": {"alchemy": "base-mainnet", "platform": "base", "native": "ethereum",
             "public_rpc": "https://base-rpc.publicnode.com"},
    "arbitrum": {"alchemy": "arb-mainnet", "platform": "arbitrum-one", "native": "ethereum",
                 "public_rpc": "https://arbitrum-one-rpc.publicnode.com"},
    "optimism": {"alchemy": "opt-mainnet", "platform": "optimistic-ethereum", "native": "ethereum",
                 "public_rpc": "https://optimism-rpc.publicnode.com"},
    "polygon": {"alchemy": "polygon-mainnet", "platform": "polygon-pos",
                "native": "polygon-ecosystem-token",
                "public_rpc": "https://polygon-bor-rpc.publicnode.com"},
}

WALLET_TYPES = {"evm", "solana", "bitcoin", "manual"}


def load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need python-dotenv. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    coingecko_api_key: str = ""
    coingecko_plan: str = "demo"
    alchemy_api_key: str = ""
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    bitcoin_api_url: str = "https://mempool.space/api"
    vs_currency: str = "usd"
    wallets_file: Path = field(default_factory=lambda: ROOT / "wallets.json")
    cache_ttl: int = 60
    cache_dir: Path = field(default_factory=lambda: ROOT / ".cache")
    dashboard_password: str = ""  # set -> the whole site sits behind HTTP Basic auth

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        wallets_file = Path(os.getenv("WALLETS_FILE", "wallets.json"))
        if not wallets_file.is_absolute():
            wallets_file = ROOT / wallets_file
        return cls(
            coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
            coingecko_plan=os.getenv("COINGECKO_PLAN", "demo").lower(),
            alchemy_api_key=os.getenv("ALCHEMY_API_KEY", ""),
            solana_rpc_url=os.getenv("SOLANA_RPC_URL") or cls.solana_rpc_url,
            bitcoin_api_url=(os.getenv("BITCOIN_API_URL") or cls.bitcoin_api_url).rstrip("/"),
            vs_currency=os.getenv("VS_CURRENCY", "usd").lower(),
            wallets_file=wallets_file,
            cache_ttl=int(os.getenv("CACHE_TTL", "60")),
            cache_dir=Path(os.getenv("CACHE_DIR") or ROOT / ".cache"),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        )


@dataclass
class Wallet:
    label: str
    type: str
    addresses: list[str] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)  # evm only
    holdings: list[dict] = field(default_factory=list)  # manual only: {"coin", "amount", "chain"?}
    display_chain: str = ""  # manual only: what to show in the "chain" column


@dataclass
class PortfolioConfig:
    wallets: list[Wallet]
    cost_basis: dict[str, float]  # coingecko id -> total amount paid, in vs_currency
    demo: bool = False


class ConfigError(ValueError):
    pass


def parse_config(raw: dict) -> PortfolioConfig:
    wallets: list[Wallet] = []
    labels: set[str] = set()
    for i, w in enumerate(raw.get("wallets", [])):
        wtype = str(w.get("type", "")).lower()
        label = w.get("label") or f"Wallet {i + 1}"
        if wtype not in WALLET_TYPES:
            raise ConfigError(f"{label}: type must be one of {sorted(WALLET_TYPES)}")
        if label in labels:
            raise ConfigError(f"Duplicate wallet label: {label}")
        labels.add(label)

        addresses = list(w.get("addresses") or [])
        if w.get("address"):
            addresses.insert(0, w["address"])
        if wtype != "manual" and not addresses:
            raise ConfigError(f"{label}: needs an 'address' (or 'addresses')")

        chains: list[str] = []
        if wtype == "evm":
            chains = [c.lower() for c in (w.get("chains") or ["ethereum"])]
            unknown = [c for c in chains if c not in EVM_CHAINS]
            if unknown:
                raise ConfigError(f"{label}: unsupported chains {unknown}; use {sorted(EVM_CHAINS)}")

        holdings = []
        if wtype == "manual":
            for h in w.get("holdings", []):
                if "coin" not in h or "amount" not in h:
                    raise ConfigError(f"{label}: manual holdings need 'coin' (CoinGecko id) and 'amount'")
                holdings.append({"coin": str(h["coin"]).lower(), "amount": float(h["amount"]),
                                 "chain": h.get("chain", "")})

        wallets.append(Wallet(label=label, type=wtype, addresses=addresses, chains=chains,
                              holdings=holdings, display_chain=w.get("display_chain", "")))

    cost_basis = {str(k).lower(): float(v) for k, v in (raw.get("cost_basis") or {}).items()
                  if not str(k).startswith("_")}
    return PortfolioConfig(wallets=wallets, cost_basis=cost_basis)


def load_config(settings: Settings) -> PortfolioConfig:
    """Load the user's wallets.json, or fall back to the bundled demo portfolio."""
    if settings.wallets_file.exists():
        return parse_config(json.loads(settings.wallets_file.read_text()))
    cfg = parse_config(json.loads((ROOT / "demo_wallets.json").read_text()))
    cfg.demo = True
    return cfg
