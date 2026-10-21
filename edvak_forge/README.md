# Edvak Forge — Maplewood Clinic Front-Desk Agent

Safety-first take-home implementation over the supplied **synthetic** clinic data. The agent uses deterministic policy logic for high-risk decisions; it does not approve or send prescriptions and does not give automated clinical advice.

## Requirements
- Python 3.10+
- Internet only to install dependencies (`mcp`, `pytest`)
- VS Code (recommended)

## Run in VS Code terminal
```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux instead: source .venv/bin/activate
pip install -r requirements.txt
python agent.py --output results.json
python evaluate.py
python -m pytest -q
```

The first command processes all 40 messages and writes `results.json`. `evaluate.py` writes `evaluation_report.json` and prints urgency accuracy, emergency recall, action exact-match, action precision/recall, missed emergencies, and prohibited-action checks.

## Run the MCP server
```bash
python mcp_server.py
```

The server exposes bounded tools for patient lookup/verification, patient medication lookup, refill eligibility, free-slot search, booking, cancellation, rescheduling, human queue routing, and reply drafting. It loads JSON from `data/`. Scheduling mutations live in memory for the process lifetime.

## Architecture and choices
- `agent.py`: deterministic message triage, policy decisions, tool-call trace, and reply drafts.
- `mcp_server.py`: MCP tools for the clinic's synthetic systems.
- `evaluate.py`: compares outputs against the supplied labels and reports safety failures.
- `test_agent.py`: automated regression tests for agent safety and behavior.
- `data/*.json`: supplied synthetic clinic data; date is fixed by the clinic file to 2026-11-09.

No external LLM is required by this baseline, so the per-run model cost is **$0**. This is intentional for repeatability and safety; a future version could use an LLM for intent extraction and reply drafting while retaining deterministic policy gates. Reused: FastMCP for MCP transport and pytest for testing. Built: triage logic, policy checks, tool wrappers, and evaluation harness.

## Known limitations
This is a time-boxed heuristic baseline, not a production clinical system. Some scheduling flows are described/routed rather than fully committing a specific slot; some natural-language variations may be missed; evaluation checks should be expanded to cover all `must_not` semantics directly. The evaluation report is generated from the actual run and should be reviewed honestly rather than presented as a guaranteed score.

## Before real patient data
Do not deploy as-is. Require a clinical safety review, privacy/security review, authentication and role-based authorization, auditable access logs, encrypted transport/storage, secrets management, secure integration with the EHR, human approval for all clinical decisions and prescription workflows, robust monitoring, incident response, consent/retention policies, and extensive prospective validation. The provided dataset is fictional and only should be used for this exercise.

## Evaluation Results

Evaluated on 40 supplied synthetic messages.

- Urgency accuracy: 97.5%
- Emergency recall: 100%
- Action exact-match accuracy: 82.5%
- Action micro-precision: 88.89%
- Action micro-recall: 85.11%
- Missed emergencies: 0
- Must-not violations: 0

These results apply only to the supplied evaluation set and do not guarantee performance on unseen messages.


## What I would improve with another week

1. **Improve action classification:** Analyze incorrect predictions across the 40-message evaluation set and expand regression tests for missed and extra actions.
2. **Strengthen authorization:** Enforce server-side identity verification and role-based access across every patient-specific tool, with token expiry, revocation, and audit logging.
3. **Expand testing:** Add tests for every MCP tool, invalid inputs, ambiguous identities, scheduling conflicts, duplicate requests, and prohibited actions.
4. **Improve language coverage:** Test more natural-language variations and use an LLM for intent extraction or reply drafting only behind deterministic safety rules and human review.
5. **Improve observability:** Add structured logs, traceable tool calls, clearer error handling, and evaluation reports that make failures easy to investigate.

The current evaluation covers 40 synthetic messages. Further validation with broader, independently reviewed test cases would be necessary before considering deployment.