"""Config module - load/save JSON configuration with defaults."""
import json
import os

CONFIG_PATH = os.environ.get(
    "QB_MOVIE_CONFIG",
    os.path.join(os.path.dirname(__file__), "data", "config.json"),
)

DEFAULTS = {
    # qBittorrent
    "qb_host": "192.168.2.200",
    "qb_port": 8085,
    "qb_username": "admin",
    "qb_password": "zz0770",
    # TMDB
    "tmdb_api_key": "f71a029311ca7a272c05c7d217bb5c5b",
    "tmdb_rate_limit": 0.2,
    "tmdb_workers": 1,
    # Filter
    "categories": ["4K电影", "高清电影"],
    "min_file_size_mb": 300,
    # SMB
    "smb_host": "192.168.2.200",
    "smb_share": "media",
    "smb_username": "zeng",
    "smb_password": "Zz198903+",
    "smb_mount_point": "/mnt/qb_downloads",
    "qb_download_prefix": "/downloads",
    # Collection strategy: "skip" (保护合集, 不删) | "prefer" (合集优先)
    "collection_strategy": "skip",
    # Priority chain: ordered list of layers
    "priority_layers": ["audio", "subtitle", "source", "resolution", "hdr"],
}

# Password fields that should be masked in API responses
PASSWORD_FIELDS = {"qb_password", "smb_password"}


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
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