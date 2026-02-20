"""
Email processing module for Fantrax transaction emails
"""

import base64
import json
import os
import re

import requests
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Transaction types
CLAIM = "claim"
DROP = "drop"
TRADE = "trade"
BLOCK = "block"
DRAFT = "draft"
UNKNOWN = "unknown"

EMOJI = {
    CLAIM: "✅",
    DROP: "🚫",
    TRADE: "🔄",
    BLOCK: "🟦",
    DRAFT: "🍺",
}

LEAGUE_NAME = "The Don Orsillo Open"


def _get_gmail_service(gmail_credentials_json: str):
    """Initialize Gmail API service from credentials JSON string"""
    creds = Credentials.from_authorized_user_info(json.loads(gmail_credentials_json))
    return build("gmail", "v1", credentials=creds)


def _detect_transaction_type(subject: str) -> str:
    """Detect transaction type from email subject line"""
    subject = subject.lower()
    if "player(s) claimed" in subject:
        return CLAIM
    elif "free agents added to pool" in subject:
        return DROP
    elif "trade executed" in subject:
        return TRADE
    elif "trade block changed" in subject:
        return BLOCK
    elif "draft pick made" in subject:
        return DRAFT
    return UNKNOWN


def _extract_html_body(message: dict) -> str | None:
    """Extract HTML body from a Gmail message"""
    payload = message.get("payload", {})

    # Check if body is directly in payload
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8")

    # Check multipart parts
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8")

    return None


def _extract_text_content(html: str) -> str:
    """Extract the main text content from Fantrax email HTML"""
    soup = BeautifulSoup(html, "lxml")
    # Fantrax puts the main content in a darkmode-text element (td or div)
    content_div = soup.find(class_="darkmode-text")
    if content_div:
        # Replace <br> tags with newlines before getting text
        for br in content_div.find_all("br"):
            br.replace_with("\n")
        return content_div.get_text(separator=" ").strip()
    return soup.get_text(separator=" ").strip()


def _parse_trade_block(text: str) -> dict | None:
    """Parse trade block email text"""
    team_match = re.search(r"- (.+?) has made changes to the Trade Block", text)
    if not team_match:
        return None

    team = team_match.group(1).strip()
    # Strip league name prefix (e.g. "The Don Orsillo Open - Grand Salamis" → "Grand Salamis")
    if " - " in team:
        team = team.rsplit(" - ", 1)[-1].strip()

    def extract_section(label: str, next_label: str) -> str:
        pattern = rf"{re.escape(label)}:(.*?){re.escape(next_label)}:"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    players_raw = extract_section("Players Offered", "Positions Offered")
    positions_offered = extract_section("Positions Offered", "Stats Offered")
    stats_offered = extract_section("Stats Offered", "Positions Needed")
    positions_needed = extract_section("Positions Needed", "Stats Needed")

    stats_needed_match = re.search(r"Stats Needed:(.*?)Comment:", text, re.DOTALL)
    stats_needed = stats_needed_match.group(1).strip() if stats_needed_match else ""

    comment_match = re.search(r"Comment:(.*?)(?:Note that|$)", text, re.DOTALL)
    comment = comment_match.group(1).strip() if comment_match else ""

    players = [p.strip() for p in re.split(r"\n+|\s{2,}", players_raw) if p.strip()]

    return {
        "team": team,
        "players_offered": players,
        "positions_offered": positions_offered or "(None specified)",
        "stats_offered": stats_offered or "(None specified)",
        "positions_needed": positions_needed or "(None specified)",
        "stats_needed": stats_needed or "(None specified)",
        "comment": comment,
    }


def _parse_trade(html: str) -> dict | None:
    """Parse trade email HTML"""
    soup = BeautifulSoup(html, "lxml")
    content_div = soup.find(class_="darkmode-text")
    if not content_div:
        return None

    # Replace <br> tags with actual newlines before extracting text
    for br in content_div.find_all("br"):
        br.replace_with("\n")

    # Extract text without adding separators between tags
    full_text = content_div.get_text(separator="")

    # Normalize whitespace: collapse multiple spaces on each line
    lines = full_text.split("\n")
    lines = [re.sub(r" +", " ", line.strip()) for line in lines]

    # Collapse consecutive empty lines into one, but keep single blank lines
    result = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                result.append(line)
            prev_empty = True
        else:
            result.append(line)
            prev_empty = False
    full_text = "\n".join(result)

    match = re.search(
        r"has been executed\.\s*(.*?)Note that you can adjust",
        full_text,
        re.DOTALL,
    )
    if not match:
        return None

    details = match.group(1).strip()
    # Clean up "click here" links
    details = re.sub(r"You can click here to go to.*?\n", "", details).strip()
    return {"details": details}


