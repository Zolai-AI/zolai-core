"""Structured verification prompts with ZVS 2018 rules.

All prompts use jinja2-style ``{{placeholder}}`` syntax.
Call ``inject_evidence()`` from ``grounding.py`` to fill them.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ZVS 2018 rules block (included in every verification prompt)
# ---------------------------------------------------------------------------

_ZVS_RULES = """\
ZVS 2018 ORTHOGRAPHY RULES (mandatory — output must comply):
1. Forbidden forms → Correct forms:
   - pathian → pasian (God)
   - ram → gam (earth/land)
   - fapa → tapa (life/son)
   - bawipa → topa (Lord)
   - siangpahrang → kumpipa (Savior)
   - cu/cun → tua (that, conjunction)
   - suah → suahtakna (holiness, context-dependent)
   - nunnak → nuntakna (life, context-dependent)
2. Word order: SOV (Subject-Object-Verb). Verb always comes last.
3. Negation: "kei" for all persons (e.g. "Ka pai kei hi"). "lo" is
   literary/standalone — never attach agreement marker "a" to "lo".
4. Questions: "hiam" for yes/no. "bang hang" + verb + subject + "hiam"
   for content questions (NOT "bang hang" + subject + verb).
5. Ergative: "in" marks the agent of transitive verbs.
6. Pronouns: "a" = agreement marker before verb; "amah" = emphatic standalone.
7. Tense: "hi" (present), "ta" (past), "ding" (future), "zo" (completive),
   "khin" (experiential), "lai" (progressive).
8. Negative future: "kei + ding" or "lo + ding".
9. Tone sandhi: T1+T3→T2+T3, T3+T1→T2+T1, T3+T3→T2+T3, T3+T4→T3+T2, T4+T1→T4+T1.
"""

# ---------------------------------------------------------------------------
# Word verification prompt
# ---------------------------------------------------------------------------

VERIFY_WORD_PROMPT = """\
You are a Zolai (Tedim Chin) language expert verifying a single word.

## Candidate word
{{candidate}}

## Evidence
{{evidence}}

{_zvs_rules}

## Task
Verify the candidate word. Return a JSON object with:
- "is_valid": true/false — is this a valid Zolai word per ZVS 2018?
- "canonical_form": the correct ZVS 2018 spelling (or the candidate if valid)
- "confidence": 0.0-1.0
- "syllables": list of syllables (e.g. ["pa", "sian"])
- "pos": part of speech
- "morphology": morphological breakdown
- "senses": list of meaning senses
- "evidence_assessment": list of strings explaining evidence evaluation
- "disagreements": list of any conflicts found in evidence
- "requires_human_review": true if evidence is contradictory or confidence < 0.6

Output ONLY valid JSON. No commentary.
""".replace("{_zvs_rules}", _ZVS_RULES)

# ---------------------------------------------------------------------------
# Sentence verification prompt
# ---------------------------------------------------------------------------

VERIFY_SENTENCE_PROMPT = """\
You are a Zolai (Tedim Chin) language expert verifying a sentence.

## Candidate sentence
{{candidate}}

## Evidence
{{evidence}}

{_zvs_rules}

## Task
Verify the candidate sentence. Return a JSON object with:
- "is_valid": true/false — is this grammatically correct ZVS 2018 Zolai?
- "confidence": 0.0-1.0
- "sov_valid": true if SOV word order is correct
- "ergative_present": true if ergative "in" is used correctly
- "negation_type": "none", "kei", or "lo"
- "question_type": "none", "yes_no" (hiam), or "content" (bang_hang)
- "tense": "present", "past", "future", "completive", "experiential", "progressive", or "unknown"
- "translation": English translation of the sentence
- "evidence_assessment": list of strings explaining evidence evaluation
- "disagreements": list of any conflicts found in evidence
- "requires_human_review": true if confidence < 0.6 or evidence is contradictory

Output ONLY valid JSON. No commentary.
""".replace("{_zvs_rules}", _ZVS_RULES)

# ---------------------------------------------------------------------------
# Grammar verification prompt
# ---------------------------------------------------------------------------

VERIFY_GRAMMAR_PROMPT = """\
You are a Zolai (Tedim Chin) grammar expert performing deep analysis.

## Candidate text
{{candidate}}

## Evidence
{{evidence}}

{_zvs_rules}

## Task
Perform thorough grammar verification. Return a JSON object with:
- "is_valid": true/false — does this pass all grammar checks?
- "confidence": 0.0-1.0
- "sov_valid": true if SOV word order is correct
- "ergative_present": true if ergative "in" is used correctly
- "negation_type": "none", "kei", or "lo"
- "question_type": "none", "yes_no" (hiam), or "content" (bang_hang)
- "tense": identified tense/aspect
- "translation": English translation
- "violations": list of specific grammar violations found
- "corrections": list of suggested corrections (each a dict with "rule", "found", "expected")
- "evidence_assessment": list of strings explaining evidence evaluation
- "disagreements": list of any conflicts found in evidence
- "requires_human_review": true if confidence < 0.6, multiple violations, or complex morphology

Output ONLY valid JSON. No commentary.
""".replace("{_zvs_rules}", _ZVS_RULES)
