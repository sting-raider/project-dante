# BLOCKERS — human action required for final proof

The local API is currently configured with Razorpay Test Mode and Groq in
process memory. Historical single-line Test Mode evidence exists, but the
amended requirement-5 proof is a fresh two-line monitor + keyboard basket. The
remaining external steps are registering the public HTTPS webhook endpoint in
the authenticated Razorpay Test Mode dashboard and completing the Standard
Checkout payment; after that, the verifier finishes the synthetic breach,
rights, scoped remedy, refund, replay, and evidence checks automatically.

## 1. If starting a fresh API process: Razorpay Test Mode credentials

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

## 2. LLM API key (required for the amended final basket proof)

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
```

## 3. Deployment authorization

If I cannot authenticate to Railway/Vercel from this environment, either run
the deploy steps in docs/DEPLOYMENT.md yourself or grant access. All
deployment config files are prepared so this is copy-paste.
