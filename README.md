# TheDooBot - Dynasty Fantasy Baseball Discord Bot

A professional-grade Discord bot system for your dynasty fantasy baseball league, featuring real-time MLB home run videos and automated Fantrax transaction parsing.

## Architecture

This project uses a **monorepo structure** with two separate bots:

- **`dingers/`**: Monitors MLB Stats API for home runs and posts videos to Discord #dingers channel
- **`transactions/`**: Parses Fantrax emails via Gmail Push API and posts to Discord #transactions and #trade-block channels

Both bots are deployed as **Google Cloud Run Functions** with the following GCP services:

- **Cloud Run Functions**: Serverless Python execution (2M calls/mo free)
- **Pub/Sub**: Gmail push notification mailbox (10GB/mo free)
- **Cloud Scheduler**: Cron job for dinger bot (3 jobs/mo free)
- **Secret Manager**: Secure credential storage (6 versions free)

## Project Structure

```
/thedoobot
├── dingers/               # MLB HR Tracker
│   ├── main.py                # Python (StatsAPI + Discord)
│   └── requirements.txt       # Dependencies
├── transactions/          # Email Parser
│   ├── main.py                # Python (Gmail API + Regex)
│   └── requirements.txt       # Dependencies
├── scripts/               # Setup utilities
│   └── auth_gmail.py          # OAuth credential generator
├── shared/                # Shared utilities
├── deploy.sh              # Deployment script
├── DEPLOYMENT.md          # Deployment guide
└── README.md              # This file
```

## Setup & Configuration

### 1. Prerequisites

- Google Cloud Platform account with billing enabled
- Discord server with webhook URLs for:
  - `#dingers` channel
  - `#transactions` channel
  - `#trade-block` channel
- Gmail account with Fantrax emails filtered/labeled
- Python 3.12+ installed

### 2. Install and Configure gcloud CLI

If you haven't installed `gcloud` yet, see **`GCLOUD_SETUP.md`** for detailed installation and setup instructions.

Quick setup:
```bash
# Install (macOS with Homebrew)
brew install --cask google-cloud-sdk

# Or download from: https://cloud.google.com/sdk/docs/install

# Authenticate and initialize
gcloud init
```

### 3. GCP Project Setup

```bash
# Set your GCP project (if not set during gcloud init)
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable pubsub.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable gmail.googleapis.com
```

### 4. Create Pub/Sub Topic

```bash
# Create topic for Gmail push notifications
gcloud pubsub topics create fantrax-pushes
```

### 5. Store Secrets in Secret Manager

```bash
# Discord webhooks
echo -n "YOUR_DINGERS_WEBHOOK_URL" | gcloud secrets create discord-webhook-dingers --data-file=-
echo -n "YOUR_TRANSACTIONS_WEBHOOK_URL" | gcloud secrets create discord-webhook-transactions --data-file=-
echo -n "YOUR_TRADE_BLOCK_WEBHOOK_URL" | gcloud secrets create discord-webhook-trade-block --data-file=-

# Gmail credentials (JSON format from OAuth)
echo -n '{"token":"...","refresh_token":"..."}' | gcloud secrets create gmail-credentials --data-file=-
```

### 6. Gmail OAuth Setup

Before deploying, you need Gmail API credentials:

1. **Create OAuth credentials in GCP Console:**
   - Go to APIs & Services > Credentials
   - Create OAuth client ID (Desktop app type)
   - Download as `credentials.json` in project root

2. **Generate OAuth tokens:**
   ```bash
   pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
   python scripts/auth_gmail.py
   ```

3. **Store the JSON output in GCP Secret Manager:**
   ```bash
   echo -n '<paste JSON output>' | gcloud secrets create gmail-credentials --data-file=-
   ```

### 7. Gmail Watch Setup

Before deploying, you need to set up Gmail Push notifications:

1. **Create Gmail OAuth credentials** in GCP Console
2. **Set up Gmail filter** to automatically label Fantrax emails
3. **Run the watch setup script** (see `setup_gmail_watch.py`)

The Gmail watch expires every 7 days. The dinger bot automatically renews it daily.

## Deployment

### Automated Deployment (Recommended)

Deploy both bots with a single command:

```bash
./deploy.sh
```

This idempotent script:
- Deploys both dingers and transactions to Cloud Run
- Creates Cloud Scheduler jobs:
  - Dingers bot: every 5 minutes
  - Gmail watch renewal: daily at 2 AM UTC
- Sets up Pub/Sub for transactions email notifications
- Configures all IAM permissions

You can run it multiple times safely — it will update existing resources.

### Manual Deployment

<details>
<summary>Click to expand manual deployment instructions</summary>

#### Deploy Dinger Bot

```bash
gcloud run deploy dingers \
  --source ./dingers \
  --entry-point check_for_dingers \
  --runtime python312 \
  --region us-central1 \
  --memory 512Mi \
  --timeout 300s \
  --set-secrets DISCORD_DINGERS_WEBHOOK_URL=discord-webhook-dingers:latest \
  --no-allow-unauthenticated
```

#### Create Cloud Scheduler Job for Dinger Bot

```bash
# Get the service URL
DINGER_URL=$(gcloud run services describe dingers --region us-central1 --format 'value(status.url)')

# Create service account for scheduler
gcloud iam service-accounts create scheduler-invoker --display-name "Scheduler Invoker"

# Grant invoke permission
gcloud run services add-iam-policy-binding dingers \
  --region us-central1 \
  --member serviceAccount:scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com \
  --role roles/run.invoker

# Create scheduler job (runs every 5 minutes)
gcloud scheduler jobs create http dingers-schedule \
  --location us-central1 \
  --schedule "*/5 * * * *" \
  --uri "$DINGER_URL" \
  --http-method GET \
  --oidc-service-account-email scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com
```

