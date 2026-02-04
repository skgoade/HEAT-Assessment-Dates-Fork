#!/bin/bash

# Setup Google Cloud Secret Manager secrets
# Run this script before deploying for the first time

set -e

PROJECT_ID="your-gcp-project-id"

echo "🔐 Setting up Secret Manager for Hitting Assessment API"
echo "Project: ${PROJECT_ID}"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is not installed"
    exit 1
fi

# Set project
gcloud config set project ${PROJECT_ID}

# Enable Secret Manager API
echo "Enabling Secret Manager API..."
gcloud services enable secretmanager.googleapis.com

echo ""
echo "Please enter your database credentials:"
echo ""

# Get database host
read -p "Database Host (e.g., 34.123.45.67): " DB_HOST
echo ${DB_HOST} | gcloud secrets create DB_HOST --data-file=- --replication-policy=automatic || \
    echo ${DB_HOST} | gcloud secrets versions add DB_HOST --data-file=-

# Get database user
read -p "Database User: " DB_USER
echo ${DB_USER} | gcloud secrets create DB_USER --data-file=- --replication-policy=automatic || \
    echo ${DB_USER} | gcloud secrets versions add DB_USER --data-file=-

# Get database password (hidden input)
read -sp "Database Password: " DB_PASS
echo ""
echo ${DB_PASS} | gcloud secrets create DB_PASS --data-file=- --replication-policy=automatic || \
    echo ${DB_PASS} | gcloud secrets versions add DB_PASS --data-file=-

# Get database name
read -p "Database Name (production): " DB_NAME_PROD
echo ${DB_NAME_PROD} | gcloud secrets create DB_NAME_PROD --data-file=- --replication-policy=automatic || \
    echo ${DB_NAME_PROD} | gcloud secrets versions add DB_NAME_PROD --data-file=-

echo ""
echo "✅ Secrets created/updated successfully!"
echo ""
echo "Grant Cloud Run access to secrets:"
echo "gcloud secrets add-iam-policy-binding DB_HOST --member='serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com' --role='roles/secretmanager.secretAccessor'"
echo "gcloud secrets add-iam-policy-binding DB_USER --member='serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com' --role='roles/secretmanager.secretAccessor'"
echo "gcloud secrets add-iam-policy-binding DB_PASS --member='serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com' --role='roles/secretmanager.secretAccessor'"
echo "gcloud secrets add-iam-policy-binding DB_NAME_PROD --member='serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com' --role='roles/secretmanager.secretAccessor'"
echo ""
echo "Replace PROJECT_NUMBER with your actual project number"
echo ""
