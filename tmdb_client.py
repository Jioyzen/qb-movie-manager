"""TMDB client - serial matching with retry, rate limiting, and multi-strategy."""
import time
import requests
import re
from config import config
from parser import extract_chinese


class TMDBClient:

    def __init__(self):
        self._api_key = config.get("tmdb_api_key", "")

    def _rate_limit(self):
        interval = config.get("tmdb_rate_limit", 0.3)
        if interval > 0:
            time.sleep(interval)

    def _request(self, query, year=None, language="zh-CN"):
        if not query or not self._api_key:
            return None
        params = {"api_key": self._api_key, "query": query, "language": language}
        if year and year.isdigit():
            params["year"] = year
        for attempt in range(3):
            try:
                r = requests.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params=params, timeout=15,
                )
                if r.status_code == 200:
                    return r
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < 2:
                    time.sleep((attempt + 1) * 2)
                continue
        return None

    def _pick_best(self, results, query, year):
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
            if m_title == ql or m_orig == ql:
                score += 80
            if (ql in m_title) or (ql in m_orig) or (m_title in ql) or (m_orig in ql):
                score += 30
            if m_orig and m_orig == ql:
                score += 50
            scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        best = scored[0][1]
        return best["id"], best.get("title", ""), best.get("original_title", "")

    def search(self, query, year=None, language="zh-CN"):
        if not query or not self._api_key:
            return None, "", ""
        query = self._normalize_query(query)
        if not query:
            return None, "", ""
        self._rate_limit()
        # 所有请求强制使用 zh-CN，确保中文名返回
        resp = self._request(query, year=year, language="zh-CN")
        if resp is None:
            return None, "", ""
        try:
            results = resp.json().get("results", [])
        except Exception:
            return None, "", ""
        return self._pick_best(results, query, year)

    def fetch_by_id(self, tmdb_id: str, language="zh-CN"):
        """Fetch movie details by TMDB ID."""
        if not tmdb_id or not tmdb_id.isdigit() or not self._api_key:
            return None, "", ""
        self._rate_limit()
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params={"api_key": self._api_key, "language": "zh-CN"},
                timeout=15,
            )
            if r.status_code == 200:
                d = r.json()
                return d.get("id"), d.get("title", ""), d.get("original_title", "")
        except Exception:
            pass
        return None, "", ""

    def _has_meaningful_chinese(self, text: str) -> bool:
        cn = "".join(c for c in text if "\u4e00" <= c <= "\u9fff")
        return len(cn) >= 2

    @staticmethod
    def _normalize_query(query: str) -> str:
        q = query
        roman_map = {"Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6",
                     "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10",
                     "Ⅰ": "1", "II": "2", "III": "3", "IV": "4", "VI": "6"}
        for k, v in roman_map.items():
            q = q.replace(k, v)
        q = re.sub(r'\b(V\d+|REPACK|PROPER|EXTENDED|DIRECTORS?\.?CUT|UNRATED|REMUX)\b', '', q, flags=re.I)
        q = re.sub(r'\s+', ' ', q).strip()
        return q

    def match_entry(self, filename, guess_title, guess_year):
        """Multi-strategy TMDB match. Falls back through Chinese → English → filename first segment."""
        result = {"tmdb_id": "", "tmdb_title_cn": "", "tmdb_title_en": "", "matched_by": ""}
        year = guess_year if guess_year and guess_year.isdigit() else None
        cn = extract_chinese(filename)
        has_cn = self._has_meaningful_chinese(cn)

        # Extract English title from guess_title (remove Chinese chars)
        eng_title = ""
        if guess_title:
            eng_title = re.sub(r'[\u4e00-\u9fff：、，。！？；："（）]', '', guess_title).strip()
            eng_title = re.sub(r'\s+', ' ', eng_title).strip()

        # ── Chinese path ──
        if has_cn:
            for suffix, q, y in [
                ("cn_zh_year", cn, year),
                ("cn_zh_no_year", cn, None),
                ("guess_zh_year", guess_title, year),
                ("guess_zh_no_year", guess_title, None),
            ]:
                if not q:
                    continue
                tid, tcn, ten = self.search(q, year=y, language="zh-CN")
                if tid:
                    result.update({"tmdb_id": str(tid), "tmdb_title_cn": tcn,
                                   "tmdb_title_en": ten, "matched_by": suffix})
                    return result

        # ── English path ──
        eng_queries = []
        if eng_title:
            eng_queries.append(eng_title)
        if guess_title and guess_title != eng_title and not has_cn:
            eng_queries.append(guess_title)

        for q in eng_queries:
            if not q:
                continue
            for suffix, y in [
                ("en_year", year),
                ("en_no_year", None),
            ]:
                tid, tcn, ten = self.search(q, year=y, language="zh-CN")
                if tid:
                    result.update({"tmdb_id": str(tid), "tmdb_title_cn": tcn,
                                   "tmdb_title_en": ten, "matched_by": suffix})
                    return result

        # ── Last resort: try first segment of filename ──
        first_seg = filename.split(".")[0].strip()
        if first_seg and first_seg != guess_title and first_seg != cn:
            for suffix, y in [
                ("first_seg_year", year),
                ("first_seg_no_year", None),
            ]:
                tid, tcn, ten = self.search(first_seg, year=y, language="zh-CN")
                if tid:
                    result.update({"tmdb_id": str(tid), "tmdb_title_cn": tcn,
                                   "tmdb_title_en": ten, "matched_by": suffix})
                    return result

        return result