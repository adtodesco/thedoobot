"""
Fantrax Email Parser Bot - Parses Fantrax emails and posts to Discord
Triggered by Gmail Push notifications via Pub/Sub
Also handles daily Gmail watch renewal via Cloud Scheduler
"""

import base64
import json
import os

from .gmail_watch import renew_gmail_watch
from .email import process_email

# Configuration
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GMAIL_CREDENTIALS_JSON = os.environ.get("GMAIL_CREDENTIALS_JSON")
DISCORD_TRANSACTIONS_WEBHOOK_URL = os.environ.get("DISCORD_TRANSACTIONS_WEBHOOK_URL")
DISCORD_TRADE_BLOCK_WEBHOOK_URL = os.environ.get("DISCORD_TRADE_BLOCK_WEBHOOK_URL")


def main(request):
    """
    Cloud Run entry point that handles both:
    1. Gmail watch renewal from Cloud Scheduler (POST with {"action": "renew_watch"})
    2. Email notifications from Pub/Sub (POST with {"message": {...}})
    """
    request_json = request.get_json(silent=True)

    # Route 1: Watch renewal from Cloud Scheduler
    if request_json and request_json.get("action") == "renew_watch":
        try:
            response = renew_gmail_watch(GMAIL_CREDENTIALS_JSON, GCP_PROJECT_ID)
            print(f"Gmail watch renewed. Expiration: {response.get('expiration')}")
            return (json.dumps({"status": "ok", "expiration": response.get("expiration")}), 200)
        except Exception as e:
            print(f"Gmail watch renewal failed: {e}")
            return (json.dumps({"error": str(e)}), 500)

    # Route 2: Email notification from Pub/Sub
    if request_json and "message" in request_json:
        try:
            # Decode Pub/Sub message
            pubsub_message = request_json["message"]
            message_data = {}

            if "data" in pubsub_message:
                decoded_data = base64.b64decode(pubsub_message["data"]).decode("utf-8")
                message_data = json.loads(decoded_data) if decoded_data else {}

            result = process_email(message_data, GMAIL_CREDENTIALS_JSON, GCP_PROJECT_ID)
            return (json.dumps({"status": "ok", "result": result}), 200)
        except Exception as e:
            # Return 200 so Pub/Sub acknowledges the message and stops retrying.
            # Errors are logged for debugging.
            print(f"Email processing failed: {e}")
            import traceback
            traceback.print_exc()
            return (json.dumps({"status": "error", "error": str(e)}), 200)

    # Unknown request type
    return (json.dumps({"error": "Invalid request format"}), 400)


if __name__ == "__main__":
    pass
