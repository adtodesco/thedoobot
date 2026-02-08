#!/bin/bash
set -e

# Idempotent deployment script for TheDooBot
# Deploys both dingers and transactions functions to Google Cloud Run
# Creates Cloud Scheduler jobs for both services

echo "🚀 Starting TheDooBot deployment..."

# Get project ID
PROJECT_ID=$(gcloud config get-value project)
REGION="us-east1"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: No GCP project configured. Run 'gcloud config set project YOUR_PROJECT_ID'"
    exit 1
fi

echo "📦 Project: $PROJECT_ID"
echo "🌎 Region: $REGION"
echo ""

# ============================================================================
# 1. Deploy dingers function
# ============================================================================
echo "⚾ Deploying dingers..."
gcloud functions deploy dingers \
  --gen2 \
  --runtime python312 \
  --entry-point main \
  --source ./dingers \
  --region $REGION \
  --memory 512Mi \
  --timeout 300s \
  --trigger-http \
  --set-secrets DISCORD_DINGERS_WEBHOOK_URL=discord-webhook-dingers:latest \
  --no-allow-unauthenticated \
  --quiet

DINGER_URL=$(gcloud functions describe dingers --gen2 --region $REGION --format 'value(serviceConfig.uri)')
echo "✅ Dingers function deployed: $DINGER_URL"
echo ""

# ============================================================================
# 2. Deploy transactions function
# ============================================================================
echo "📧 Deploying transactions..."
gcloud functions deploy transactions \
  --gen2 \
  --runtime python312 \
  --entry-point main \
  --source ./transactions \
  --region $REGION \
  --memory 512Mi \
  --timeout 300s \
  --trigger-http \
  --set-secrets GMAIL_CREDENTIALS_JSON=gmail-credentials:latest,DISCORD_TRANSACTIONS_WEBHOOK_URL=discord-webhook-transactions:latest,DISCORD_TRADE_BLOCK_WEBHOOK_URL=discord-webhook-trade-block:latest \
  --set-env-vars GCP_PROJECT_ID=$PROJECT_ID \
  --no-allow-unauthenticated \
  --quiet

TRANSACTIONS_URL=$(gcloud functions describe transactions --gen2 --region $REGION --format 'value(serviceConfig.uri)')
echo "✅ Transactions function deployed: $TRANSACTIONS_URL"
echo ""

# ============================================================================
# 3. Create Service Account for Cloud Scheduler (idempotent)
# ============================================================================
echo "🔐 Setting up service account..."
if gcloud iam service-accounts describe scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com &>/dev/null; then
    echo "   Service account already exists"
else
    gcloud iam service-accounts create scheduler-invoker \
      --display-name "Scheduler Invoker" \
      --quiet
    echo "   Service account created"
fi
echo ""

# ============================================================================
# 4. Grant IAM Permissions
# ============================================================================
echo "🔑 Granting IAM permissions..."

# Grant invoke permission to dingers (both Cloud Functions and underlying Cloud Run)
gcloud functions add-iam-policy-binding dingers \
  --gen2 \
  --region $REGION \
  --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/cloudfunctions.invoker \
  --quiet

gcloud run services add-iam-policy-binding dingers \
  --region $REGION \
  --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/run.invoker \
  --quiet

# Grant invoke permission to transactions (both Cloud Functions and underlying Cloud Run)
gcloud functions add-iam-policy-binding transactions \
  --gen2 \
  --region $REGION \
  --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/cloudfunctions.invoker \
  --quiet

gcloud run services add-iam-policy-binding transactions \
  --region $REGION \
  --member serviceAccount:scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/run.invoker \
  --quiet

echo "✅ IAM permissions configured"
echo ""

# ============================================================================
# 5. Create/Update Cloud Scheduler Jobs
# ============================================================================
echo "⏰ Setting up Cloud Scheduler jobs..."

# Dingers function scheduler (every 5 minutes)
if gcloud scheduler jobs describe dingers-schedule --location $REGION &>/dev/null; then
    echo "   Updating dingers-schedule..."
    gcloud scheduler jobs update http dingers-schedule \
      --location $REGION \
      --schedule "*/5 * * * *" \
      --uri "$DINGER_URL" \
      --http-method GET \
      --oidc-service-account-email scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
