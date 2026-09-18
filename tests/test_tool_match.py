from types import SimpleNamespace

from core.tool_match import pick_best_tool, tokenize


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("Please, tell me the Bitcoin price now!") == {"bitcoin", "price"}


def test_pick_best_tool_prefers_more_specific_match():
    tools = [
        SimpleNamespace(
            name="generic_price",
            trigger_keywords=["price"],
            description="Look up a generic price",
        ),
        SimpleNamespace(
            name="bitcoin_price",
            trigger_keywords=["bitcoin", "price"],
            description="Look up the current Bitcoin price",
        ),
    ]

    assert pick_best_tool("what is the bitcoin price?", tools).name == "bitcoin_price"