def _parse_claim(text: str) -> dict | None:
    """Parse claim email text"""
    match = re.search(r"\*(.+?)\*\s*\n\s*([\w\s\-']+)\s+([A-Z]+)\s*-\s*([\w,]+.*)", text)
    if match:
        return {
            "team": match.group(1).strip(),
            "player": f"{match.group(2).strip()} {match.group(3).strip()}",
            "details": match.group(4).strip(),
        }
    return {"raw": text}


def _parse_drop(text: str) -> dict | None:
    """Parse drop/free agent email text"""
    match = re.search(
        r"re-entered the\s+player pool as free agents.*?:\s*(.*?)(?:Note that|Thanks|$)",
        text,
        re.DOTALL,
    )
    if match:
        players = [p.strip() for p in match.group(1).strip().split("\n") if p.strip()]
        return {"players": players}
    return {"raw": text}


def _parse_draft(text: str) -> dict | None:
    """Parse draft pick email text"""
    match = re.search(
        r"Round\s+(\d+)\s*,\s*Pick\s+(\d+)\s*:\s*(.+?)\s+was picked by the team\s+(.+?)\s*\.",
        text,
        re.DOTALL,
    )
    if match:
        return {
            "round": re.sub(r"\s+", " ", match.group(1).strip()),
            "pick": re.sub(r"\s+", " ", match.group(2).strip()),
            "player": re.sub(r"\s+", " ", match.group(3).strip()),
            "team": re.sub(r"\s+", " ", match.group(4).strip()),
        }
    return {"raw": text}


def _format_trade_block_message(data: dict) -> str:
    """Format trade block data into Discord message"""
    players_list = "\n".join(data["players_offered"]) if data["players_offered"] else "(none)"
    msg = f"**{data['team']}** updated their trade block\n\n"
    msg += f"**Players Offered:**\n{players_list}\n\n"
    msg += f"**Positions Offered:** {data['positions_offered']}\n"
    msg += f"**Stats Offered:** {data['stats_offered']}\n"
    msg += f"**Positions Needed:** {data['positions_needed']}\n"
    msg += f"**Stats Needed:** {data['stats_needed']}\n"
    if data.get("comment"):
        msg += f"**Comment:** {data['comment']}\n"
    return msg.strip()


def _format_discord_message(transaction_type: str, data: dict) -> str:
    """Format parsed data into Discord message"""
    emoji = EMOJI.get(transaction_type, "📋")
    titles = {
        CLAIM: f"A player has been claimed in {LEAGUE_NAME}!",
        DROP: f"A player has been dropped in {LEAGUE_NAME}!",
        TRADE: f"A trade has been executed in {LEAGUE_NAME}!",
        BLOCK: f"A trade block has been updated in {LEAGUE_NAME}!",
        DRAFT: f"Draft pick made in {LEAGUE_NAME}!",
    }
    title = titles.get(transaction_type, "A transaction occurred!")

    if transaction_type == BLOCK and data:
        details = _format_trade_block_message(data)
    elif transaction_type == TRADE and data:
        details = data.get("details", "")
    elif transaction_type == CLAIM and data:
        if "player" in data:
            details = f"**{data.get('team', '')}** claimed {data['player']}"
        else:
            details = data.get("raw", "")
    elif transaction_type == DROP and data:
        if "players" in data:
            players_list = "\n".join(data["players"])
            details = f"Players dropped to waivers:\n{players_list}"
        else:
            details = data.get("raw", "")
    elif transaction_type == DRAFT and data:
        if "player" in data:
            details = f"**{data['team']}** drafted **{data['player']}**\nRound {data['round']}, Pick {data['pick']}"
        else:
            details = data.get("raw", "")
    else:
        details = ""

    return f"{emoji} **{title}**\n\n{details}\n\n"


def _post_to_discord(webhook_url: str, message: str) -> None:
    """Post a message to a Discord webhook"""
    response = requests.post(webhook_url, json={"content": message})
    response.raise_for_status()


