"""TMDB client - serial matching with retry, rate limiting, and Chinese priority."""

import time
import requests
from config import config
from parser import extract_chinese


class TMDBClient:

    def __init__(self):
        self._api_key = config.get("tmdb_api_key", "")

    def _rate_limit(self):
        """Sleep between requests to respect rate limit."""
        interval = config.get("tmdb_rate_limit", 0.3)
        if interval > 0:
            time.sleep(interval)

    def _request(self, query, year=None, language="zh-CN"):
        """Make a single TMDB search request with retry logic."""
        if not query or not self._api_key:
            return None
        params = {"api_key": self._api_key, "query": query, "language": language}
        if year and year.isdigit():
            params["year"] = year
        for attempt in range(3):
            try:
                r = requests.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params=params,
                    timeout=15,
                )
                if r.status_code == 200:
                    return r
                if r.status_code == 429:
                    # Rate limited - wait longer
                    time.sleep(2 * (attempt + 1))
                    continue
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if attempt < 2:
                    time.sleep((attempt + 1) * 2)
                continue
        return None

    def _pick_best(self, results, query, year):
        """Score results and return the best match."""
        if not results:
            return None, "", ""
        scored = []
        ql = query.lower().strip()
        for m in results:
            score = 0
            m_year = (m.get("release_date") or "")[:4]
            if year and m_year == year:
                score += 100
            m_title = (m.get("title") or "").lower().strip()
            m_orig = (m.get("original_title") or "").lower().strip()
            # Exact title match is strongest signal
            if m_title == ql or m_orig == ql:
                score += 80
            # Partial match
            if (ql in m_title) or (ql in m_orig) or (m_title in ql) or (m_orig in ql):
                score += 30
            # Original title matching English query is good sign
            if m_orig and m_orig == ql:
                score += 50
            scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        best = scored[0][1]
        return best["id"], best.get("title", ""), best.get("original_title", "")

    def search(self, query, year=None, language="zh-CN"):
        """Search TMDB and return (id, cn_title, en_title)."""
        if not query or not self._api_key:
            return None, "", ""
        # Normalize: strip Roman numerals, version markers
        query = self._normalize_query(query)
        if not query:
            return None, "", ""
        self._rate_limit()
        resp = self._request(query, year=year, language=language)
        if resp is None:
            return None, "", ""
        try:
            results = resp.json().get("results", [])
        except Exception:
            return None, "", ""
        return self._pick_best(results, query, year)

    def _has_meaningful_chinese(self, text: str) -> bool:
        """Check if text contains meaningful Chinese (>=2 Chinese chars)."""
        cn = "".join(c for c in text if "\u4e00" <= c <= "\u9fff")
        return len(cn) >= 2

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Clean up query for TMDB search: strip Roman numerals, noise words."""
        import re
        q = query
        # Replace Roman numerals with Arabic equivalents
        roman_map = {"Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6",
                     "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10",
                     "Ⅰ": "1", "II": "2", "III": "3", "IV": "4", "VI": "6"}
        for k, v in roman_map.items():
            q = q.replace(k, v)
        # Remove version markers like V2, Repack, etc.
        q = re.sub(r'\b(V\d+|REPACK|PROPER|EXTENDED|DIRECTORS?\.?CUT|UNRATED|REMUX)\b', '', q, flags=re.I)
        # Remove extra spaces
        q = re.sub(r'\s+', ' ', q).strip()
        return q

    def match_entry(self, filename, guess_title, guess_year):
        """Multi-strategy TMDB match for one entry.

        Strategy order depends on whether we have meaningful Chinese text:
        - Meaningful Chinese found -> zh-CN search first (handles [飞驰人生] etc.)
        - No meaningful Chinese (007, English titles) -> en-US search first
        Always passes year if available for precision.
        """
        result = {
            "tmdb_id": "",
            "tmdb_title_cn": "",
            "tmdb_title_en": "",
            "matched_by": "",
        }
        year = guess_year if guess_year and guess_year.isdigit() else None
        cn = extract_chinese(filename)

        # Determine whether we have meaningful Chinese text
        has_cn = self._has_meaningful_chinese(cn)

        if has_cn:
            # Strategy A1: Chinese text -> zh-CN with year
            tid, tcn, ten = self.search(cn, year=year, language="zh-CN")
            if tid:
                result.update({
                    "tmdb_id": str(tid),
                    "tmdb_title_cn": tcn,
                    "tmdb_title_en": ten,
                    "matched_by": "cn_zh_year",
                })
                return result
            # Strategy A2: Chinese text -> zh-CN without year
            tid, tcn, ten = self.search(cn, year=None, language="zh-CN")
            if tid:
                result.update({
                    "tmdb_id": str(tid),
                    "tmdb_title_cn": tcn,
                    "tmdb_title_en": ten,
                    "matched_by": "cn_zh_no_year",
                })
                return result
            # Strategy A3: guess_title -> zh-CN with year
            if guess_title:
                tid, tcn, ten = self.search(guess_title, year=year, language="zh-CN")
                if tid:
                    result.update({
                        "tmdb_id": str(tid),
                        "tmdb_title_cn": tcn,
                        "tmdb_title_en": ten,
                        "matched_by": "guess_zh_year",
                    })
                    return result
        else:
            # No meaningful Chinese - this is an English-named entry
            # Strategy B1: guess_title (English) -> en-US with year
            if guess_title:
                tid, tcn, ten = self.search(guess_title, year=year, language="en-US")
                if tid:
                    result.update({
                        "tmdb_id": str(tid),
                        "tmdb_title_cn": tcn,
                        "tmdb_title_en": ten,
                        "matched_by": "en_year",
                    })
                    return result
            # Strategy B2: guess_title -> en-US without year
            if guess_title:
                tid, tcn, ten = self.search(guess_title, year=None, language="en-US")
                if tid:
                    result.update({
                        "tmdb_id": str(tid),
                        "tmdb_title_cn": tcn,
                        "tmdb_title_en": ten,
                        "matched_by": "en_no_year",
                    })
                    return result
            # Strategy B3: guess_title -> zh-CN with year
            if guess_title:
                tid, tcn, ten = self.search(guess_title, year=year, language="zh-CN")
                if tid:
                    result.update({
                        "tmdb_id": str(tid),
                        "tmdb_title_cn": tcn,
                        "tmdb_title_en": ten,
                        "matched_by": "guess_zh_year",
                    })
                    return result
            # Strategy B4: guess_title -> zh-CN without year
            if guess_title:
                tid, tcn, ten = self.search(guess_title, year=None, language="zh-CN")
                if tid:
                    result.update({
                        "tmdb_id": str(tid),
                        "tmdb_title_cn": tcn,
                        "tmdb_title_en": ten,
                        "matched_by": "guess_zh_no_year",
                    })
                    return result

        # Final fallbacks for both paths
        # Fallback: guess_title -> en-US with year
        if guess_title and not has_cn:
            tid, tcn, ten = self.search(guess_title, year=year, language="en-US")
            if tid:
                result.update({
                    "tmdb_id": str(tid),
                    "tmdb_title_cn": tcn,
                    "tmdb_title_en": ten,
                    "matched_by": "fallback_en",
                })
                return result

        return result

    def batch_match(self, entries, progress_callback=None):
        """Serialize matching with rate limiting - one at a time, no concurrency."""
        total = len(entries)
        results = list(entries)

        for idx, entry in enumerate(entries):
            md = self.match_entry(
                entry.get("seed_name", ""),
                entry.get("guess_title", ""),
                entry.get("guess_year", ""),
            )
            results[idx].update(md)
            if progress_callback:
                progress_callback(idx, idx + 1, total, md)

        return results