else
    echo "   Creating dingers-schedule..."
    gcloud scheduler jobs create http dingers-schedule \
      --location $REGION \
      --schedule "*/5 * * * *" \
      --uri "$DINGER_URL" \
      --http-method GET \
      --oidc-service-account-email scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
fi
echo "   ✅ Dingers scheduler: every 5 minutes"

# Transactions function renewal scheduler (every 24 hours at 2 AM UTC)
if gcloud scheduler jobs describe transactions-watch-renewal-schedule --location $REGION &>/dev/null; then
    echo "   Updating transactions-watch-renewal-schedule..."
    gcloud scheduler jobs update http transactions-watch-renewal-schedule \
      --location $REGION \
      --schedule "0 2 * * *" \
      --uri "$TRANSACTIONS_URL" \
      --http-method POST \
      --message-body '{"action":"renew_watch"}' \
      --update-headers "Content-Type=application/json" \
      --oidc-service-account-email scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
else
    echo "   Creating transactions-watch-renewal-schedule..."
    gcloud scheduler jobs create http transactions-watch-renewal-schedule \
      --location $REGION \
      --schedule "0 2 * * *" \
      --uri "$TRANSACTIONS_URL" \
      --http-method POST \
      --message-body '{"action":"renew_watch"}' \
      --headers "Content-Type=application/json" \
      --oidc-service-account-email scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
fi
echo "   ✅ Transactions watch renewal: daily at 2 AM UTC"
echo ""

# ============================================================================
# 6. Pub/Sub Setup for Transactions Functions
# ============================================================================
echo "📬 Setting up Pub/Sub for transactions..."

# Create topic if it doesn't exist
if gcloud pubsub topics describe transactions-pushes &>/dev/null; then
    echo "   Pub/Sub topic already exists"
else
    gcloud pubsub topics create transactions-pushes --quiet
    echo "   Pub/Sub topic created"
fi

# Create service account for Pub/Sub if needed
if gcloud iam service-accounts describe pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com &>/dev/null; then
    echo "   Pub/Sub service account already exists"
else
    gcloud iam service-accounts create pubsub-invoker \
      --display-name "Pub/Sub Invoker" \
      --quiet
    echo "   Pub/Sub service account created"
fi

# Grant invoke permission (both Cloud Functions and underlying Cloud Run)
gcloud functions add-iam-policy-binding transactions \
  --gen2 \
  --region $REGION \
  --member serviceAccount:pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/cloudfunctions.invoker \
  --quiet

gcloud run services add-iam-policy-binding transactions \
  --region $REGION \
  --member serviceAccount:pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --role roles/run.invoker \
  --quiet

# Grant Gmail API permission to publish to our Pub/Sub topic
# Gmail uses gmail-api-push@system.gserviceaccount.com to send push notifications
gcloud pubsub topics add-iam-policy-binding transactions-pushes \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher \
  --quiet

# Create or update push subscription
if gcloud pubsub subscriptions describe transactions-pushes-sub &>/dev/null; then
    echo "   Pub/Sub subscription already exists"
    # Update the push endpoint in case URL changed
    gcloud pubsub subscriptions update transactions-pushes-sub \
      --push-endpoint "$TRANSACTIONS_URL" \
      --push-auth-service-account pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
else
    gcloud pubsub subscriptions create transactions-pushes-sub \
      --topic transactions-pushes \
      --push-endpoint "$TRANSACTIONS_URL" \
      --push-auth-service-account pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
      --quiet
    echo "   Pub/Sub subscription created"
fi

echo "✅ Pub/Sub configured"
echo ""

# ============================================================================
# Summary
# ============================================================================
echo "════════════════════════════════════════════════════════════════"
echo "🎉 Deployment Complete!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "📊 Functions:"
echo "   Dingers:  $DINGER_URL"
echo "   Transactions:  $TRANSACTIONS_URL"
echo ""
echo "⏰ Schedules:"
echo "   Dingers:  Every 5 minutes"
echo "   Transactions Watch Renewal: Daily at 2 AM UTC"
echo ""
echo "📝 Next steps:"
echo "   1. Ensure Gmail watch is initialized (run setup_gmail_watch.py if needed)"
echo "   2. Monitor logs: gcloud logging read 'resource.type=cloud_run_revision'"
echo "   3. Pause during off-season: gcloud scheduler jobs pause dingers-schedule --location $REGION"
echo ""
