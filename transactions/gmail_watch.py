"""
Gmail Watch Renewal - Renews Gmail push notification subscription
Gmail watch subscriptions expire every 7 days; this runs daily via Cloud Scheduler.
"""

import json

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def _get_gmail_service(gmail_credentials_json: str):
    """Initialize Gmail API service from credentials JSON string"""
    creds = Credentials.from_authorized_user_info(json.loads(gmail_credentials_json))
    return build("gmail", "v1", credentials=creds)


def renew_gmail_watch(gmail_credentials_json: str, gcp_project_id: str) -> dict:
    """
    Renew Gmail watch subscription.
    Returns the watch response dict on success, raises on failure.
    """
    service = _get_gmail_service(gmail_credentials_json)

    labels = service.users().labels().list(userId="me").execute()
    label_id = next(
        (l["id"] for l in labels.get("labels", []) if l["name"] == "DOO Transaction"),
        None,
    )
    if not label_id:
        raise ValueError("'DOO Transaction' label not found in Gmail — create it first")

    topic_name = f"projects/{gcp_project_id}/topics/transactions-pushes"

    return (
        service.users()
        .watch(
            userId="me",
            body={"topicName": topic_name, "labelIds": [label_id]},
        )
        .execute()
    )
