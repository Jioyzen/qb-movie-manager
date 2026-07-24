"""TMDB client - serial matching with retry, rate limiting, and multi-strategy."""
import time, requests, re
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
                    params=params, timeout=15)
                if r.status_code == 200:
                    return r
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < 2:
                    time.sleep((attempt + 1) * 2)
        return None

    def _pick_best(self, results, query, year):
        if not results:
            return None, "", "", "", ""
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
        release_year = (best.get("release_date") or "")[:4]
        rating = best.get("vote_average", 0) or 0
        return best["id"], best.get("title", ""), best.get("original_title", ""), release_year, round(rating, 1)

    def search(self, query, year=None, language="zh-CN"):
        if not query or not self._api_key:
            return None, "", "", "", ""
        query = self._normalize_query(query)
        if not query:
            return None, "", "", "", ""
        self._rate_limit()
        resp = self._request(query, year=year, language="zh-CN")
        if resp is None:
            return None, "", "", "", ""
        try:
            results = resp.json().get("results", [])
        except Exception:
            return None, "", "", "", ""
        return self._pick_best(results, query, year)

    def fetch_by_id(self, tmdb_id, language="zh-CN"):
        if not tmdb_id or not tmdb_id.isdigit() or not self._api_key:
            return None, "", "", ""
        self._rate_limit()
        try:
            r = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params={"api_key": self._api_key, "language": "zh-CN"}, timeout=15)
            if r.status_code == 200:
                d = r.json()
                rating = round(d.get("vote_average", 0) or 0, 1)
                return d.get("id"), d.get("title", ""), d.get("original_title", ""), rating
        except Exception:
            pass
        return None, "", "", ""

    def _has_meaningful_chinese(self, text):
        cn = "".join(c for c in text if "\u4e00" <= c <= "\u9fff")
        return len(cn) >= 2

    @staticmethod
    def _normalize_query(query):
        q = query
        roman_map = {"Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6",
                     "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10", "Ⅰ": "1",
                     "II": "2", "III": "3", "IV": "4", "VI": "6"}
        for k, v in roman_map.items():
            q = q.replace(k, v)
        q = re.sub(r'\b(V\d+|REPACK|PROPER|EXTENDED|DIRECTORS?\.?CUT|UNRATED|REMUX)\b', '', q, flags=re.I)
        q = re.sub(r'\s+', ' ', q).strip()
        return q

    def match_entry(self, filename, guess_title, guess_year):
        result = {"tmdb_id": "", "tmdb_title_cn": "", "tmdb_title_en": "", "tmdb_year": "", "tmdb_rating": "", "matched_by": ""}
        year = guess_year if guess_year and guess_year.isdigit() else None
        cn = extract_chinese(filename)
        has_cn = self._has_meaningful_chinese(cn)

        eng_title = ""
        if guess_title:
            eng_title = re.sub(r'[\u4e00-\u9fff：、，。！？；："（）]', '', guess_title).strip()
            eng_title = re.sub(r'\s+', ' ', eng_title).strip()

        def _years_to_try(base_year):
            """返回 [精确年份, 年份-1, 年份+1] 列表，自动去重和过滤无效值。"""
            if not base_year or not base_year.isdigit():
                return [base_year]
            years = [base_year]
            y = int(base_year)
            if y > 1900:
                years.append(str(y - 1))
            if y < 2099:
                years.append(str(y + 1))
            return years

        def try_search(q, y):
            tid, tcn, ten, ty, tr = self.search(q, year=y, language="zh-CN")
            return tid, tcn, ten, ty, tr

        def try_search_with_fallback(q, base_year):
            """尝试精确年份搜索，失败后尝试 ±1 年。"""
            for y in _years_to_try(base_year):
                tid, tcn, ten, ty, tr = try_search(q, y)
                if tid:
                    return tid, tcn, ten, ty, tr
            return None, "", "", "", ""

        def _update_result(tid, tcn, ten, ty, tr, suffix):
            result.update({"tmdb_id": str(tid), "tmdb_title_cn": tcn,
                           "tmdb_title_en": ten, "tmdb_year": ty,
                           "tmdb_rating": tr, "matched_by": suffix})

        # Chinese path
        if has_cn:
            for suffix, q in [("cn_zh_year", cn),
                             ("guess_zh_year", guess_title)]:
                if not q:
                    continue
                tid, tcn, ten, ty, tr = try_search_with_fallback(q, year)
                if tid:
                    _update_result(tid, tcn, ten, ty, tr, suffix)
                    return result

        # English path (only with year)
        if year:
            for q in ([eng_title] if eng_title else []):
                if not q:
                    continue
                tid, tcn, ten, ty, tr = try_search_with_fallback(q, year)
                if tid:
                    _update_result(tid, tcn, ten, ty, tr, "en_year")
                    return result

        # Last resort: first segment of filename (only with year)
        if year:
            first_seg = filename.split(".")[0].strip()
            if first_seg and first_seg != guess_title and first_seg != cn:
                tid, tcn, ten, ty, tr = try_search_with_fallback(first_seg, year)
                if tid:
                    _update_result(tid, tcn, ten, ty, tr, "first_seg_year")
                    return result

        return result