#### Deploy Fantrax Bot

```bash
gcloud run deploy transactions \
  --source ./transactions \
  --entry-point main \
  --runtime python312 \
  --region us-central1 \
  --memory 512Mi \
  --timeout 300s \
  --set-secrets GMAIL_CREDENTIALS_JSON=gmail-credentials:latest,DISCORD_TRANSACTIONS_WEBHOOK_URL=discord-webhook-transactions:latest,DISCORD_TRADE_BLOCK_WEBHOOK_URL=discord-webhook-trade-block:latest \
  --set-env-vars GCP_PROJECT_ID=$(gcloud config get-value project) \
  --no-allow-unauthenticated
```

#### Create Pub/Sub Subscription for Fantrax Bot

```bash
# Get the service URL
FANTRAX_URL=$(gcloud run services describe transactions --region us-central1 --format 'value(status.url)')

# Create service account for Pub/Sub
gcloud iam service-accounts create pubsub-invoker --display-name "Pub/Sub Invoker"

# Grant invoke permission
gcloud run services add-iam-policy-binding transactions \
  --region us-central1 \
  --member serviceAccount:pubsub-invoker@$(gcloud config get-value project).iam.gserviceaccount.com \
  --role roles/run.invoker

# Create push subscription
gcloud pubsub subscriptions create fantrax-pushes-sub \
  --topic fantrax-pushes \
  --push-endpoint "$FANTRAX_URL" \
  --push-auth-service-account pubsub-invoker@$(gcloud config get-value project).iam.gserviceaccount.com
```

#### Create Gmail Watch Renewal Scheduler

```bash
gcloud scheduler jobs create http transactions-watch-renewal-schedule \
  --location us-central1 \
  --schedule "0 2 * * *" \
  --uri "$FANTRAX_URL" \
  --http-method POST \
  --message-body '{"action":"renew_watch"}' \
  --headers "Content-Type=application/json" \
  --oidc-service-account-email scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com
```

</details>

## Gmail Watch Initialization

After deploying, you need to initialize the Gmail watch. Create a one-time setup script:

```python
# setup_gmail_watch.py
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import os

# Load credentials from Secret Manager or local file
creds_json = os.environ.get('GMAIL_CREDENTIALS_JSON')
creds = Credentials.from_authorized_user_info(json.loads(creds_json))

service = build('gmail', 'v1', credentials=creds)

# Get the Fantrax label ID (create label in Gmail first if needed)
labels = service.users().labels().list(userId='me').execute()
fantrax_label_id = next((l['id'] for l in labels['labels'] if l['name'] == 'Fantrax'), None)

if not fantrax_label_id:
    print("Fantrax label not found. Create it in Gmail first.")
    exit(1)

# Set up watch
PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
response = service.users().watch(
    userId='me',
    body={
        'topicName': f'projects/{PROJECT_ID}/topics/fantrax-pushes',
        'labelIds': [fantrax_label_id]
    }
).execute()

print(f"Watch set up! Expiration: {response.get('expiration')}")
```

Run this script once to initialize the watch. The fantrax bot will automatically renew it daily.

## Maintenance

### The 7-Day Rule

Gmail Push API watch subscriptions expire every 7 days. The fantrax bot automatically renews the watch once per day (at 2 AM UTC) to prevent expiration.

### Pausing During Off-Season

To save resources during the off-season, pause the Cloud Scheduler job:

```bash
gcloud scheduler jobs pause dingers-schedule --location us-central1
```

Resume when season starts:

```bash
gcloud scheduler jobs resume dingers-schedule --location us-central1
```

### Monitoring

View logs for both bots:

```bash
# Dinger bot logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=dingers" --limit 50

# Fantrax bot logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=transactions" --limit 50
```

## Local Development

### Initial Setup

```bash
# Install dependencies for dinger bot
cd dingers
pip install -r requirements.txt

# Install dependencies for transactions bot
cd ../transactions
pip install -r requirements.txt
```

### Testing Dinger Bot

```bash
cd dingers
export DISCORD_DINGERS_WEBHOOK_URL="your_webhook_url"
python main.py
```

### Testing Transactions Bot

```bash
cd transactions
export DISCORD_TRANSACTIONS_WEBHOOK_URL="your_webhook_url"
export DISCORD_TRADE_BLOCK_WEBHOOK_URL="your_webhook_url"
export GMAIL_CREDENTIALS_JSON='{"token":"...","refresh_token":"..."}'
export GCP_PROJECT_ID="your-project-id"
python main.py
```

### Running Gmail OAuth Flow

```bash
# Install dependencies
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

# Run the OAuth flow (opens browser for authorization)
python scripts/auth_gmail.py
```

## Cost Estimation

With free tier limits:
- **Cloud Run**: 2M requests/month free (dingers bot: ~8,640 requests/month at 5-min intervals)
- **Pub/Sub**: 10GB/month free (plenty for email notifications)
- **Cloud Scheduler**: 3 jobs/month free (we use 2: dingers + watch renewal)
- **Secret Manager**: 6 versions free

**Estimated monthly cost**: $0 (within free tier) to ~$5-10 during active season

## Troubleshooting

### Dinger bot not posting videos
- Check MLB Stats API is accessible
- Verify Discord webhook URL is correct
- Check Cloud Scheduler job is running
- Review logs for API errors

### Fantrax bot not receiving emails
- Verify Gmail watch is active (check expiration)
- Ensure Fantrax emails are properly labeled
- Check Pub/Sub topic has messages
- Verify service account permissions

### Gmail watch expired
- The dinger bot should auto-renew, but you can manually run the setup script
- Check that Gmail credentials have not expired

## License

MIT