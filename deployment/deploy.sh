#!/bin/bash

# HEAT Assessment API — new Cloud Run service (separate from legacy hitting-assessment-api)
# Deploys from this repo so PDF/report work does not overwrite the old static-dates API.
#
# Prefer Cloud Build build+deploy (backend/cloudbuild.yaml) so the Build SA pulls the image.
# See docs/NOAH_OPS.md for IAM, SQL migrations, and signed-URL SA setup.

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

# Build + deploy via Cloud Build (Build SA pushes image and runs gcloud run deploy)
echo "Submitting Cloud Build (build + deploy)..."
cd backend
gcloud builds submit --config cloudbuild.yaml .
cd ..

# Get the service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format 'value(status.url)')

echo ""
echo "Deployment complete!"
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Next steps:"
echo "1. In frontend/index.html set: window.HEAT_API_BASE = '${SERVICE_URL}'"
echo "2. Health check: curl ${SERVICE_URL}/health"
echo "3. Run SQL if needed: deployment/assessment_attachments.sql"
echo "   and deployment/assessment_report_versions.sql"
echo "4. Publish frontend/index.html to the form bucket"
echo "5. Leave legacy hitting-assessment-api running until you decommission it"
echo "6. See docs/NOAH_OPS.md for GCS signed-URL IAM on the Cloud Run SA"
echo ""