def _get_label_id(service, label_name: str) -> str | None:
    """Get Gmail label ID by name"""
    labels = service.users().labels().list(userId="me").execute()
    return next(
        (l["id"] for l in labels.get("labels", []) if l["name"] == label_name),
        None,
    )


def _process_single_message(service, msg_id: str, discord_transactions_url: str, discord_trade_block_url: str) -> dict | None:
    """Fetch, parse, and post a single Gmail message. Returns result dict or None if skipped."""
    message = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )

    # Extract subject from headers
    headers = message.get("payload", {}).get("headers", [])
    subject = next(
        (h["value"] for h in headers if h["name"].lower() == "subject"),
        "",
    )
    print(f"Processing email: {subject}")

    transaction_type = _detect_transaction_type(subject)
    if transaction_type == UNKNOWN:
        print(f"Skipping unknown transaction type: {subject}")
        return None

    html_body = _extract_html_body(message)
    if not html_body:
        print(f"No HTML body found for message {msg_id}")
        return None

    # Parse based on type
    if transaction_type == BLOCK:
        text = _extract_text_content(html_body)
        data = _parse_trade_block(text)
    elif transaction_type == TRADE:
        data = _parse_trade(html_body)
    elif transaction_type == CLAIM:
        text = _extract_text_content(html_body)
        data = _parse_claim(text)
    elif transaction_type == DROP:
        text = _extract_text_content(html_body)
        data = _parse_drop(text)
    elif transaction_type == DRAFT:
        text = _extract_text_content(html_body)
        data = _parse_draft(text)
    else:
        data = None

    discord_message = _format_discord_message(transaction_type, data)

    # Route to correct webhook
    webhook_url = discord_trade_block_url if transaction_type == BLOCK else discord_transactions_url

    if webhook_url:
        _post_to_discord(webhook_url, discord_message)
        print(f"Posted {transaction_type} to Discord")
        return {"message_id": msg_id, "type": transaction_type}
    else:
        print(f"No webhook URL configured for type: {transaction_type}")
        return None


def process_email(message_data: dict, gmail_credentials_json: str, gcp_project_id: str) -> dict:
    """
    Process a Pub/Sub message containing a Gmail notification.

    Instead of using historyId (which misses batched notifications), we fetch
    all inbox (unarchived) messages with the DOO Transaction label and process
    each one, archiving them afterward.

    Args:
        message_data: The Pub/Sub message data (decoded) — contains emailAddress and historyId
        gmail_credentials_json: Gmail API credentials as JSON string
        gcp_project_id: GCP project ID

    Returns:
        Dict with processing status
    """
    discord_transactions_url = os.environ.get("DISCORD_TRANSACTIONS_WEBHOOK_URL")
    discord_trade_block_url = os.environ.get("DISCORD_TRADE_BLOCK_WEBHOOK_URL")

    print(f"Processing Gmail notification: {message_data}")

    service = _get_gmail_service(gmail_credentials_json)

    # Find the DOO Transaction label ID
    label_id = _get_label_id(service, "DOO Transaction")
    if not label_id:
        print("DOO Transaction label not found")
        return {"status": "error", "reason": "DOO Transaction label not found"}

    # Fetch all inbox (unarchived) messages with the DOO Transaction label
    try:
        list_response = (
            service.users()
            .messages()
            .list(userId="me", labelIds=[label_id, "INBOX"], maxResults=50)
            .execute()
        )
    except Exception as e:
        print(f"Failed to list Gmail messages: {e}")
        return {"status": "error", "reason": str(e)}

    messages = list_response.get("messages", [])
    if not messages:
        print("No unarchived DOO Transaction messages")
        return {"status": "skipped", "reason": "no unarchived messages"}

    print(f"Found {len(messages)} unarchived DOO Transaction message(s)")

    processed = []
    for msg_stub in messages:
        msg_id = msg_stub["id"]
        try:
            result = _process_single_message(service, msg_id, discord_transactions_url, discord_trade_block_url)
            if result:
                processed.append(result)
            # Always archive so we don't reprocess on next notification
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["INBOX"]},
            ).execute()
        except Exception as e:
            print(f"Failed to process message {msg_id}: {e}")
            # Still archive to avoid infinite retry on broken emails
            try:
                service.users().messages().modify(
                    userId="me",
                    id=msg_id,
                    body={"removeLabelIds": ["INBOX"]},
                ).execute()
            except Exception:
                pass

    return {"status": "ok", "processed": processed}
