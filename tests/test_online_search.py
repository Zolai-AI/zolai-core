"""Tests for online search improvements: ranking, cross-lingual, topical, analytics."""
from __future__ import annotations

import time


class TestComputeRelevance:
    """Test TF-IDF-inspired relevance scoring."""

    def test_exact_match(self) -> None:
        """Exact match returns 1.0."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("pasian", "pasian", exact_match=True)
        assert score == 1.0

    def test_exact_match_case_insensitive(self) -> None:
        """Exact match is case-insensitive."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("Pasian", "pasian")
        assert score == 1.0

    def test_prefix_match(self) -> None:
        """Prefix match returns high score."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("pas", "pasian")
        assert 0.8 <= score <= 1.0

    def test_substring_match(self) -> None:
        """Substring containment returns moderate score."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("sian", "pasian")
        assert 0.3 <= score <= 0.7

    def test_no_match(self) -> None:
        """No match returns 0.0."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("xyz", "pasian")
        assert score == 0.0

    def test_empty_query(self) -> None:
        """Empty query returns 0.0."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("", "pasian")
        assert score == 0.0

    def test_empty_text(self) -> None:
        """Empty text returns 0.0."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("pasian", "")
        assert score == 0.0

    def test_multi_word_query_partial_match(self) -> None:
        """Multi-word query with partial match."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("pasian gam", "pasian in gam a piangsak hi")
        assert score > 0.0

    def test_multi_word_query_exact_match(self) -> None:
        """Multi-word query exact match."""
        from zolai.learning.online_search import OnlineSearch
        score = OnlineSearch._compute_relevance("pasian gam", "pasian gam")
        assert score == 1.0


class TestTTLCache:
    """Test TTL-based cache."""

    def test_cache_get_set(self) -> None:
        """Test basic cache set and get."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch(ttl=60)
        search._cache_set("test_key", {"data": "value"})
        result = search._cache_get("test_key")
        assert result == {"data": "value"}

    def test_cache_expired(self) -> None:
        """Test cache expiry."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch(ttl=0)  # Immediate expiry
        search._cache_set("test_key", {"data": "value"})
        time.sleep(0.01)
        result = search._cache_get("test_key")
        assert result is None

    def test_cache_miss(self) -> None:
        """Test cache miss returns None."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        result = search._cache_get("nonexistent_key")
        assert result is None

    def test_clear_cache(self) -> None:
        """Test cache clearing."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        search._cache_set("key1", "value1")
        search._cache_set("key2", "value2")
        search.clear_cache()
        assert search._cache_get("key1") is None
        assert search._cache_get("key2") is None


class TestAnalytics:
    """Test search analytics tracking."""

    def test_track_analytics(self) -> None:
        """Test analytics tracking."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        search._track_analytics("pasian", "vocabulary", 5)
        analytics = search.get_analytics()
        assert len(analytics) == 1
        assert analytics[0]["query"] == "pasian"
        assert analytics[0]["category"] == "vocabulary"
        assert analytics[0]["count"] == 5

    def test_analytics_sorted_by_count(self) -> None:
        """Test analytics are sorted by count descending."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        search._track_analytics("word1", "vocabulary", 3)
        search._track_analytics("word2", "bible", 10)
        search._track_analytics("word3", "grammar", 7)
        analytics = search.get_analytics()
        counts = [a["count"] for a in analytics]
        assert counts == sorted(counts, reverse=True)

    def test_analytics_cumulative(self) -> None:
        """Test analytics accumulate counts for same query."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        search._track_analytics("pasian", "vocabulary", 3)
        search._track_analytics("pasian", "vocabulary", 5)
        analytics = search.get_analytics()
        assert len(analytics) == 1
        assert analytics[0]["count"] == 8

    def test_analytics_empty(self) -> None:
        """Test analytics returns empty list when nothing tracked."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        assert search.get_analytics() == []


class TestSearchRanked:
    """Test ranked search functionality."""

    def test_search_ranked_returns_list(self) -> None:
        """Test ranked search returns a list."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_ranked("pasian", limit=5)
        assert isinstance(results, list)
        assert len(results) <= 5

    def test_search_ranked_has_relevance(self) -> None:
        """Test ranked search results have relevance scores."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_ranked("pasian", limit=5)
        for r in results:
            assert "relevance" in r
            assert isinstance(r["relevance"], float)

    def test_search_ranked_sorted(self) -> None:
        """Test ranked search results are sorted by relevance."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_ranked("pasian", limit=10)
        if len(results) > 1:
            relevances = [r["relevance"] for r in results]
            assert relevances == sorted(relevances, reverse=True)

    def test_search_ranked_empty_query(self) -> None:
        """Test ranked search with empty query."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_ranked("", limit=5)
        assert isinstance(results, list)


class TestSearchCrossLingual:
    """Test cross-lingual search functionality."""

    def test_cross_lingual_en_to_zo(self) -> None:
        """Test English to Zolai cross-lingual search."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_cross_lingual("god", source_lang="en", target_lang="zo", limit=5)
        assert isinstance(results, list)
        for r in results:
            assert r["source_lang"] == "en"
            assert r["target_lang"] == "zo"
            assert "relevance" in r

    def test_cross_lingual_zo_to_en(self) -> None:
        """Test Zolai to English cross-lingual search."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_cross_lingual("pasian", source_lang="zo", target_lang="en", limit=5)
        assert isinstance(results, list)
        for r in results:
            assert r["source_lang"] == "zo"
            assert r["target_lang"] == "en"

    def test_cross_lingual_has_relevance(self) -> None:
        """Test cross-lingual results have relevance scores."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_cross_lingual("god", source_lang="en", limit=5)
        for r in results:
            assert "relevance" in r
            assert isinstance(r["relevance"], float)


class TestSearchTopical:
    """Test topical Bible search functionality."""

    def test_topical_search_returns_list(self) -> None:
        """Test topical search returns a list."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_topical("love", limit=5)
        assert isinstance(results, list)

    def test_topical_search_has_relevance(self) -> None:
        """Test topical search results have relevance scores."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_topical("creation", limit=5)
        for r in results:
            assert "relevance" in r
            assert isinstance(r["relevance"], float)

    def test_topical_search_sorted(self) -> None:
        """Test topical search results are sorted by relevance."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_topical("prayer", limit=10)
        if len(results) > 1:
            relevances = [r["relevance"] for r in results]
            assert relevances == sorted(relevances, reverse=True)

    def test_topical_search_empty_theme(self) -> None:
        """Test topical search with empty theme."""
        from zolai.learning.online_search import OnlineSearch
        search = OnlineSearch()
        results = search.search_topical("", limit=5)
        assert isinstance(results, list)


class TestGlobalInstance:
    """Test singleton pattern."""

    def test_singleton(self) -> None:
        """Test get_online_search returns same instance."""
        from zolai.learning.online_search import get_online_search
        s1 = get_online_search()
        s2 = get_online_search()
        assert s1 is s2
