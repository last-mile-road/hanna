# 01 — What HANNA does, end to end

**In plain language:** HANNA reads a photo of a leprosy patient's clinic card
taken at a primary-health clinic, pulls the important values out of it, works
out where the patient lives, shows the result to a person for a quick check,
and then notifies the national leprosy programme. It exists because the data
path today runs one way — from the clinic up to the centre — and the clinic
never sees capability, feedback or recognition come back. HANNA is designed to
make the *notification itself* the moment where the last mile gains something.

---

## The pipeline

```
photo of card → alignment → block reading (VLM) → validation
              → location (gazetteer → service → map picker)
              → human review → national notification
```

| Step | Component | Where it lives |
|---|---|---|
| Capture | phone photo, with a client-side quality gate (blur, brightness, glare) | Bench screen 1 |
| Alignment | template matching onto the blank card (`core/align.py`) | server |
| Reading | one vision-model call per block (`core/inference.py`, `core/prompts.py`) | OpenRouter / Modal GPU |
| Validation | rules from `validation.yaml`: required fields, ranges, cross-checks (`core/validate.py`) | server |
| Location | the cascade — see `03-location.md` | server + map |
| Review | one screen: confirm or correct each value, confirm the pin (`static/review.*`) | Bench |
| Ledger | every run, block call, observation and correction recorded (PostgreSQL) | server |

## The three surfaces

| Surface | Who | Does |
|---|---|---|
| **Bench** | country team | upload cards, run, review, correct, comment |
| **Studio** | form owner / moderator | define the card template (build plan: `hanna_next/STUDIO.md`) |
| **Clinic** | clinic staff (later) | photograph and confirm |

The clinic route never exposes coordinates, concepts or profiles.

## Where the numbers live

- Accuracy is reported per block, per concept, per model profile — always with
  `n` and the number of distinct cards (`CONTRACTS.md` §5).
- The measured baseline: 94.2% over 3,045 human-judged values from the VPS
  extraction-accuracy table (weighted; 15 patients behind 53 captures).
- Everything measured is reproducible from the ledger; nothing is asserted
  from memory.

*(Page owner's note: extraction detail lives in `02-extraction.md`; this page
stays the map of the whole.)*
