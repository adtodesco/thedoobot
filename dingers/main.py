"""
MLB Dinger Bot - Posts home run videos to Discord #dingers channel
Runs every minute via Cloud Scheduler to check for new HRs
"""

import os
import json
import re
import requests
from datetime import datetime, timezone
from datetime import timedelta
import hashlib
from typing import Dict, List
from tenacity import (
    retry,
    stop_after_attempt,
    wait_fixed,
    retry_if_exception_type,
    retry_if_exception,
)
import statsapi
from google.cloud import firestore


# Configuration
DISCORD_DINGERS_WEBHOOK_URL = os.environ.get("DISCORD_DINGERS_WEBHOOK_URL")
FIRESTORE_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "videos")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "dingers")


def get_firestore_client():
    """Get Firestore client (uses default GCP credentials)."""
    return firestore.Client(database=FIRESTORE_DATABASE)


def _video_doc_id(video_url: str) -> str:
    """Create stable doc ID from video URL."""
    return hashlib.sha256(video_url.encode("utf-8")).hexdigest()


def has_posted_video(client, date_str: str, video_url: str) -> bool:
    """Check if a video URL was already posted for the given date."""
    doc_ref = (
        client.collection(FIRESTORE_COLLECTION)
        .document(date_str)
        .collection("videos")
        .document(_video_doc_id(video_url))
    )
    return doc_ref.get().exists


def mark_video_posted(client, date_str: str, highlight: Dict) -> None:
    """Mark a video URL as posted with TTL for cleanup."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=2)
    video_url = highlight["video_url"]

    doc_ref = (
        client.collection(FIRESTORE_COLLECTION)
        .document(date_str)
        .collection("videos")
        .document(_video_doc_id(video_url))
    )
    doc_ref.set(
        {
            "video_url": video_url,
            "title": highlight.get("title"),
            "description": highlight.get("description"),
            "posted_at": firestore.SERVER_TIMESTAMP,
            "expires_at": expires_at,
        }
    )


def get_games_for_date(date_str: str = None) -> List[Dict]:
    """Fetch MLB games for a given date

    Args:
        date_str: Date in YYYY-MM-DD format. If None, uses today's date.

    Returns:
        List of game dictionaries
    """
    try:
        # Use provided date or default to today
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Fetch schedule for the date
        schedule = statsapi.get(
            "schedule",
            {
                "sportId": 1,  # MLB
                "date": date_str,
                "hydrate": "linescore,game(content(highlights))",
            },
        )

        dates = schedule.get("dates", [])
        if not dates:
            return []
        games = dates[0].get("games", [])
        return games
    except Exception as e:
        print(f"Error fetching games for date {date_str}: {e}")
        return []


def get_todays_games() -> List[Dict]:
    """Fetch today's MLB games from Stats API"""
    return get_games_for_date()


def extract_hr_highlights(game_id: int) -> List[Dict]:
    """Extract home run highlights from a game using statsapi.game_highlights()"""
    highlights = []

    try:
        # Get highlights string from statsapi
        highlights_text = statsapi.game_highlights(game_id)

        # Split by double newline to get individual highlights
        highlight_list = highlights_text.split("\n\n")

        for highlight in highlight_list:
            if not highlight:
                continue

            # Split each highlight by newline to get title, description, video_url
            parts = highlight.split("\n")
            if len(parts) < 3:
                print(f"Warning: Invalid highlight format: {highlight}")
                continue

            title = parts[0]
            description = parts[1]
            video_url = parts[2]

            # Check if it's a home run highlight
            title_lower = title.lower()
            description_lower = description.lower()
            if (
                "homer" in title_lower
                or "home run" in title_lower
                or "homer" in description_lower
                or "home run" in description_lower
            ):
                highlights.append(
                    {
                        "title": title,
                        "description": description,
                        "video_url": video_url,
                        "game_id": game_id,
                    }
                )
    except Exception as e:
        print(f"Error extracting highlights from game {game_id}: {e}")

    return highlights


def _should_retry_http_error(exception):
    """Check if HTTP error is retryable (429, 500, 502, 503, 504)"""
    if not isinstance(exception, requests.exceptions.HTTPError):
        return False
    status_code = exception.response.status_code if exception.response else None
    retryable_status_codes = {429, 500, 502, 503, 504}
    return status_code in retryable_status_codes


@retry(
    stop=stop_after_attempt(3),  # 3 total attempts
    wait=wait_fixed(0.5),  # 500ms delay between attempts
    retry=(
        retry_if_exception_type(requests.exceptions.ConnectionError)
        | retry_if_exception_type(requests.exceptions.Timeout)
        | retry_if_exception(_should_retry_http_error)
    ),
    reraise=True,
)
def post_to_discord(highlight: Dict):
    """Post highlight to Discord webhook with retry logic"""
    if not DISCORD_DINGERS_WEBHOOK_URL:
        print("DISCORD_DINGERS_WEBHOOK_URL not set")
        return False

    # Clean title by removing timestamp pattern (e.g., "(02:34:56)")
    clean_title = re.sub(
        r"\s*\(\d{2}:\d{2}:\d{2}\)\s*$", "", highlight["title"]
    ).strip()

    # Format post content
    content = (
        f"☄️ **{clean_title}**\n{highlight['description']}\n[Video]({highlight['video_url']})"
    )

    payload = {"content": content}

    response = requests.post(DISCORD_DINGERS_WEBHOOK_URL, json=payload)
    response.raise_for_status()


def main(_request):
    """
    Main Cloud Function Gen 2 entry point
    Called by Cloud Scheduler every 5 minutes

    Args:
        _request: Flask request object (unused for scheduled invocations)
    """
    print(f"Checking for dingers at {datetime.now(timezone.utc).isoformat()}")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games = get_todays_games()
    print(f"Found {len(games)} games today ({date_str})")

    firestore_client = get_firestore_client()

    # Check each game for HR highlights
    for game in games:
        game_status = game.get("status", {}).get("abstractGameState", "")

        # Only check live or final games
        if game_status not in ["Live", "Final"]:
            continue

        game_id = game.get("gamePk")
        if not game_id:
            continue

        highlights = extract_hr_highlights(game_id)

        for highlight in highlights:
            # Create unique ID for this highlight using video_url
            video_url = highlight["video_url"]

            if has_posted_video(firestore_client, date_str, video_url):
                continue

            try:
                post_to_discord(highlight)
                mark_video_posted(firestore_client, date_str, highlight)
            except Exception as e:
                print(f"Error posting to Discord: {e}")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": f"Checked {len(games)} games",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
    }


if __name__ == "__main__":
    main()
