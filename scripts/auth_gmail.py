"""
Gmail OAuth Flow - Generate tokens for Gmail API access
This script runs the OAuth flow locally and outputs credentials in the format
needed for GCP Secret Manager and the bots.
"""

import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes needed for Gmail API
# gmail.readonly - Read emails (required for push notifications)
# gmail.modify - Modify labels (optional, if you want to mark emails as read)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    """
    Run OAuth flow to generate Gmail API credentials

    Steps:
    1. Place your OAuth client credentials.json in the project root
    2. Run this script from project root: python scripts/auth_gmail.py
    3. Follow the browser flow to authorize
    4. The script will output JSON that you can copy to GCP Secret Manager
    """
    creds = None

    # Get paths relative to project root (parent directory of scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    token_path = os.path.join(project_root, "token.json")
    credentials_path = os.path.join(project_root, "credentials.json")

    # If you've run this before and have a token.json, we can load it
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        print("Found existing token.json")

    # If there are no (valid) credentials, run the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Token expired, refreshing...")
            creds.refresh(Request())
        else:
            # Check if credentials.json exists
            if not os.path.exists(credentials_path):
                print("ERROR: credentials.json not found!")
                print("\nTo get credentials.json:")
                print("1. Go to https://console.cloud.google.com/")
                print("2. Select your project")
                print("3. Go to 'APIs & Services' > 'Credentials'")
                print("4. Click 'Create Credentials' > 'OAuth client ID'")
                print("5. Choose 'Desktop app' as application type")
                print(
                    "6. Download the JSON and save it as 'credentials.json' in the project root"
                )
                print("\nAlso make sure Gmail API is enabled:")
                print("  - Go to 'APIs & Services' > 'Library'")
                print("  - Search for 'Gmail API' and enable it")
                return

            print("Starting OAuth flow...")
            print("A browser window will open. Please authorize the application.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for next time (local file)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
        print(f"\n✅ Token saved to {token_path}")

    # Convert credentials to the format needed for GCP Secret Manager
    # This is the format that the bots expect in GMAIL_CREDENTIALS_JSON
    credentials_dict = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }

    print("\n" + "=" * 60)
    print("SUCCESS! Copy the JSON below to GCP Secret Manager:")
    print("=" * 60)
    print()
    print(json.dumps(credentials_dict, indent=2))
    print()
    print("=" * 60)
    print("\nTo store in GCP Secret Manager, run:")
    print()
    print("echo -n '<paste JSON above>' | \\")
    print("  gcloud secrets create gmail-credentials --data-file=-")
    print()
    print("Or if the secret already exists, update it:")
    print()
    print("echo -n '<paste JSON above>' | \\")
    print("  gcloud secrets versions add gmail-credentials --data-file=-")
    print()
    print("Note: token.json has been saved locally and can be reused.")
    print("      The token will auto-refresh when expired.")


if __name__ == "__main__":
    main()
