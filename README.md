# Document Analyzer

AI-powered document summarization, extraction, rewriting, and analysis.
Upload a PDF or paste text, pick a mode, and watch Claude's response stream
in live.

## Stack
- **Backend:** Python, FastAPI, Anthropic SDK (`claude-sonnet-4-6`), streamed via SSE
- **Frontend:** Vanilla HTML/CSS/JS (no build step), responsive, renders Markdown
- **Container:** Single Docker image (FastAPI serves the static frontend + API)
- **Deploy target:** AWS App Runner (ECR-backed)

## Project layout
```
document-analyzer/
├── backend/
│   ├── main.py            # FastAPI app, Claude streaming, PDF parsing
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── deploy/
│   ├── deploy-apprunner.sh
│   └── apprunner-trust-policy.json
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## 1. Run locally (no Docker)
```bash
cd backend
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --reload --port 8080
```
Open http://localhost:8080

## 2. Run locally with Docker
```bash
cp .env.example .env        # then edit .env with your real key
docker compose up --build
```
Open http://localhost:8080

## 3. Deploy to AWS App Runner
Prerequisites: AWS CLI v2 configured (`aws configure`), Docker running, an
Anthropic API key.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export AWS_REGION=us-east-1        # optional, defaults to us-east-1
export APP_NAME=document-analyzer  # optional
cd deploy
./deploy-apprunner.sh
```

What the script does:
1. Creates an ECR repository (if missing) and pushes the built Docker image.
2. Stores `ANTHROPIC_API_KEY` as a **SecureString** in AWS Systems Manager
   Parameter Store — the key never touches source control or the image.
3. Creates an IAM role App Runner uses to pull from ECR.
4. Creates (or redeploys) the App Runner service, injecting the API key as a
   runtime secret via `RuntimeEnvironmentSecrets`.
5. Prints the public HTTPS URL of the live service.

You can also do this from the AWS Console:
1. **ECR** → create repository → push the image built from the `Dockerfile`.
2. **App Runner** → Create service → Source: Container registry → select the
   ECR image → Port `8080`.
3. Under **Environment variables**, add `ANTHROPIC_API_KEY` as a secret
   (reference a Secrets Manager/SSM value — do not paste it as plain text).
4. Deploy. App Runner gives you a public `https://xxxx.awsapprunner.com` URL.

### Alternative: Elastic Beanstalk (Docker platform)
1. `eb init -p docker document-analyzer`
2. `eb create document-analyzer-env`
3. Set the API key: `eb setenv ANTHROPIC_API_KEY=sk-ant-...`
4. `eb deploy`

## Security notes
- The API key is read only from `ANTHROPIC_API_KEY`/SSM/Secrets Manager —
  never hard-coded, never sent to the browser, and excluded from git via
  `.gitignore`.
- CORS is restricted via `ALLOWED_ORIGINS`; set it to your real domain in
  production instead of `*`.
- Uploaded PDFs are processed in memory and discarded — nothing is written
  to disk or persisted.
- The container runs as a non-root user.

## API reference
| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/api/health` | GET | – | Health check |
| `/api/analyze/text` | POST | `{text, mode, custom_instructions}` (JSON) | Analyze pasted text, streams SSE |
| `/api/analyze/pdf` | POST | multipart: `file`, `mode`, `custom_instructions` | Analyze uploaded PDF, streams SSE |

`mode` ∈ `summarize | extract | rewrite | analyze`
