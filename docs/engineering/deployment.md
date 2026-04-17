# Deployment Guide

The application is designed to run on **Google Cloud Run** with Firebase Authentication. This guide covers the full production deployment process.

## Architecture Overview

```
Internet -> Cloud Run (FastAPI app) -> Document AI API
                  ^
          Firebase Auth (JWT verification via public certs)
```

## Prerequisites

- A GCP project with billing enabled.
- Firebase project linked to the same GCP project.
- `gcloud` CLI installed and authenticated.

## GCP Setup

### 1. Enable Required APIs

In **APIs & Services -> Enable APIs and Services**:

- Cloud Run Admin API
- Cloud Build API
- Artifact Registry API
- Document AI API

### 2. Create Artifact Registry Repository

```
Artifact Registry -> Repositories -> Create repository
  Name:     webapp-images
  Format:   Docker
  Mode:     Standard
  Region:   africa-south1  (match your Cloud Run region)
```

Image path: `africa-south1-docker.pkg.dev/<project-id>/webapp-images`

### 3. Create Document AI Processor

```
Document AI -> Processors -> Create processor
  Type:     Bank Statement Parser
  Name:     fnb-bank-statement-parser
  Region:   eu
```

Note the **Processor ID** for `DOCUMENTAI_PROCESSOR_ID`.

### 4. Create Runtime Service Account

```
IAM & Admin -> Service Accounts -> Create
  Name:  cloudrun-pdf-service
  Email: cloudrun-pdf-service@<project-id>.iam.gserviceaccount.com
```

Grant roles on the GCP project:
- **Document AI API User** (`roles/documentai.apiUser`)
- **Firebase Authentication Admin** (`roles/firebaseauth.admin`) - required to create backend Firebase session cookies
- **Logs Writer** (`roles/logging.logWriter`)
- **Secret Manager Secret Accessor** (`roles/secretmanager.secretAccessor`)
- **Cloud Datastore User** (`roles/datastore.user`) - required for Firestore billing/admin data
- **BigQuery Job User** (`roles/bigquery.jobUser`) - required for billing reconciliation queries
- **BigQuery Data Viewer** (`roles/bigquery.dataViewer`) - required to read billing export data

## Firebase Setup

### 1. Link Firebase to GCP Project

Firebase Console -> Create project -> Connect to existing GCP project.

### 2. Enable Auth Providers

Firebase Console -> Authentication -> Enable:
- **Google**
- **Email/Password**

### 3. Add Authorized Domains

Firebase Console -> Authentication -> Settings -> Authorized domains:
- Add the neutral Cloud Run URL for the current beta:
  `statement-to-excel-1052852371581.africa-south1.run.app`.
- If a custom domain is added later, add that domain too.

### 4. Create User Accounts

Firebase Console -> Authentication -> Users -> Add users that should be in the allowlist.

## Cloud Run Deployment (Image-Based)

This repository uses an image-based deployment flow:

1. Build the Docker image locally.
2. Push the image to Artifact Registry.
3. Deploy Cloud Run using `--image`.

Production naming:

| Item | Value |
|---|---|
| Cloud Run service | `statement-to-excel` |
| Current public URL | `https://statement-to-excel-1052852371581.africa-south1.run.app` |
| Deferred custom domain | `statements.swan-computing.com` |
| Artifact image name | `fnb-pdf-to-excel` |

### Environment Variables

Set these in Cloud Run:

| Variable | Value |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | `<your-gcp-project-id>` |
| `DOCUMENTAI_LOCATION` | `eu` |
| `DOCUMENTAI_PROCESSOR_ID` | `<your-processor-id>` |
| `FIREBASE_PROJECT_ID` | `<your-firebase-project-id>` |
| `ALLOWED_USER_EMAILS` | `user1@example.com,user2@example.com` |
| `MAX_FILE_SIZE_MB` | `10` |
| `MAX_PAGES_PER_REQUEST` | `50` |
| `MAX_REQUESTS_PER_MINUTE_PER_USER` | `10` |
| `MAX_PAGES_PER_DAY_PER_USER` | `300` |
| `REDIS_URL` | `<redis-url>` |
| `BILLING_ENABLED` | `true` |
| `DEFAULT_MONTHLY_LIMIT` | `500.00` |
| `DEFAULT_WARN_PCT` | `80` |
| `FIRESTORE_DATABASE_ID` | `fnb-billing` for production |
| `BQ_BILLING_TABLE` | Full BigQuery billing export table path |
| `RECONCILIATION_TZ` | `Africa/Johannesburg` |
| `RECONCILIATION_ALERT_PCT` | `15` |
| `RECONCILIATION_COLLECTION` | `cost_reconciliation_daily` |
| `LOG_LEVEL` | `INFO` |

### Build and Push

