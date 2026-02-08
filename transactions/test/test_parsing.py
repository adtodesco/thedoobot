"""
Tests for Fantrax email parsing logic
Based on Apps Script parsing logic
"""

import pytest
import email
from email import policy
from main import (
    parse_transaction_email,
    parse_trade_block_email,
)


# Sample email HTML content - replace these with your actual Fantrax emails
SAMPLE_TRANSACTION_EMAIL_CLAIM = """
<html>
<body>
<table>
<tr>
<td>Team Name</td>
<td>claimed</td>
<td>Player Name</td>
</tr>
</table>
</body>
</html>
"""

SAMPLE_TRANSACTION_EMAIL_DROP = """
<html>
<body>
<table>
<tr>
<td>Team Name</td>
<td>dropped</td>
<td>Player Name</td>
</tr>
</table>
</body>
</html>
"""

SAMPLE_TRANSACTION_EMAIL_TRADE = """
<html>
<body>
<table>
<tr>
<td>Trade completed</td>
<td>Team A receives Player X, Team B receives Player Y</td>
</tr>
</table>
</body>
</html>
"""


def load_trade_block_email():
    """Load the actual trade-block.eml file"""
    with open("../trade-block.eml", "r", encoding="utf-8", errors="ignore") as f:
        msg = email.message_from_file(f, policy=policy.default)
        # Get HTML content
        html_content = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html_content = part.get_content()
                    break
        else:
            if msg.get_content_type() == "text/html":
                html_content = msg.get_content()

        return html_content if html_content else msg.get_content()


def test_parse_transaction_claim():
    """Test parsing a claim transaction email"""
    result = parse_transaction_email(SAMPLE_TRANSACTION_EMAIL_CLAIM)
    assert result is not None
    assert result["type"] == "claim"
    # Adjust assertions based on your actual email format
    # assert result["team"] == "Team Name"
    # assert result["player"] == "Player Name"


def test_parse_transaction_drop():
    """Test parsing a drop transaction email"""
    result = parse_transaction_email(SAMPLE_TRANSACTION_EMAIL_DROP)
    assert result is not None
    assert result["type"] == "drop"
    # Adjust assertions based on your actual email format


def test_parse_transaction_trade():
    """Test parsing a trade transaction email"""
    result = parse_transaction_email(SAMPLE_TRANSACTION_EMAIL_TRADE)
    assert result is not None
    assert result["type"] == "trade"
    assert result["details"] is not None


def test_parse_trade_block():
    """Test parsing a trade block update email with real email"""
    html_content = load_trade_block_email()
    result = parse_trade_block_email(html_content)

    assert result is not None
    assert result["team"] == "Grand Salamis"
    assert len(result["players_offered"]) == 6
    assert "Contreras, Willson" in result["players_offered"]
    assert "Turner, Trea" in result["players_offered"]
    assert "Smith, Cam" in result["players_offered"]
    assert "Wallner, Matt" in result["players_offered"]
    assert "Miller, Bryce" in result["players_offered"]
    assert "Pfaadt, Brandon" in result["players_offered"]
    assert result["positions_offered"] == "(None specified)"
    assert result["stats_offered"] == "(None specified)"
    assert result["positions_needed"] == "(None specified)"
    assert result["stats_needed"] == "(None specified)"


# To test with your actual emails:
# 1. Copy the HTML content from a real Fantrax email
# 2. Replace the SAMPLE_* constants above
# 3. Run: uv run pytest transactions/test/test_parsing.py -v


if __name__ == "__main__":
    # Quick manual testing
    print("Testing trade block parsing with real email...")
    html_content = load_trade_block_email()
    result = parse_trade_block_email(html_content)
    print(f"Result: {result}")
    if result:
        print(f"\nTeam: {result['team']}")
        print(f"Players Offered: {result['players_offered']}")
        print(f"Positions Offered: {result['positions_offered']}")
        print(f"Stats Offered: {result['stats_offered']}")
        print(f"Positions Needed: {result['positions_needed']}")
        print(f"Stats Needed: {result['stats_needed']}")
        print(f"Comment: {result['comment']}")
