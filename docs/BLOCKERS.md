# BLOCKERS — submission work remaining

The local API was configured with Razorpay Test Mode and Groq for the completed
proof. The fresh two-line monitor + keyboard run is recorded in
`REAL_INTEGRATION_STATUS.md`: the real order, payment, signed webhook, synthetic
one-line breach, line-scoped remedy/refund, idempotent replay, and final audit
reconciliation all passed. All eleven real-integration criteria are PROVEN.
Remaining blockers are deployment access and submission assets, not payment
verification.

## 1. If reproducing a fresh local proof: Razorpay Test Mode credentials

Copy `.env.example` → `.env` at repo root and fill:

```dotenv
RAZORPAY_KEY_ID=(paste your Test key id — looks like rzp_test_ + 14 chars)
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxx # the paired secret
RAZORPAY_WEBHOOK_SECRET=xxxxxxxxxxxxxxxx   # dashboard -> Settings -> Webhooks
DEMO_OPERATOR_TOKEN=<any long random string>
```

Then:
```bash
cd apps/api && .venv/Scripts/python.exe ../../scripts/verify_real_integration.py
```
The script pauses once for the one human step (completing the test payment in
the Razorpay window) and does everything else automatically.

If that wait expires after an order was created, use
`--resume-contract con_... --wait 600` to continue the same contract without
resetting the store or creating a second Razorpay order.

## 2. LLM API key (required for a fresh LLM basket proof)

Any one of:
```dotenv
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
# or
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://api.openai.com/v1     # or any compatible endpoint
LLM_MODEL=gpt-4o                            # or your gateway's model id
LLM_API_KEY=sk-...
# or Groq's OpenAI-compatible endpoint
LLM_PROVIDER=groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_API_KEY=<groq-api-key>
# or NVIDIA NIM's OpenAI-compatible endpoint
LLM_PROVIDER=nvidia
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
LLM_API_KEY=<nvidia-api-key>
```

## 3. Deployment authorization

If I cannot authenticate to Railway/Vercel from this environment, either run
the deploy steps in docs/DEPLOYMENT.md yourself or grant access. All
deployment config files are prepared so this is copy-paste.

## 4. Submission assets and credential rotation

Before public deployment, rotate the Test Mode and LLM credentials used for the
local proof and configure only the rotated values in the deployed services. Then
record the final video, refresh the 11 screenshots, and replace the
`[LIVE_DEMO_URL]` and `[VIDEO_URL]` placeholders in `docs/SUBMISSION.md`.
