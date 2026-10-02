"""web_search answers from the clock, wttr.in or the calculator only when the
whole query asks for that; everything else reaches the search engine. A URL in
the query is fetched as written."""

from datetime import datetime

import pytest

from backend.api import web_search_api

MUST_SEARCH = [
    "python requests timeout",
    "node runtime error cannot find module",
    "best time to visit japan",
    "Times New Roman font license",
    "kubernetes clock skew",
    "timezone conversion in pandas",
    "what time is it in Tokyo",
    "what is the time complexity of quicksort",
    "x = 5 meaning in algebra",
    "equation of a line y = 2x + 3",
    "what is 2+2 in binary math",
    "LLM temperature setting explained",
    "climate change research 2026",
    "sales forecast model python",
    "how many degrees in a triangle",
    "temperature in celsius vs fahrenheit",
    "how hot is it in a car on a sunny day",
    "stormy weather lyrics",
    "2026-09-30",
    "555-123-4567",
]

MUST_SHORTCUT = [
    ("what time is it", "time_service"),
    ("What time is it?", "time_service"),
    ("what's the current time right now", "time_service"),
    ("current time", "time_service"),
    ("weather in Paris", "weather_service"),
    ("What's the weather like in San Francisco, CA today?", "weather_service"),
    ("current temperature in Boston", "weather_service"),
    ("how cold is it in Moscow?", "weather_service"),
    ("2+2", "math_calculation"),
    ("what is 2 + 2?", "math_calculation"),
    ("calculate (3*4)/2", "math_calculation"),
    ("2^10", "math_calculation"),
]


@pytest.fixture
def routes(monkeypatch):
    """Stub every outbound path and record which one each query took."""
    seen = {"weather": [], "search": [], "fetch": []}

    def weather(location):
        seen["weather"].append(location)
        return {"success": True, "location": location, "temperature_celsius": "18",
                "temperature_fahrenheit": "64", "description": "Clear", "humidity": "50"}

    def search(query, max_results=5):
        seen["search"].append(query)
        return {"success": True, "results": [{"title": "t", "url": "https://example.org/", "snippet": "s"}],
                "snippet": "s", "total_results": 1}

    def fetch(url, query=None, public_only=False):
        seen["fetch"].append(url)
        return {"success": True, "url": url, "title": "page", "description": "", "content": "c"}

    monkeypatch.setattr(web_search_api, "get_weather_info", weather)
    monkeypatch.setattr(web_search_api, "perform_duckduckgo_search", search)
    monkeypatch.setattr(web_search_api, "extract_website_content", fetch)
    return seen


@pytest.mark.parametrize("query", MUST_SEARCH)
def test_ordinary_queries_reach_the_search(routes, query):
    result = web_search_api.enhanced_web_search(query)
    assert result["strategy_used"] == "duckduckgo_search"
    assert routes["search"] == [query]
    assert routes["weather"] == []


@pytest.mark.parametrize("query,strategy", MUST_SHORTCUT)
def test_clear_intents_still_take_the_shortcut(routes, query, strategy):
    result = web_search_api.enhanced_web_search(query)
    assert result["success"] and result["strategy_used"] == strategy
    assert routes["search"] == []


def test_a_long_query_is_always_searched(routes):
    query = "weather in " + "x" * 300
    assert web_search_api.enhanced_web_search(query)["strategy_used"] == "duckduckgo_search"
    assert routes["weather"] == []


def test_the_clock_names_its_zone(routes):
    info = web_search_api.enhanced_web_search("what time is it")["data"]["time_info"]
    assert datetime.fromisoformat(info["timestamp"]).tzinfo is not None
    assert "  on " not in info["local_time"]


def test_the_weather_shortcut_is_given_the_place_only(routes):
    web_search_api.enhanced_web_search("what's the weather like in San Francisco, CA right now?")
    assert routes["weather"] == ["san francisco, ca"]


def test_the_calculator_answers_the_expression(routes):
    result = web_search_api.enhanced_web_search("what is 2 + 2?")
    assert result["data"]["result"] == 4
    assert web_search_api.enhanced_web_search("2^10")["data"]["result"] == 1024


@pytest.mark.parametrize("query,url", [
    ("read https://Site.example/CasePage please", "https://Site.example/CasePage"),
    ("read https://site.example/CasePage.", "https://site.example/CasePage"),
    ("see (https://site.example/Path?q=A),", "https://site.example/Path?q=A"),
    ('"https://site.example/a"', "https://site.example/a"),
    ("look at https://en.wikipedia.org/wiki/Mercury_(planet).", "https://en.wikipedia.org/wiki/Mercury_(planet)"),
    ("[docs](https://site.example/Docs/Intro)", "https://site.example/Docs/Intro"),
    ("open <https://site.example/X>!", "https://site.example/X"),
    ("www.Example.com/About?", "https://www.Example.com/About"),
    ("HTTPS://Example.com/Page", "HTTPS://Example.com/Page"),
])
def test_a_url_in_the_query_is_fetched_as_written(routes, query, url):
    result = web_search_api.enhanced_web_search(query)
    assert result["strategy_used"] == "direct_website"
    assert routes["fetch"] == [url]
