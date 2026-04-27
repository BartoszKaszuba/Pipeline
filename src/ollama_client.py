"""
ollama_client.py
────────────────
Thin wrapper around Ollama's local REST API.

Handles the HTTP call, response parsing, and stripping of any
thinking-mode tags that Qwen3.5 emits before its actual output.
"""

import json
import urllib.request
import urllib.error
from typing import Any


class OllamaClient:
    """
    Sends prompts to a locally running Ollama instance and returns
    the model's response as a plain string.

    The client always uses non-streaming mode so the full response
    arrives as a single JSON object rather than a stream of chunks.

    Usage
    -----
        client = OllamaClient(model="qwen35-docs")
        text = client.chat("Summarise this code: ...")
    """

    def __init__(
        self,
        model: str = "qwen35-docs",
        base_url: str = "http://localhost:11434",
        timeout: int = 600,
    ) -> None:
        """
        Parameters
        ----------
        model : str
            The Ollama model name to use (must already be pulled).
        base_url : str
            Base URL of the Ollama server (default: local).
        timeout : int
            Seconds to wait for a response before raising TimeoutError.
            Large codebases can take several minutes on CPU-only hardware.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(self, prompt: str) -> str:
        """
        Send a single-turn prompt and return the model's text response.

        Thinking-mode tags (<think>...</think>) are stripped automatically
        before returning.

        Parameters
        ----------
        prompt : str
            The user message to send.

        Returns
        -------
        str
            The model's response with thinking blocks removed.

        Raises
        ------
        urllib.error.URLError
            If Ollama is unreachable (not running or wrong URL).
        TimeoutError
            If the model takes longer than `timeout` seconds to respond.
        ValueError
            If the response body cannot be parsed as JSON or the expected
            fields are missing.
        """
        payload = self._build_payload(prompt)
        raw_response = self._post(payload)
        return self._extract_text(raw_response)

    def chat_json(self, prompt: str) -> Any:
        """
        Like `chat`, but also parses the response text as JSON.

        Parameters
        ----------
        prompt : str
            The user message. Should instruct the model to respond with
            JSON only (no markdown fences, no preamble).

        Returns
        -------
        Any
            The parsed Python object (dict, list, etc.).

        Raises
        ------
        json.JSONDecodeError
            If the model's response cannot be parsed as JSON even after
            stripping markdown fences.
        """
        text = self.chat(prompt)
        return self._parse_json(text)

    # ── Private helpers ───────────────────────────────────────────────────

    def _build_payload(self, prompt: str) -> bytes:
        return json.dumps({
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

    def _post(self, payload: bytes) -> dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except TimeoutError:
            raise TimeoutError(
                f"Ollama did not respond within {self.timeout}s. "
                "The model may still be loading or the codebase is too large."
            )
        except urllib.error.URLError as exc:
            raise urllib.error.URLError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Is `ollama serve` running? Detail: {exc.reason}"
            )

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        try:
            text = response["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Unexpected Ollama response shape: {response}"
            ) from exc

        # Strip thinking blocks emitted by Qwen3.5 in thinking mode
        if "<think>" in text and "</think>" in text:
            text = text.split("</think>", 1)[-1]

        return text.strip()

    @staticmethod
    def _parse_json(text: str) -> Any:
        # Strip markdown fences the model may emit despite instructions
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove the opening fence (```json or ```) and closing fence
            cleaned = "\n".join(lines[1:-1]).strip()
        return json.loads(cleaned)