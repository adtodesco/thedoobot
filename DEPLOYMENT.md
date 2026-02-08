# Deployment Guide

Complete guide for deploying TheDooBot to Google Cloud Platform.

## Pre-Deployment Checklist

- [ ] gcloud CLI installed and configured
- [ ] GCP project created with billing enabled
- [ ] Discord webhooks created for:
  - [ ] `#dingers` channel
  - [ ] `#transactions` channel
  - [ ] `#trade-block` channel
- [ ] Gmail OAuth credentials configured (see separate Gmail setup docs)
- [ ] Gmail filter set up to label Fantrax emails as "Fantrax"

## Step 1: Enable Required APIs

```bash
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable pubsub.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable gmail.googleapis.com
```

## Step 2: Create Secrets

Store sensitive values in Secret Manager:

```bash
# Discord webhook for dingers channel
echo -n "YOUR_DINGERS_WEBHOOK_URL" | gcloud secrets create discord-webhook-dingers --data-file=-

# Discord webhook for transactions channel
echo -n "YOUR_TRANSACTIONS_WEBHOOK_URL" | gcloud secrets create discord-webhook-transactions --data-file=-

# Discord webhook for trade-block channel
echo -n "YOUR_TRADE_BLOCK_WEBHOOK_URL" | gcloud secrets create discord-webhook-trade-block --data-file=-

# Gmail OAuth credentials (JSON format)
echo -n '{"token":"...","refresh_token":"...","client_id":"...","client_secret":"..."}' | gcloud secrets create gmail-credentials --data-file=-
```

**To update an existing secret:**
```bash
echo -n "NEW_VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-
```

## Step 3: Grant Compute Engine Service Account Permissions

Cloud Functions Gen 2 uses the Compute Engine default service account for builds. Grant it the necessary permissions:

```bash
# Get your project number
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format="value(projectNumber)")

# Grant required roles to Compute Engine default service account
gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --role=roles/cloudfunctions.developer

gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --role=roles/artifactregistry.writer

gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --role=roles/storage.objectAdmin

gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor

gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --role=roles/logging.logWriter
```

**What these roles do:**
- `cloudfunctions.developer` - Deploy and manage Cloud Functions
- `artifactregistry.writer` - Push container images to Artifact Registry
- `storage.objectAdmin` - Manage Cloud Storage objects used during build
- `secretmanager.secretAccessor` - Read secrets to inject into functions
- `logging.logWriter` - Write build logs to Cloud Logging

**Note:** Cloud Functions Gen 2 uses the Compute Engine default service account (`{PROJECT_NUMBER}-compute@developer.gserviceaccount.com`) for building and deploying functions, not the Cloud Build service account.

## Step 4: Configure Firestore TTL (Optional)

Enable automatic deletion of old video records:

```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=videos \
  --database=dingers \
  --enable-ttl
```

**Note:** Documents are deleted within 24 hours after the `expires_at` timestamp.

## Step 5: Deploy Functions

Once infrastructure setup is complete, run the deployment script:

```bash
./deploy.sh
```

The deployment script will:
1. Deploy both Cloud Functions (dingers and transactions)
2. Create service accounts for Cloud Scheduler and Pub/Sub
3. Grant IAM permissions on both Cloud Functions AND underlying Cloud Run services
4. Set up Cloud Scheduler jobs
5. Configure Pub/Sub topic and subscription

**Important**: Cloud Functions Gen 2 is built on Cloud Run. The deploy script grants IAM permissions on both the Cloud Function and the underlying Cloud Run service to ensure proper authentication.

## Step 6: Generate Gmail OAuth Credentials

Before initializing the Gmail watch, you need to generate OAuth credentials:

```bash
# Install dependencies
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

# Run the OAuth flow (this will open a browser for authorization)
python scripts/auth_gmail.py

# Copy the JSON output and store it in Secret Manager
echo -n '<paste JSON output>' | gcloud secrets create gmail-credentials --data-file=-

# Or if the secret already exists:
echo -n '<paste JSON output>' | gcloud secrets versions add gmail-credentials --data-file=-
```

This creates `token.json` locally and outputs credentials formatted for GCP Secret Manager.

**Note:** You need `credentials.json` from GCP Console first:
1. Go to APIs & Services > Credentials
2. Create OAuth client ID (Desktop app type)
3. Download as `credentials.json` in project root

## Verification

After deployment, verify everything is working:

