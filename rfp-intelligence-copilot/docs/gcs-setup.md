# GCS Storage Backend — Setup Guide

This guide sets up Google Cloud Storage as the file storage backend for RFP Intelligence Copilot.

---

## Prerequisites

- A Google Cloud project with billing enabled
- `gcloud` CLI installed and authenticated (`gcloud auth login`)

---

## Step 1 — Create the bucket

```bash
gcloud storage buckets create gs://<your-bucket-name> \
  --location=asia-south1 \
  --uniform-bucket-level-access
```

---

## Step 2 — Create a service account and grant permissions

```bash
gcloud iam service-accounts create rfp-copilot-sa \
  --display-name="RFP Copilot Storage SA"

gcloud storage buckets add-iam-policy-binding gs://<your-bucket-name> \
  --member="serviceAccount:rfp-copilot-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

## Step 3 — Download the service account key

```bash
mkdir -p secrets

gcloud iam service-accounts keys create secrets/gcp-sa-key.json \
  --iam-account=rfp-copilot-sa@<your-gcp-project-id>.iam.gserviceaccount.com
```

> `secrets/` is in `.gitignore` — the key file is never committed.

---

## Step 4 — Fill in `.env`

```env
STORAGE_BACKEND=gcs
GCS_BUCKET_NAME=<your-bucket-name>
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
DATA_DIR=/tmp/rfp-cache
```

---

## Step 5 — Start with the GCS Compose file

```bash
docker compose -f docker-compose.gcs.yml up -d
```

---

## Cloud Run / GKE — Workload Identity (no key file)

If you're deploying on Cloud Run or GKE, skip the key file entirely.
Assign `roles/storage.objectAdmin` to the compute service account and leave
`GOOGLE_APPLICATION_CREDENTIALS` unset. The SDK auto-detects it.

```bash
gcloud projects add-iam-policy-binding <your-gcp-project-id> \
  --member="serviceAccount:<compute-sa>@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```
