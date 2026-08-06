# Project Hardening Prompts — Index

One file per **built** project. Open the repo in Claude Code, paste the file.

Excluded (not yet built): AgentForge, LLM Eval Platform, Travel Disruption
Concierge, Code Execution Sandbox.

## Shared preamble — PREPEND TO EVERY PROMPT

```
RULES (non-negotiable)
- Work directly. Read files yourself. Do NOT spawn sub-agents.
- Cite file:line for every claim. No generic advice.
- RUN the test suite FIRST and paste real output. If it does not collect, that is finding #1.
- Grep for callers of every major class. Zero callers = dead code = a defect,
  regardless of code quality. This has already bitten two repos in this portfolio.
- Where README/docs disagree with code, report BOTH and treat it as a defect.
- Never claim a pass you did not observe. Never invent a number.
- AUDIT FIRST: findings sorted CRITICAL/HIGH/MEDIUM/LOW with file:line, failure
  scenario, proposed fix. STOP and wait for approval before changing code.
- Label each finding [FIX] / [DOCUMENT] / [DEFEND].
```

## Priority order

| # | File | Project | Why this rank | Est. |
|---|------|---------|---------------|------|
| 1 | `02-arbitron.md` | Arbitron | Live look-ahead bug + a Deflated Sharpe that is a constant | 1 hr for Phase 1 |
| 2 | `04-systemlens.md` | SystemLens | Closes the C/C++ gap AND the SQL gap — the two documented holes | 1–2 wks |
| 3 | `01-amme.md` | AMME | Strongest asset; audited clean; needs verification not repair | 3–5 days |
| 4 | `06-hydrograph.md` | HydroGraph | Leakage audit is blocking — could invalidate every number | 3–5 days |
| 5 | `07-nanolm.md` | NanoLM | KV-cache logit equivalence is the decisive test and the best bullet | 2–3 days |
| 6 | `10-logos-r1.md` | Logos-R1 | **Now load-bearing** — your only built GRPO project | 3–5 days |
| 7 | `05-file-sync-fuse.md` | File Sync | FUSE turns it from common into unique | 2–4 wks |
| 8 | `08-nanovision.md` | NanoVision | Characterise the 109 tests; verify CLIP loss | 2–3 days |
| 9 | `11-clinical-rag.md` | Multi-Agent Clinical RAG | Zero tests today; swap option for GenAI roles | 2–3 days |
| 10 | `09-finguru.md` | FinGuru | Fintech-track only; large build | 3–5 wks |
| 11 | `03-volt-infer.md` | Volt-Infer | Off all resumes until Phase 0 triage decides | ? |

## Resume slots affected by the unbuilt projects

- **AI/ML resume** — AgentForge was slot 2. Substitute **Logos-R1** (built, GRPO,
  StreamingLLM, Best-of-N). Harden it first: `10-logos-r1.md`.
- **Quant resume** — AgentForge was slot 4. Substitute **VolSmith** or **Monte
  Carlo Options Analytics** if either is built; otherwise **Volt-Infer** post-fix
  for the systems slot.
- **SDE resume** — Travel Concierge was slot 4. Substitute **FinGuru** (Track A,
  fintech) or **Arbitron** (full-stack: FastAPI + WebSocket + React).

## Epistemic status of each file

**Findings verified by running code** — treat as a defect list:
`02-arbitron.md`, `03-volt-infer.md`, `09-finguru.md` (the IDOR)

**Structure verified, behaviour unaudited** — treat as a hunt list:
`01-amme.md`, `04-systemlens.md`, `05-file-sync-fuse.md`

**Unaudited** — audit-first, findings unknown:
`06-hydrograph.md`, `07-nanolm.md`, `08-nanovision.md`, `10-logos-r1.md`,
`11-clinical-rag.md`

## Cross-project reuse
- **Argon2id + rotating refresh + Postgres RLS** appears in SystemLens Fleet and
  FinGuru. Build once in SystemLens, port once.
- **Paired bootstrap** from AMME's ablation is directly reusable anywhere you
  compare two configurations.
- A **seccomp/cgroups sandbox** would be shared by Logos-R1's verification path
  and any future eval work. Not yet built.
