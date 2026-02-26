from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmbeddingsRequestError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingsClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []

        endpoint = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": inputs}
        body = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            details = _read_http_error_body(exc)
            raise EmbeddingsRequestError(
                f"HTTP {exc.code} calling embeddings endpoint: {details}"
            ) from exc
        except URLError as exc:
            raise EmbeddingsRequestError(
                f"Network error calling embeddings endpoint: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise EmbeddingsRequestError(
                f"I/O error calling embeddings endpoint: {exc}"
            ) from exc

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise EmbeddingsRequestError(
                f"Embeddings response is not valid JSON: {exc}"
            ) from exc

        return _parse_openai_embeddings_response(parsed, len(inputs))


def _parse_openai_embeddings_response(
    payload: Any, expected_size: int
) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise EmbeddingsRequestError("Embeddings response must be a JSON object.")

    raw_data = payload.get("data")
    if not isinstance(raw_data, list):
        raise EmbeddingsRequestError(
            "Embeddings response is missing a valid 'data' list."
        )

    indexed_vectors: dict[int, list[float]] = {}
    for entry in raw_data:
        if not isinstance(entry, dict):
            raise EmbeddingsRequestError("Each item in 'data' must be an object.")

        index = entry.get("index")
        embedding = entry.get("embedding")
        if not isinstance(index, int) or index < 0:
            raise EmbeddingsRequestError(
                "Each embedding item requires a non-negative integer 'index'."
            )
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingsRequestError(
                "Each embedding item requires a non-empty 'embedding' list."
            )
        if not all(isinstance(value, (int, float)) for value in embedding):
            raise EmbeddingsRequestError(
                "Embedding vectors must contain only numeric values."
            )

        indexed_vectors[index] = [float(value) for value in embedding]

    if len(indexed_vectors) != expected_size:
        raise EmbeddingsRequestError(
            f"Embeddings response size mismatch: expected {expected_size}, received {len(indexed_vectors)}."
        )

    missing = [index for index in range(expected_size) if index not in indexed_vectors]
    if missing:
        raise EmbeddingsRequestError(f"Embeddings response missing indices: {missing}")

    return [indexed_vectors[index] for index in range(expected_size)]


def _read_http_error_body(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except OSError:
        return str(exc.reason)
    return body or str(exc.reason)
