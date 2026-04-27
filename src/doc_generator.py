"""
doc_generator.py
────────────────
Orchestrates the two-pass AI documentation generation strategy:

  Pass 1 — Comprehension
      The model reads raw source code and extracts a structured JSON
      outline (modules, API endpoints, system summary).

  Pass 2 — Writing
      The model receives only the compact outline and produces the final
      markdown files and Mermaid architecture diagram.

The two-pass approach reduces hallucination by separating the "understand
what exists" task from the "write about it" task.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from ollama_client import OllamaClient


@dataclass
class GeneratedDocs:
    """
    Holds all artefacts produced by a single documentation generation run.

    Attributes
    ----------
    overview_md : str
        Full markdown content for docs/generated/README.md.
    architecture_mermaid : str
        Raw Mermaid diagram syntax (no fences) for the architecture overview.
    modules : list[dict]
        Per-module documentation entries, each with `name` and `content_md`.
    outline : dict
        The intermediate JSON outline produced by Pass 1. Stored so callers
        can inspect or log it without re-running the model.
    """
    overview_md: str
    architecture_mermaid: str
    modules: list[dict[str, Any]] = field(default_factory=list)  # type: ignore[assignment]
    outline: dict[str, Any] = field(default_factory=dict)  # type: ignore[assignment]


# ── Prompt templates ──────────────────────────────────────────────────────────
# Kept as module-level constants so they can be imported and inspected
# in tests without instantiating the full generator.

OUTLINE_PROMPT_TEMPLATE = """\
Analyze these source files and output ONLY a JSON object.
No markdown fences, no explanation, no preamble — raw JSON only.

Required schema:
{{
  "modules": [
    {{
      "name": "string",
      "path": "string",
      "purpose": "string (one sentence)",
      "inputs": ["string"],
      "outputs": ["string"],
      "dependencies": ["string"]
    }}
  ],
  "api_endpoints": [
    {{
      "method": "string",
      "path": "string",
      "description": "string"
    }}
  ],
  "system_summary": "string (2-3 sentences, plain English)"
}}

SOURCE FILES:
{source}
"""

DOCS_PROMPT_TEMPLATE = """\
Using the system outline below, generate documentation.
Output ONLY a JSON object — no markdown fences, no explanation.

Required schema:
{{
  "overview_md": "string — full markdown for the top-level README",
  "architecture_mermaid": "string — valid Mermaid flowchart TD syntax only, no fences",
  "modules": [
    {{
      "name": "string",
      "path": "string",
      "content_md": "string — markdown documentation for this module"
    }}
  ]
}}

Rules:
- The Mermaid string must be valid `flowchart TD` or `sequenceDiagram` syntax.
- Never invent module names or endpoints not present in the outline.
- Keep each module content_md under 300 words.

OUTLINE:
{outline}
"""


class DocGenerator:
    """
    Runs the two-pass documentation generation pipeline.

    Parameters
    ----------
    client : OllamaClient
        The Ollama client used to call the language model.
        Injected so it can be replaced with a mock in tests.
    """

    def __init__(self, client: OllamaClient) -> None:
        self.client = client

    def generate(self, source: str) -> GeneratedDocs:
        """
        Run the full two-pass pipeline and return the generated documents.

        Parameters
        ----------
        source : str
            The concatenated source files string produced by FileCollector.

        Returns
        -------
        GeneratedDocs
            All generated artefacts.

        Raises
        ------
        ValueError
            If either pass returns malformed JSON or is missing required fields.
        """
        outline = self._run_pass1(source)
        docs    = self._run_pass2(outline)

        return GeneratedDocs(
            overview_md          = docs.get("overview_md", ""),
            architecture_mermaid = docs.get("architecture_mermaid", ""),
            modules              = docs.get("modules", []),
            outline              = outline,
        )

    # ── Private methods ───────────────────────────────────────────────────────

    def _run_pass1(self, source: str) -> dict[str, Any]:
        """
        Pass 1: send raw source code, receive structured outline JSON.
        """
        prompt = OUTLINE_PROMPT_TEMPLATE.format(source=source)
        try:
            outline = self.client.chat_json(prompt)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Pass 1 produced invalid JSON. "
                f"Try reducing the codebase size or simplifying the prompt. "
                f"Detail: {exc}"
            ) from exc

        self._validate_outline(outline)
        return outline

    def _run_pass2(self, outline: dict[str, Any]) -> dict[str, Any]:
        """
        Pass 2: send the compact outline, receive final documentation JSON.
        """
        prompt = DOCS_PROMPT_TEMPLATE.format(
            outline=json.dumps(outline, indent=2)
        )
        try:
            docs = self.client.chat_json(prompt)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Pass 2 produced invalid JSON. Detail: {exc}"
            ) from exc

        self._validate_docs(docs)
        return docs

    @staticmethod
    def _validate_outline(outline: Any) -> None:
        """Raise ValueError if the outline is missing required top-level keys."""
        if not isinstance(outline, dict):
            raise ValueError(
                f"Pass 1 response must be a JSON object, got: {type(outline)}"
            )
        required = {"modules", "system_summary"}
        missing = required - outline.keys()
        if missing:
            raise ValueError(
                f"Pass 1 response missing required keys: {missing}"
            )

    @staticmethod
    def _validate_docs(docs: Any) -> None:
        """Raise ValueError if the docs output is missing required top-level keys."""
        if not isinstance(docs, dict):
            raise ValueError(
                f"Pass 2 response must be a JSON object, got: {type(docs)}"
            )
        required = {"overview_md", "architecture_mermaid", "modules"}
        missing = required - docs.keys()
        if missing:
            raise ValueError(
                f"Pass 2 response missing required keys: {missing}"
            )