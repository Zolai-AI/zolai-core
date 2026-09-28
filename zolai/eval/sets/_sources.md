# Eval fixture sources

## `eval_v1` set (KR3.1 — 110 cases, created 2026-09-28)

`eval_v1_zvs.jsonl` (40) + `eval_v1_qa.jsonl` (40) + `eval_v1_translation.jsonl`
(30) = **110 cases**, all derived from the read-only canonical store
`data/zolai.db` (SELECT-only). No Zolai string was hand-written; every string
passes `zolai.zvs.validate` and contains no ZVS-2018 forbidden form.

| Lane | Rows | Source tables | Selection method |
|------|------|---------------|------------------|
| `eval_v1_zvs` | 40 | `bible_verses.zo_tdb77`, `translations.source` (zo→en), `training_exercises.zolai`, `proverbs.zolai`, `phrases.zolai` | modulo-sampled rows (id % 3/5/11) → structural pre-filter (capitalised start, final `.`/`?`, 15–230 chars, no `{}`/brackets, no forbidden forms) → `zolai.zvs.validate` → 10 short (<70 chars) + 16 medium (70–130) + 14 long (>130), each bucket stride-sampled across the pool so sources and first words are spread |
| `eval_v1_qa` | 40 | `bible_verses.zo_tdb77`, `training_exercises.zolai`, `translations` (both directions), `proverbs`, `phrases` | extractive triples: `hyp` is a verbatim DB sentence, `answer` is a span inside it (whole-word match, ≤2 uses per answer, unique answers for the who-template), `question` derives from that same sentence by one of two templates attested in `smoke_qa.jsonl`: **(a)** `<Name> in <VP> hi.` → `Kua in <VP> hiam?` (22 rows; agent must be a single token that recurs capitalised mid-clause — a name heuristic — and is not a pronoun/common noun), **(b)** `<NP> a <pred> [ahi] hi.` → `<NP> a bang hi hiam?` (18 rows; NP must be a plain noun phrase, no clause markers) |
| `eval_v1_translation` | 30 | `translations` (`direction='zo_to_en'` and `'en_to_zo'`) | 1767 + 1768 candidate EN/ZO pairs (id % 7) → ZO side must be a complete ZVS-valid sentence → stride-sampled 15 per direction. Record shape follows `smoke_translation.jsonl`: `source` = English, `hyp` == `ref` = Zolai (the gold self-consistency harness; drop in model outputs to replace `hyp`) |

Integrity checks run on the committed files (and re-asserted by the builder):
exact row counts (40/40/30), unique `text`/`question`/`hyp`, `answer` ⊂ `hyp`
as whole words, `hyp == ref`, non-empty fields, `zvs.validate` on every ZVS
string, question and hyp. CLI on the set (with `report/eval-baseline.json`
floors): `zvs_compliance_rate=1.0, translation_bleu=1.0, translation_chrf=1.0,
qa_term_recall=1.0`, gate exit 0.

Open follow-up: KR3.3 still needs human-annotated **gold** sets (these are
DB-derived fixtures, not speaker-validated gold).

## `smoke` set (task fixtures)

All Zolai strings in the `smoke_translation.jsonl` set are **verbatim** corpus
strings (or explicitly-marked owner-native canonical forms). No Zolai was
hand-written. hyp == ref on every record: this is the designed gold
self-consistency harness (identical hypothesis and reference).

Provenance key: `data/clean/parallel.jsonl` line number(s) (TDB_KJV) and/or
`data/corpus/bible/markdown/Genesis_Parallel.md` verse. EN/ZO columns copied
exactly from the corpus (byte-for-byte, including curly quotes).

