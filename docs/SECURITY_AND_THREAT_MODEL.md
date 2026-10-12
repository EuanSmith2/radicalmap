# Security and Threat Model

## Security assumptions
- Inputs (captions, comments, bios, transcripts) are untrusted text.
- The local SQLite database may contain sensitive public-interest research data.
- Optional AI analysis introduces prompt-injection and data-exfiltration risk if misconfigured.

## Key risks and controls

### 1) Untrusted text and prompt injection
- Treat all ingested text as adversarial.
- Keep deterministic flagging logic separate from AI interpretations.
- Do not grant tool-using LLM agents write access to production systems from this repository.

### 2) Credential handling
- `ANTHROPIC_API_KEY` is optional and should be injected via environment variables only.
- Never commit keys, cookies, or exported session data.

### 3) Local data exposure
- Keep `db/radicalmap.db` local and out of version control.
- Use minimised synthetic fixtures for onboarding and CI.
- Define and follow local retention/deletion procedures for collected research data.

### 4) Platform and legal compliance
- This repository is for research and educational use.
- Users remain responsible for checking platform terms and applicable law in their jurisdiction.
- Nothing in this repository is legal advice.

### 5) Misuse risks
- Do not use outputs for harassment, targeting, or automated enforcement against individuals.
- Treat outputs as investigatory leads requiring human review and context.
