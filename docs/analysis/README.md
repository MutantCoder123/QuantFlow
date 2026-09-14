# Analysis archive

The audits that produced
[`docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md`](../superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md).

Kept for provenance: when the plan says a number was fabricated or an
indicator read the wrong series, the evidence is here, with `file:line`
citations against the pre-remediation tree.

| File | What it is | Still current? |
|---|---|---|
| `Architecture_detailed_overview.md` | The system as built, every structural claim cited | Structure holds; Phase 0–5 changed many specifics |
| `drawbacks_false_claimed.md` | Gap between advertised and actual behaviour; the defect list the plan works through | Most findings now fixed — see `CHANGE_SPECSHEET.md` |
| `improved_architecture_and_features.md` | Proposed changes, each traced to a measured property; §7 lists rejected ideas | Became the plan |
| `Already_fixed_issues.md` | Edge cases surviving an earlier round of fixes (2026-06-13) | Superseded |
| `LLM_PIPELINE_CRITIQUE.md` | Earlier critique of the LLM path | Superseded |
| `old_PIPELINE_AUDIT_REPORT.md` | The original pipeline audit | Superseded |

These are **historical records, not living documentation**. They describe the
tree as it stood before the remediation branch. For current behaviour read the
code and `CHANGE_SPECSHEET.md`.
