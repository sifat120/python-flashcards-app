"""AI card generation service — mock by default.

To enable real AI:
  1. pip install openai
  2. Set environment variables:
       AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
       AZURE_OPENAI_API_KEY=<key>
       AZURE_OPENAI_DEPLOYMENT=<deployment-name>
  3. Uncomment the real implementation below and remove the mock.
"""

from __future__ import annotations

import json  # noqa: F401  (used by the real-implementation block below)
import logging
import os

logger = logging.getLogger(__name__)


def is_ai_enabled() -> bool:
    return bool(os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"))


_MOCK_CARDS = [
    {
        "front_text": "[Mock] What is the main topic of the provided passage?",
        "back_text": "This is a mock answer. Configure AZURE_OPENAI_* env vars to get real AI-generated cards from your passage.",
    },
    {
        "front_text": "[Mock] List two key ideas from the passage.",
        "back_text": "Idea 1 — mock. Idea 2 — also mock.",
    },
    {
        "front_text": "[Mock] Define the most important term in the passage.",
        "back_text": "Mock definition: enable real AI to extract actual terms and definitions.",
    },
    {
        "front_text": "[Mock] Why is this topic important?",
        "back_text": "Because mock data proves the end-to-end flow. Swap in Azure OpenAI for a real answer.",
    },
    {
        "front_text": "[Mock] Give one application of the concept.",
        "back_text": "Application: mock. Real AI will tailor this to your passage.",
    },
]


async def generate_cards(passage: str, count: int = 5) -> tuple[list[dict], str]:
    """Generate flashcard Q/A pairs from a passage of study material.

    Returns (cards, source) where source is "mock" or "azure_openai".
    """
    count = max(1, min(count, 20))

    if not is_ai_enabled():
        logger.debug("ai_service: mock generator (AZURE_OPENAI_* not configured)")
        return _MOCK_CARDS[:count], "mock"

    logger.debug("ai_service: real generator (azure_openai), count=%d", count)

    # ── Real implementation (uncomment when ready) ──────────────────────────
    # from openai import AsyncAzureOpenAI
    #
    # client = AsyncAzureOpenAI(
    #     azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    #     api_key=os.environ["AZURE_OPENAI_API_KEY"],
    #     api_version="2024-02-15-preview",
    # )
    # prompt = (
    #     f"Generate {count} high-quality flashcard Q/A pairs from the passage "
    #     f"below. Respond ONLY with a JSON array of objects with keys "
    #     f"'front_text' and 'back_text'.\n\nPassage:\n{passage}"
    # )
    # resp = await client.chat.completions.create(
    #     model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
    #     messages=[{"role": "user", "content": prompt}],
    #     response_format={"type": "json_object"},
    #     max_tokens=1500,
    # )
    # data = json.loads(resp.choices[0].message.content or "{}")
    # cards = data if isinstance(data, list) else data.get("cards", [])
    # return cards[:count], "azure_openai"

    return _MOCK_CARDS[:count], "mock"
