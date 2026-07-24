"""Config module - load/save configuration with multiple sources.

Priority (highest wins):
1. data/config.json  (runtime persistent config, saved via UI)
2. .env file         (environment variables, never committed)
3. DEFAULTS dict     (built-in defaults)
"""
import json
import os

CONFIG_PATH = os.environ.get(
    "QB_MOVIE_CONFIG",
    os.path.join(os.path.dirname(__file__), "data", "config.json"),
)

# Default values — override via .env or data/config.json
DEFAULTS = {
    # qBittorrent
    "qb_host": "192.168.1.100",
    "qb_port": 8085,
    "qb_username": "admin",
    "qb_password": "",
    # TMDB
    "tmdb_api_key": "",
    "tmdb_rate_limit": 0.2,
    "tmdb_workers": 1,
    # Filter
    "categories": [],
    "min_file_size_mb": 300,
    # SMB
    "smb_host": "192.168.1.100",
    "smb_share": "downloads",
    "smb_username": "",
    "smb_password": "",
    "smb_mount_point": "/mnt/qb_downloads",
    "qb_download_prefix": "/downloads",
    # Collection strategy: "skip" (protect collections) | "prefer" (prefer collection)
    "collection_strategy": "skip",
    # Priority chain: ordered list of layers
    "priority_layers": ["audio", "subtitle", "source", "resolution", "hdr"],
}

# Password fields that should be masked in API responses
PASSWORD_FIELDS = {"qb_password", "smb_password"}

# Mapping from .env keys to config keys
_ENV_MAP = {
    "QB_HOST": "qb_host",
    "QB_PORT": "qb_port",
    "QB_USERNAME": "qb_username",
    "QB_PASSWORD": "qb_password",
    "TMDB_API_KEY": "tmdb_api_key",
    "TMDB_RATE_LIMIT": "tmdb_rate_limit",
    "SMB_HOST": "smb_host",
    "SMB_SHARE": "smb_share",
    "SMB_USERNAME": "smb_username",
    "SMB_PASSWORD": "smb_password",
    "SMB_MOUNT_POINT": "smb_mount_point",
    "QB_DOWNLOAD_PREFIX": "qb_download_prefix",
}

# Keys that should be parsed as integers
_INT_KEYS = {"qb_port", "tmdb_rate_limit", "tmdb_workers", "min_file_size_mb"}


def _load_dotenv():
    """Load .env file from project root into os.environ (simple parser, no deps)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


def _get_env_config() -> dict:
    """Read config values from environment variables (.env or exported)."""
    overrides = {}
    for env_key, cfg_key in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            if cfg_key in _INT_KEYS:
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    pass
            overrides[cfg_key] = val
    return overrides


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        # 1. Load .env file overrides
        _load_dotenv()
        self._data.update(_get_env_config())
        # 2. Load config.json overrides (highest priority)
        self.load()

    @property
    def qb_url(self):
        return f"http://{self._data['qb_host']}:{self._data['qb_port']}"

    def load(self):
        path = os.path.abspath(CONFIG_PATH)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data.update(loaded)
            except Exception as e:
                print(f"[config] Load error: {e}", flush=True)
        else:
            self.save()

    def save(self):
        path = os.path.abspath(CONFIG_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def set_multi(self, mappings: dict):
        self._data.update(mappings)
        self.save()

    def all(self, mask_passwords=True):
        """Return all config, optionally masking password fields."""
        d = dict(self._data)
        if mask_passwords:
            for key in PASSWORD_FIELDS:
                if key in d and d[key]:
                    d[key] = "********"
        return d


config = Config()