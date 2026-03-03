"""
OpenAI-powered helpers — tour link & venue type discovery.

Uses GPT-4o with web search tool to find information about artists.
Extracted from the original ``soundchart.py`` / ``soundchart_live.py``.
"""

import logging
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def _query_openai(api_key: str, prompt: str) -> Optional[str]:
    """Send a web-search prompt to GPT-4o and return the raw text."""
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        return response.output_text.strip()
    except Exception as e:
        logger.warning("OpenAI call failed: %s", e)
        return None


def get_tour_link(artist_name: str, api_key: str) -> Optional[str]:
    """Return the official tour page / website URL for *artist_name*."""
    logger.info("Fetching tour link for %s", artist_name)
    prompt = (
        f'Search the web for the official tour page or official website '
        f'of the artist "{artist_name}". '
        f'Return ONLY the URL. No text, no explanation, no markdown. '
        f'Just the raw URL.'
    )
    url = _query_openai(api_key, prompt)
    if not url:
        return None

    # Auto-prepend https:// for bare domains
    if not url.startswith("http://") and not url.startswith("https://"):
        if "." in url and " " not in url:
            url = "https://" + url

    if url.startswith("http://") or url.startswith("https://"):
        logger.info("Tour link for %s: %s", artist_name, url)
        return url

    logger.warning("Unexpected response for %s: %s", artist_name, url[:120])
    return url  # return anyway — user can review


def get_venue_type(artist_name: str, api_key: str) -> Optional[str]:
    """Return the venue type the artist most frequently performs at."""
    logger.info("Fetching venue type for %s", artist_name)
    prompt = (
        f'Search the web for venue types where "{artist_name}" most '
        f'frequently performs. Return ONLY the venue type. '
        f'No text, no explanation, no markdown. Just the raw venue type.'
    )
    result = _query_openai(api_key, prompt)
    if result:
        logger.info("Venue type for %s: %s", artist_name, result)
    return result
