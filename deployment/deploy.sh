#!/bin/bash

# RBI Hitting Assessment Deployment Script
# This script deploys the backend API to Google Cloud Run

set -e  # Exit on error

# Configuration
PROJECT_ID="your-gcp-project-id"
REGION="us-central1"
SERVICE_NAME="hitting-assessment-api"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "🚀 Starting deployment of Hitting Assessment API..."

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is not installed"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &> /dev/null; then
    echo "❌ Error: Not authenticated with gcloud"
    echo "Please run: gcloud auth login"
    exit 1
fi

# Set the project
echo "📋 Setting GCP project to: ${PROJECT_ID}"
gcloud config set project ${PROJECT_ID}

# Enable required APIs
echo "🔧 Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable secretmanager.googleapis.com

# Build the container image
echo "🏗️  Building container image..."
cd backend
gcloud builds submit --tag ${IMAGE_NAME}
cd ..

# Deploy to Cloud Run
echo "☁️  Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME} \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 10 \
    --set-env-vars "DB_PORT=3306" \
    --set-secrets "DB_HOST=DB_HOST:latest,DB_USER=DB_USER:latest,DB_PASS=DB_PASS:latest,DB_NAME_PROD=DB_NAME_PROD:latest"

# Get the service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format 'value(status.url)')

echo ""
echo "✅ Deployment complete!"
echo "📍 Service URL: ${SERVICE_URL}"
echo ""
echo "Next steps:"
echo "1. Update the API_URL in frontend/index.html to: ${SERVICE_URL}/api/hitting-assessment"
echo "2. Test the health endpoint: curl ${SERVICE_URL}/health"
echo "3. Deploy the frontend to your web hosting service"
echo ""