```bash
export PROJECT_ID="<your-gcp-project-id>"
export REGION="africa-south1"
export REPO="webapp-images"
export IMAGE_NAME="fnb-pdf-to-excel"
export IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_NAME}:${IMAGE_TAG}"
```

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker build -t "${IMAGE_URI}" .
docker push "${IMAGE_URI}"
```

### Deploy Cloud Run

```bash
gcloud run deploy statement-to-excel \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --service-account "cloudrun-pdf-service@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},DOCUMENTAI_LOCATION=eu,DOCUMENTAI_PROCESSOR_ID=<processor-id>,FIREBASE_PROJECT_ID=<firebase-project-id>,ALLOWED_USER_EMAILS=<emails>,MAX_FILE_SIZE_MB=10,MAX_PAGES_PER_REQUEST=50,MAX_REQUESTS_PER_MINUTE_PER_USER=10,MAX_PAGES_PER_DAY_PER_USER=300,REDIS_URL=<redis-url>,LOG_LEVEL=INFO" \
  --timeout 120 \
  --memory 512Mi \
  --allow-unauthenticated
```

> **Note**: `--allow-unauthenticated` is expected because auth is handled in-app with Firebase bearer tokens.

### Recommended Settings

| Setting | Value | Reason |
|---|---|---|
| Timeout | 120s | Document AI processing can take 30-60s for large PDFs |
| Memory | 512Mi | PDF processing and Excel generation are memory-intensive |
| CPU | 1 | Sufficient for single-user / low-traffic usage |
| Min instances | 1 | Keeps one warm instance to reduce auth/dashboard latency |
| Max instances | 2 | Prevent cost overruns during testing |

## Production Considerations

### Session Storage

Preview/review sessions should use shared storage (Redis/Memorystore) in production:
- Survives app instance recycle.
- Works consistently across multiple Cloud Run instances.
- Reduces repeated bootstrap latency when users navigate between pages.

### Document AI Caching

The file-based cache (`.cache/docai/`) is **ephemeral** on Cloud Run - it will be cleared on every container restart. For production:
- This is acceptable: the cache primarily benefits local development.
- Optionally, use a Cloud Storage bucket for persistent caching.

### Secrets Management

For production, move sensitive values to **Secret Manager**:
- `ALLOWED_USER_EMAILS`
- Any future API keys

Reference secrets in Cloud Run using `--set-secrets` instead of `--set-env-vars`.

### Cost Controls

Document AI charges per page processed. Implement rate limiting before going to production:
- `MAX_FILE_SIZE_MB` (e.g., 10)
- `MAX_PAGES_PER_REQUEST` (e.g., 50)
- `MAX_REQUESTS_PER_MINUTE_PER_USER` (e.g., 10)
- `MAX_PAGES_PER_DAY_PER_USER` (e.g., 300)

These quota limits are enforced before Document AI calls.

### Firestore Query Indexes

This project includes a baseline composite index definition in `firestore.indexes.json`.
The Firebase project metadata in `.firebaserc` and `firebase.json` targets the
production named Firestore database, `fnb-billing`. Keep `FIRESTORE_DATABASE_ID`
set to the same database ID in Cloud Run.

Deploy indexes:

```powershell
firebase deploy --only firestore:indexes --project fnb-pdf-to-excel-prod-491212
```

Do not add single-field indexes to `firestore.indexes.json`. Firestore manages
normal ascending and descending single-field indexes automatically. For example,
`cost_reconciliation_daily.day ASC` is not a valid composite index entry by
itself and Firebase will reject it as unnecessary.

If the Firebase CLI tries to create or deploy to `(default)`, check that
`firebase.json` includes `"database": "fnb-billing"` under the `firestore`
configuration before rerunning the command.

### Custom Domain

The current beta uses the neutral Cloud Run URL:

```text
https://statement-to-excel-1052852371581.africa-south1.run.app
```

The intended future custom domain is:

```text
https://statements.swan-computing.com
```

Custom domain work is deferred until the app has enough usage to justify the
extra monthly cost. Direct Cloud Run domain mappings are not available in
`africa-south1`. The production-grade custom-domain path is a global external
HTTPS load balancer with a serverless NEG pointing at `statement-to-excel`.
Budget roughly the load balancer base cost plus data processing before enabling
this path.

`swan-computing.com` has already been verified in Google Search Console. Keep
the DNS verification TXT record in place so the domain stays verified.

### Cache Layer

For cross-instance cache hits, point `REDIS_URL` to Memorystore and keep short TTL caches for:
- `admin:*` payloads
- `billing:data:*`
- preview sessions

## Monitoring

### Logs

The application emits structured JSON logs via `app/services/logging_utils.py`. Key events:

| Event | Level | Description |
|---|---|---|
| `auth_success` | INFO | Successful authentication |
| `auth_failed_*` | ERROR | Various auth failure reasons |
| `extract_success` | INFO | Successful extraction and download |
| `extract_preview_success` | INFO | Successful preview generation |
| `parser_visual_row` | INFO | Each parsed transaction (with details) |
| `docai_cache_hit` | INFO | Document AI cache hit |
| `docai_cache_write` | INFO | Document AI response cached |

Logs are viewable in **Cloud Logging** when deployed to Cloud Run.

Track these latency SLO checks after deployment:
- `/billing/data` p95
- `/admin/overview` p95
- `/admin/users` p95
- auth bootstrap (`POST /auth/session`) error rate and latency