| # | Topic | ref (Zolai) | Source | English |
|---|-------|-------------|--------|---------|
| 1 | **create — `piangsak` guard** | `A kipat cil-in Pasian in vantung le leitung a piangsak hi.` | **verbatim** `parallel.jsonl` line 8 (TDB_KJV) · Gen 1:1 | In the beginning God created the heaven and the earth. |
| 2 | **sunrise — `suak` guard** | `Ni a suak hi.` | **owner-native canonical** (native speaker confirmation). No single verbatim Genesis sunrise line exists with `suak`; stem `suak` (distinct from `suah→chuak`) is ZVS-valid. Documented for verifiability. | The sun rises. |
| 3 | sun + moon + stars (Gen 1:16) | `Pasian in khuavak lian nih bawl a, ... amah in aksite zong a bawl hi.` | **verbatim** `parallel.jsonl` line 23 (TDB_KJV) | And God made two great lights; the greater light to rule the day, ... he made the stars also. |
| 4 | rain (Gen 7:12) | `Sun sawmli le zan sawmli sung leitungah guah zu hi.` | **verbatim** `parallel.jsonl` line 157 (TDB_KJV) | And the rain was upon the earth forty days and forty nights. |
| 5 | come here (Gen 24:31) | `Hong pai in.` | **verbatim** embedded phrase in `parallel.jsonl` line 3164 (TDB77) / `Genesis_Parallel.md` line 3164 | ...Come in, thou blessed of the Lord... |
| 6 | water (Gen 1:9) | `Pasian in, “Vantungte nuai-a om tuite mun khatah kikhawm hen la, lei keu kidawk hen,” ci hi. Tua mah bangin a piang pah hi.` | **verbatim** `parallel.jsonl` line 16 (TDB_KJV) | And God said, Let the waters under the heaven be gathered together unto one place, and let the dry land appear... |
| 7 | house (Gen 24:28) | `Tua ciangin nungaknu a inn-ah tai-in hih thute khempeuh a innkuanpihte tungah a gen hi.` | **verbatim** `parallel.jsonl` line 580 (TDB_KJV) | And the damsel ran, and told them of her mother's house these things. |
| 8 | children (Gen 4:25) | `Adam in a zi luppih leuleu hi. Amah in tapa khat nei a, a min Seth ci hi.` | **verbatim** `parallel.jsonl` line 93 (TDB_KJV) | ...she bare a son, and called his name Seth... |
| 9 | food / bread (Gen 43:31) | `Tua ciangin amah in a mai phiatin hong paikhia hi. Amah ki-ip tawmin, “An hong pia un,” a ci hi.` | **verbatim** `parallel.jsonl` line 1268 (TDB_KJV) | ...and said, Set on bread. |
| 10 | light (Gen 1:3) | `Pasian in, “Khuavak om hen,” ci hi; tua ciangin khuavak om pah hi.` | **verbatim** `parallel.jsonl` line 10 (TDB_KJV) | And God said, Let there be light: and there was light. |
| 11 | day / night / morning (Gen 1:5) | `Pasian in khuavak pen “Sun” ci a, khuamial pen “Zan” ci hi. Nitak hong bei-in, zingsang hong tung a, ni khat ni ahi hi.` | **verbatim** `parallel.jsonl` line 12 (TDB_KJV) | And God called the light Day, and the darkness he called Night. |
| 12 | earth / seas (Gen 1:10) | `Pasian in lei keu pen “Leitung” ci a, a kikaikhawm tuite pen “Tuipi” ci hi. Pasian in tua pen hoih hi, ci-in a mu hi.` | **verbatim** `parallel.jsonl` line 17 (TDB_KJV) | And God called the dry land Earth; and the gathering together of the waters called he Seas... |

## Verb alignment in sibling sets

- `smoke_zvs.jsonl` line 1: `... a piangsak hi.` (was `phuak` — semantic fix, no
  validator change). Line 3: `Ni a suak veve hi.` (was `chuak` — sunrise stem is
  `suak`, distinct from `suah→chuak`; already ZVS-valid).
- `smoke_qa.jsonl` line 1: question + hyp use `piangsak` (was `phuak`).

The `suah→chuak` mapping in `rules_data.py` means "go out/exit"; the sunrise
stem `suak` is a **separate, non-forbidden** lemma, so no validator change was
required. `phuak` was a semantic error, not orthographic — corrected via
fixtures only.