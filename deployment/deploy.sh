#!/bin/bash

# HEAT Assessment API — new Cloud Run service (separate from legacy hitting-assessment-api)
# Deploys from this repo so PDF/report work does not overwrite the old static-dates API.

set -e  # Exit on error

# Configuration
PROJECT_ID="norse-coral-441421-r9"
REGION="us-east4"
# New service name — do NOT reuse hitting-assessment-api (legacy static dates site)
SERVICE_NAME="heat-assessment-api"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "Starting deployment of HEAT Assessment API (${SERVICE_NAME})..."

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI is not installed"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &> /dev/null; then
    echo "Error: Not authenticated with gcloud"
    echo "Please run: gcloud auth login"
    exit 1
fi

# Set the project
echo "Setting GCP project to: ${PROJECT_ID}"
gcloud config set project ${PROJECT_ID}

# Enable required APIs
echo "Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable secretmanager.googleapis.com

# Build the container image
echo "Building container image..."
cd backend
gcloud builds submit --tag ${IMAGE_NAME}
cd ..

# Deploy to Cloud Run (creates a NEW service if it does not exist)
echo "Deploying to Cloud Run as ${SERVICE_NAME}..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME} \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --max-instances 10 \
    --set-env-vars "DB_PORT=3306,HEAT_GCS_BUCKET=heat-assessment-reports,HEAT_GCS_PREFIX=heat-assessments/,REPORT_LOCAL_DIR=/app/reports" \
    --set-secrets "DB_HOST=DB_HOST:latest,DB_USER=DB_USER:latest,DB_PASS=DB_PASS:latest,DB_NAME_PROD=DB_NAME_PROD:latest"

# Get the service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format 'value(status.url)')

echo ""
echo "Deployment complete!"
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Next steps:"
echo "1. Point frontend API_URL at: ${SERVICE_URL}/api/hitting-assessment"
echo "2. Health check: curl ${SERVICE_URL}/health"
echo "3. Publish frontend/index.html to the form bucket"
echo "4. Leave legacy hitting-assessment-api running until you decommission it"
echo ""
