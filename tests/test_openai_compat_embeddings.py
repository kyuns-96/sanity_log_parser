import pytest

from sanity_log_parser.embeddings.openai_compat import EmbeddingsRequestError
from sanity_log_parser.embeddings.openai_compat import _parse_openai_embeddings_response  # pyright: ignore[reportPrivateUsage]


def test_parse_response_happy_path_returns_ordered_vectors():
    payload = {
        "data": [
            {"index": 1, "embedding": [9, 8.0, 7]},
            {"index": 0, "embedding": [1, 2, 3]},
        ]
    }

    vectors = _parse_openai_embeddings_response(payload, expected_size=2)

    assert vectors == [[1.0, 2.0, 3.0], [9.0, 8.0, 7.0]]


def test_parse_response_raises_for_missing_index():
    payload = {"data": [{"index": 1, "embedding": [1.0]}]}

    with pytest.raises(EmbeddingsRequestError, match="missing indices"):
        _ = _parse_openai_embeddings_response(payload, expected_size=1)


def test_parse_response_raises_for_wrong_size():
    payload = {
        "data": [
            {"index": 0, "embedding": [1.0]},
            {"index": 1, "embedding": [2.0]},
            {"index": 2, "embedding": [3.0]},
        ]
    }

    with pytest.raises(EmbeddingsRequestError, match="response size mismatch"):
        _ = _parse_openai_embeddings_response(payload, expected_size=2)


def test_parse_response_raises_for_nonnumeric_values():
    payload = {"data": [{"index": 0, "embedding": [1.0, "two", 3.0]}]}

    with pytest.raises(EmbeddingsRequestError, match="only numeric values"):
        _ = _parse_openai_embeddings_response(payload, expected_size=1)
