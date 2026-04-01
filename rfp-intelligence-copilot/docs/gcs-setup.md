# GCS Storage Setup Guide

This guide walks you through wiring RFP Copilot to Google Cloud Storage so that
all project data and uploaded files survive container restarts and redeployments.

---

## 1. Create a GCS Bucket

```bash
gcloud storage buckets create gs://<your-bucket-name> \
  --location=<your-region> \
  --uniform-bucket-level-access
```

> Recommended region: same as your compute (e.g. `asia-south1` for Mumbai / Bengaluru).
> Enable **uniform bucket-level access** — the app does not use ACLs.

---

## 2. Create a Service Account

```bash
# Create the service account
gcloud iam service-accounts create rfp-copilot-sa \
  --display-name="RFP Copilot Storage SA"

# Grant it Storage Object Admin on your bucket only (least privilege)
gcloud storage buckets add-iam-policy-binding gs://<your-bucket-name> \
  --member="serviceAccount:rfp-copilot-sa@<your-gcp-project-id>.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

## 3. Download the Service Account Key

```bash
mkdir -p secrets

gcloud iam service-accounts keys create secrets/gcp-sa-key.json \
  --iam-account=rfp-copilot-sa@<your-gcp-project-id>.iam.gserviceaccount.com
```

> `secrets/gcp-sa-key.json` is already in `.gitignore` — it will never be committed.

---

## 4. Configure `.env`

```env
STORAGE_BACKEND=gcs
GCS_BUCKET_NAME=<your-bucket-name>
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
```

---

## 5. Start the Server

```bash
docker compose -f docker-compose.gcs.yml up -d
```

On startup you should see:

```
[project_store] GCS backend active: gs://<your-bucket-name>
```

If you see `GCS unavailable ... falling back to local`, check:
- The key file is mounted at `/secrets/gcp-sa-key.json` inside the container
- `GOOGLE_APPLICATION_CREDENTIALS` in `.env` matches that path exactly
- The service account has `roles/storage.objectAdmin` on the bucket

---

## 6. Verify

```bash
# Create a test project via the API
curl -X POST http://localhost:7860/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "GCS test project"}'

# List objects in your bucket — you should see projects/<uuid>/project.json
gcloud storage ls gs://<your-bucket-name>/projects/
```

---

## Running on GCP (Cloud Run / GKE)

If you deploy to Cloud Run or GKE, you can skip the service account key entirely.
Assign the **Workload Identity** or the **Cloud Run service account** the
`roles/storage.objectAdmin` role on the bucket, then set:

```env
STORAGE_BACKEND=gcs
GCS_BUCKET_NAME=<your-bucket-name>
# Leave GOOGLE_APPLICATION_CREDENTIALS blank — SDK auto-detects credentials
```

The app falls back gracefully: if GCS fails to connect at startup, it logs a warning
and continues with local storage so the container still boots.

---

## Data Layout in the Bucket

```
gs://<bucket>/
  projects/
    <project-uuid>/
      project.json          ← project metadata
      rfp/
        <filename>.pdf      ← uploaded RFP document
      suppliers/
        <filename>.xlsx     ← uploaded supplier responses
      metadata/
        questions.json
        feature_flags.json
        audit_log.json
        suppliers.json
```

All reads are cached to `/tmp/rfp-cache/` inside the container for the duration
of a request. The container filesystem is intentionally ephemeral — GCS is the
single source of truth.