```bash
# Check function deployments
gcloud functions list --gen2 --region us-east1

# Check scheduler jobs
gcloud scheduler jobs list --location us-east1

# Check Pub/Sub setup
gcloud pubsub topics list
gcloud pubsub subscriptions list

# View function logs
gcloud functions logs read dingers --gen2 --region us-east1 --limit 50
gcloud functions logs read transactions --gen2 --region us-east1 --limit 50
```

**Verification checklist:**
- [ ] Both functions show as ACTIVE
- [ ] Cloud Scheduler jobs are enabled and running
- [ ] Pub/Sub topic and subscription exist
- [ ] Function logs show successful execution (no 403 errors)
- [ ] Discord channels receive test messages

## Post-Deployment

- [ ] Monitor logs for first 24 hours
- [ ] Verify Gmail watch auto-renewal is working (check logs at 2 AM UTC)
- [ ] Set up alerting for Cloud Function errors (optional)
- [ ] Pause dingers during off-season: `gcloud scheduler jobs pause dingers-schedule --location us-east1`

## Troubleshooting

### Dingers function not running

**Symptoms:** No dinger posts to Discord, Cloud Scheduler shows failures

**Solutions:**
- Check Cloud Scheduler job status:
  ```bash
  gcloud scheduler jobs describe dingers-schedule --location us-east1
  ```
- Verify service account permissions (both Cloud Functions and Cloud Run):
  ```bash
  gcloud functions get-iam-policy dingers --gen2 --region us-east1
  gcloud run services get-iam-policy dingers --region us-east1
  ```
- Check function logs for errors:
  ```bash
  gcloud functions logs read dingers --gen2 --region us-east1 --limit 50
  ```

### Transactions function not receiving emails

**Symptoms:** No transaction posts to Discord, Gmail emails not triggering function

**Solutions:**
- Verify Gmail watch is active (check expiration timestamp):
  ```bash
  # Re-run the setup script to check status
  uv run python setup_gmail_watch.py
  ```
- Ensure Fantrax label exists and Gmail filter is working
- Check Pub/Sub topic for messages:
  ```bash
  gcloud pubsub subscriptions pull transactions-pushes-sub --limit 10
  ```
- Verify Pub/Sub service account has invoke permissions:
  ```bash
  gcloud run services get-iam-policy transactions --region us-east1
  ```

### Gmail watch expired

**Symptoms:** No emails being processed after 7 days

**Solutions:**
- Re-run the Gmail watch setup script:
  ```bash
  uv run python setup_gmail_watch.py
  ```
- Verify the transactions watch renewal scheduler is running (runs daily at 2 AM UTC):
  ```bash
  gcloud scheduler jobs describe transactions-watch-renewal-schedule --location us-east1
  ```

### 403 Authentication Errors

**Symptoms:** Function logs show "The request was not authenticated"

**Solutions:**
- This means IAM permissions are missing on the underlying Cloud Run service
- Grant permissions to both Cloud Function AND Cloud Run:
  ```bash
  PROJECT_ID=$(gcloud config get-value project)

  # For scheduler to invoke dingers
  gcloud functions add-iam-policy-binding dingers --gen2 --region us-east1 \
    --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
    --role roles/cloudfunctions.invoker

  gcloud run services add-iam-policy-binding dingers --region us-east1 \
    --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
    --role roles/run.invoker
  ```

## Important Notes

- **Cloud Functions Gen 2 Architecture**: Cloud Functions Gen 2 is built on Cloud Run. When granting IAM permissions for invoking functions, you must grant permissions on BOTH:
  1. The Cloud Function itself (using `gcloud functions add-iam-policy-binding`)
  2. The underlying Cloud Run service (using `gcloud run services add-iam-policy-binding`)

  The `deploy.sh` script handles this automatically for scheduler and pub/sub service accounts.

- **Compute Engine Service Account**: Cloud Functions Gen 2 uses the Compute Engine default service account (`{PROJECT_NUMBER}-compute@developer.gserviceaccount.com`) for building and deploying functions, not the Cloud Build service account.

- **Region**: All resources deployed to `us-east1`

- **Secrets**: Stored in Secret Manager and mounted as environment variables in Cloud Functions

- **Firestore**: Uses nested collection structure: `videos/{date}/videos/{video-hash}`

- **Cloud Scheduler Triggers**: Cloud Scheduler jobs don't appear in the Cloud Functions "Triggers" UI because they make authenticated HTTP requests to the function URL rather than using a platform-native trigger mechanism.

- **Off-Season**: During MLB off-season (November-February), the dingers function will find 0 games and complete successfully. This is expected behavior.
