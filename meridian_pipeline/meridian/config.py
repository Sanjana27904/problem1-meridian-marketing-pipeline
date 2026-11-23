import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    dsn: str = field(default_factory=lambda: os.getenv("MERIDIAN_DSN", "postgresql://postgres@localhost:5433/meridian"))
    api_base_url: str = field(default_factory=lambda: os.getenv("ADS_API_URL", "http://127.0.0.1:5055"))
    crm_dir: Path = field(default_factory=lambda: Path(os.getenv("CRM_DIR", ROOT / "data" / "crm")))
    orders_path: Path = field(default_factory=lambda: Path(os.getenv("ORDERS_PATH", ROOT / "data" / "orders.json")))
    page_size: int = 100
    max_retries: int = 6            # how many times to retry one failed request
    retry_base_delay: float = 0.5   # first wait; doubles every time: 0.5s, 1s, 2s, 4s ...
    max_reject_rate: float = 0.10   # warn if more than 10% of a source is rejected
