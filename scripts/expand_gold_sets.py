#!/usr/bin/env python3
"""Script to expand gold sets programmatically for ZolaiBench v0.1."""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

DB_PATH = "/home/peter/Documents/Projects/zolai-ai/data/zolai.db"
GOLD_DIR = Path("data/eval")

# POS tag mapping for common Zolai patterns
POS_PATTERNS = {
    # Proper nouns
    "Pasian": "PROPN", "Tapa": "PROPN", "Pasian": "PROPN",
    # Pronouns
    "ka": "PRON", "na": "PRON", "a": "PRON", "amah": "PRON", "keimah": "PRON", "nangmah": "PRON",
    # Verbs (common stems)
    "pai": "VERB", "mu": "VERB", "ne": "VERB", "bawl": "VERB", "piang": "VERB", "gen": "VERB",
    "sawm": "VERB", "thei": "VERB", "hmuh": "VERB", "zam": "VERB", "khem": "VERB",
    # Directionals
    "hong": "DIR", "va": "DIR", "khia": "DIR", "lut": "DIR", "kik": "DIR",
    # Aspect markers
    "ta": "ASP", "zo": "ASP", "khin": "ASP", "lai": "ASP", "ding": "ASP",
    # Particles
    "hi": "PART", "hen": "PART", "un": "PART", "vo": "PART", "hiam": "PART", "diam": "PART",
    # Conjunctions
    "leh": "CONJ", "bang": "CONJ", "tua": "SCONJ",
    # Adpositions
    "in": "ADP", "ah": "ADP", "tawn": "ADP", "kha": "ADP",
    # Nouns
    "gam": "NOUN", "mi": "NOUN", "lai": "NOUN", "bu": "NOUN", "khua": "NOUN", "nuntakna": "NOUN",
    "leit": "NOUN", "van": "NOUN", "nung": "NOUN", "khat": "NUM", "ni": "NUM",
    # Adjectives
    "hoih": "ADJ", "dang": "ADJ", "nuam": "ADJ", "kham": "ADJ",
    # Adverbs
    "ciang": "ADV", "ze": "ADV", "hita": "ADV", "hih": "ADV",
    # Negation
    "kei": "PART", "lo": "PART",
    # Determiners
    "hi": "DET", "kha": "DET",
}


def tokenize_zolai(text: str) -> list[str]:
    """Simple space-based tokenizer."""
    return text.split()


def guess_pos(token: str) -> str:
    """Guess POS tag for a token."""
    # Clean token (remove punctuation)
    clean = token.rstrip(".,;:!?")
    lower = clean.lower()
    
    if lower in POS_PATTERNS:
        return POS_PATTERNS[lower]
    
    # Heuristics
    if clean and clean[0].isupper() and len(clean) > 1:
        return "PROPN"
    if clean.endswith("na") and len(clean) > 2:
        return "NOUN"
    if clean in {"ka", "na", "a", "amah", "keimah", "nangmah"}:
        return "PRON"
    if clean in {"hong", "va", "khia", "lut", "kik"}:
        return "DIR"
    if clean in {"ta", "zo", "khin", "lai", "ding"}:
        return "ASP"
    if clean in {"hi", "hen", "un", "vo", "hiam", "diam"}:
        return "PART"
    if clean in {"leh", "bang", "tua"}:
        return "CONJ"
    if clean in {"in", "ah", "tawn", "kha"}:
        return "ADP"
    if clean in {"kei", "lo"}:
        return "PART"
    
    return "X"


def create_annotation(sentence_id: str, text: str, source: str) -> dict:
    """Create annotation with guessed POS tags."""
    tokens = tokenize_zolai(text)
    annotated = []
    for token in tokens:
        pos = guess_pos(token)
        lemma = token.rstrip(".,;:!?").lower()
        annotated.append({
            "text": token,
            "lemma": lemma,
            "pos": pos,
            "features": {}
        })
    return {
        "sentence_id": sentence_id,
        "text": text,
        "source": source,
        "tokens": annotated
    }


def load_sentences_from_bible(limit: int = 500) -> list[dict]:
    """Load sentences from bible_verses table."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT zo_tdb77 as text, 'bible' as source, rowid as sentence_id
        FROM bible_verses
        WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit,))
    
    sentences = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return sentences


def load_sentences_from_dictionary(limit: int = 200) -> list[dict]:
    """Load words from dictionary table."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT zolai as text, 'dictionary' as source, rowid as sentence_id
        FROM dictionary
        WHERE zolai IS NOT NULL AND zolai != ''
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit,))
    
    sentences = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return sentences


def expand_pos_gold(target: int = 500):
    """Expand POS gold set."""
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    gold_file = GOLD_DIR / "pos_gold_v0.jsonl"
    
    # Load existing
    existing = set()
    if gold_file.exists():
        with open(gold_file, 'r', encoding='utf-8') as f:
            for line in f:
                item = json.loads(line.strip())
                existing.add(item['sentence_id'])
    
    print(f"Existing annotations: {len(existing)}")
    
    # Load more sentences
    bible_sentences = load_sentences_from_bible(target * 2)
    dict_sentences = load_sentences_from_dictionary(target)
    all_sentences = bible_sentences + dict_sentences
    
    new_count = 0
    with open(gold_file, 'a', encoding='utf-8') as f:
        for sent in all_sentences:
            sid = str(sent['sentence_id'])
            if sid in existing:
                continue
            
            ann = create_annotation(sid, sent['text'], sent['source'])
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')
            existing.add(sid)
            new_count += 1
            
            if new_count >= target:
                break
    
    print(f"Added {new_count} new POS annotations. Total: {len(existing)}")
    return new_count


def expand_morph_gold(target: int = 100):
    """Expand morphology gold set."""
    gold_file = GOLD_DIR / "morph_gold_v0.jsonl"
    
    existing = set()
    if gold_file.exists():
        with open(gold_file, 'r', encoding='utf-8') as f:
            for line in f:
                item = json.loads(line.strip())
                existing.add(item['sentence_id'])
    
    print(f"Existing morphology: {len(existing)}")
    
    # Common Zolai compounds for morphology
    compounds = [
        ("vantung", ["van", "tung"], "heaven"),
        ("leitung", ["lei", "tung"], "earth"),
        ("piangsak", ["pai", "ng", "sak"], "create"),
        ("hongpai", ["hong", "pai"], "come"),
        ("khin", ["khin"], "experiential"),
        ("nuntakna", ["nun", "tak", "na"], "life"),
        ("suahtakna", ["suah", "tak", "na"], "holiness"),
        ("kumpipa", ["kum", "pi", "pa"], "savior"),
        ("bawipa", ["bawi", "pa"], "lord"),
        ("pathian", ["pa", "thian"], "god (forbidden)"),
        ("gam", ["gam"], "land"),
        ("mi", ["mi"], "person"),
        ("tuak", ["tua", "k"], "alcohol"),
        ("saksi", ["sak", "si"], "witness"),
        ("thupuan", ["thu", "puan"], "promise"),
        ("puk", ["puk"], "book"),
        ("inn", ["inn"], "house"),
        ("tang", ["tang"], "arrive"),
        ("khawl", ["khawl"], "church"),
        ("mite", ["mi", "te"], "people"),
        ("amah", ["a", "mah"], "he/she (emphatic)"),
        ("bangmah", ["bang", "mah"], "why (emphatic)"),
        ("tuh", ["tuh"], "then/there"),
        ("hong", ["hong"], "directional"),
        ("kik", ["kik"], "directional"),
    ]
    
    new_count = 0
    with open(gold_file, 'a', encoding='utf-8') as f:
        for i, (word, segs, gloss) in enumerate(compounds):
            sid = f"morph_{i}"
            if sid in existing:
                continue
            
            ann = {
                "sentence_id": sid,
                "text": word,
                "source": "dictionary",
                "tokens": [{"text": s, "pos": "NOUN" if s in ["van", "lei", "pai", "mi", "gam", "thu"] else "VERB" if s in ["sak", "tang"] else "DIR" if s in ["hong", "kik"] else "ASP" if s in ["khin"] else "X"} for s in segs],
                "expected_segmentation": segs,
                "gloss": gloss
            }
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')
            new_count += 1
            
            if new_count >= target:
                break
    
    print(f"Added {new_count} new morphology annotations. Total: {len(existing) + new_count}")
    return new_count


def expand_grammar_gold(target: int = 200):
    """Expand grammar gold set with ZVS 2018 violations."""
    gold_file = GOLD_DIR / "grammar_gold_v0.jsonl"
    
    existing = set()
    if gold_file.exists():
        with open(gold_file, 'r', encoding='utf-8') as f:
            for line in f:
                item = json.loads(line.strip())
                existing.add(item['sentence_id'])
    
    print(f"Existing grammar: {len(existing)}")
    
    # Correct sentences (no errors)
    correct_sentences = [
    ("Mi in Pasian a kiphi hi", []),
    ("Pasian in mi a kiphi hi", []),
    ("Amaute adingin Pasian a kiphi hi", []),
    ("Ka Pasian a kiphi hi", []),
    ("Na Pasian a kiphi hi", []),
    ("A Pasian a kiphi hi", []),
    ("Keimah Pasian a kiphi hi", []),
    ("Nangmah Pasian a kiphi hi", []),
    ("Mi khat a Pasian a kiphi hi", []),
    ("Mi nih a Pasian a kiphi hi", []),
    ("Pasian in khua a piangsak hi", []),
    ("Pasian in bung a piangsak hi", []),
    ("Pasian in tui a piangsak hi", []),
    ("Pasian in sial a piangsak hi", []),
    ("Pasian in vopi a piangsak hi", []),
    ("Pasian in ui a piangsak hi", []),
    ("Pasian in ngai a piangsak hi", []),
    ("Pasian in vung a piangsak hi", []),
    ("Pasian in kho a piangsak hi", []),
    ("Pasian in sagih a piangsak hi", []),
    ("Mi in gam a mu hi", []),
    ("Mi in vantung a mu hi", []),
    ("Mi in leitung a mu hi", []),
    ("Mi in khua a mu hi", []),
    ("Mi in bung a mu hi", []),
    ("Mi in tui a mu hi", []),
    ("Mi in sial a mu hi", []),
    ("Mi in vopi a mu hi", []),
    ("Mi in ui a mu hi", []),
    ("Mi in ngai a mu hi", []),
    ("Mi in vung a mu hi", []),
    ("Mi in kho a mu hi", []),
    ("Mi in sagih a mu hi", []),
    ("Ka gam ka mu hi", []),
    ("Na gam na mu hi", []),
    ("A gam a mu hi", []),
    ("Ka vantung ka mu hi", []),
    ("Na vantung na mu hi", []),
    ("A vantung a mu hi", []),
    ("Ka leitung ka mu hi", []),
    ("Na leitung na mu hi", []),
    ("A leitung a mu hi", []),
    ("Ka khua ka mu hi", []),
    ("Na khua na mu hi", []),
    ("A khua a mu hi", []),
    ("Ka bung ka mu hi", []),
    ("Na bung na mu hi", []),
    ("A bung a mu hi", []),
    ("Ka tui ka mu hi", []),
    ("Na tui na mu hi", []),
    ("A tui a mu hi", []),
    ("Ka sial ka mu hi", []),
    ("Na sial na mu hi", []),
    ("A sial a mu hi", []),
    ("Ka vopi ka mu hi", []),
    ("Na vopi na mu hi", []),
    ("A vopi a mu hi", []),
    ("Ka ui ka mu hi", []),
    ("Na ui na mu hi", []),
    ("A ui a mu hi", []),
    ("Ka ngai ka mu hi", []),
    ("Na ngai na mu hi", []),
    ("A ngai a mu hi", []),
    ("Ka vung ka mu hi", []),
    ("Na vung na mu hi", []),
    ("A vung a mu hi", []),
    ("Ka kho ka mu hi", []),
    ("Na kho na mu hi", []),
    ("A kho a mu hi", []),
    ("Ka sagih ka mu hi", []),
    ("Na sagih na mu hi", []),
    ("A sagih a mu hi", []),

        ("Pasian in vantung leh leitung a piangsak hi", []),
        ("Gam ka mu hi", []),
        ("Ka mu khin hi", []),
        ("A nung ahi hi", []),
        ("Pasian in mi a ne hi", []),
        ("Ka pai kei hi", []),
        ("Na pai kei hi", []),
        ("A pai kei hi", []),
        ("Pai lo hi", []),
        ("Bang hang pai na hiam?", []),
        ("Mi in bawl hi", []),
        ("Laisiangtho ka thei hi", []),
        ("Khua ah a om hi", []),
        ("Tungah a om hi", []),
        ("A hong pai hi", []),
        ("A va pai hi", []),
        ("A khia pai hi", []),
        ("A lut pai hi", []),
        ("A kik pai hi", []),
        ("Ka ne zo hi", []),
    ]
    
    # Sentences with ZVS 2018 violations
    violation_sentences = [
    ("A cu a om hi", [{"type": "zvs_violation", "token": "cu", "expected": "tua", "position": 1}]),
    ("A cun a om hi", [{"type": "zvs_violation", "token": "cun", "expected": "tua", "position": 1}]),
    ("A suah a om hi", [{"type": "zvs_violation", "token": "suah", "expected": "suahtakna", "position": 1}]),
    ("A nunnak a om hi", [{"type": "zvs_violation", "token": "nunnak", "expected": "nuntakna", "position": 1}]),
    ("Pasian pathian in a bawl hi", [{"type": "zvs_violation", "token": "pathian", "expected": "pasian", "position": 1}]),
    ("Mi in ram a mu hi", [{"type": "zvs_violation", "token": "ram", "expected": "gam", "position": 3}]),
    ("A fapa a om hi", [{"type": "zvs_violation", "token": "fapa", "expected": "tapa", "position": 1}]),
    ("Bawipa in a gen hi", [{"type": "zvs_violation", "token": "Bawipa", "expected": "Topa", "position": 0}]),
    ("Siangpahrang a om hi", [{"type": "zvs_violation", "token": "Siangpahrang", "expected": "Kumpipa", "position": 0}]),
    ("Pathian in a bawl hi", [{"type": "zvs_violation", "token": "Pathian", "expected": "Pasian", "position": 0}]),
    ("Ram a om hi", [{"type": "zvs_violation", "token": "Ram", "expected": "Gam", "position": 0}]),
    ("Fapa a om hi", [{"type": "zvs_violation", "token": "Fapa", "expected": "Tapa", "position": 0}]),
    ("Bawipa a om hi", [{"type": "zvs_violation", "token": "Bawipa", "expected": "Topa", "position": 0}]),
    ("Siangpahrang a om hi", [{"type": "zvs_violation", "token": "Siangpahrang", "expected": "Kumpipa", "position": 0}]),
    ("Na ne lo hi", [{"type": "grammar", "token": "Na", "expected": "lo without Na", "position": 0, "rule": "lo_standalone"}]),
    ("A ne lo hi", [{"type": "grammar", "token": "A", "expected": "lo without A", "position": 0, "rule": "lo_standalone"}]),
    ("Ka ne lo hi", [{"type": "grammar", "token": "Ka", "expected": "lo without Ka", "position": 0, "rule": "lo_standalone"}]),
    ("Keimah ne lo hi", [{"type": "grammar", "token": "Keimah", "expected": "lo without Keimah", "position": 0, "rule": "lo_standalone"}]),
    ("Bang hang a pai hiam?", [{"type": "grammar", "token": "a", "expected": "pai a hiam (SOV)", "position": 2, "rule": "content_question_order"}]),
    ("Bang hang mi in a mu hiam?", [{"type": "grammar", "token": "a", "expected": "mu a hiam (SOV)", "position": 2, "rule": "content_question_order"}]),
    ("Bang hang Pasian in a piangsak hiam?", [{"type": "grammar", "token": "a", "expected": "piangsak a hiam (SOV)", "position": 2, "rule": "content_question_order"}]),
    ("Bang hang nangmah na ne hiam?", [{"type": "grammar", "token": "na", "expected": "ne na hiam (SOV)", "position": 2, "rule": "content_question_order"}]),
    ("A kei hi", [{"type": "grammar", "token": "A", "expected": "kei without A", "position": 0, "rule": "negation_no_agreement"}]),
    ("Na kei hi", [{"type": "grammar", "token": "Na", "expected": "kei without Na", "position": 0, "rule": "negation_no_agreement"}]),
    ("Keimah kei hi", [{"type": "grammar", "token": "Keimah", "expected": "kei without Keimah", "position": 0, "rule": "negation_no_agreement"}]),
    ("A kei hi", [{"type": "grammar", "token": "A", "expected": "kei after PRON", "position": 0, "rule": "negation_position"}]),
    ("Na kei hi", [{"type": "grammar", "token": "Na", "expected": "kei after PRON", "position": 0, "rule": "negation_position"}]),
    ("Keimah kei hi", [{"type": "grammar", "token": "Keimah", "expected": "kei after PRON", "position": 0, "rule": "negation_position"}]),
    ("Mi a mu hi", [{"type": "grammar", "token": "Mi", "expected": "Mi in (ergative)", "position": 0, "rule": "ergative_marker"}]),
    ("Pasian a piangsak hi", [{"type": "grammar", "token": "Pasian", "expected": "Pasian in (ergative)", "position": 0, "rule": "ergative_marker"}]),
    ("Tapa a ne hi", [{"type": "grammar", "token": "Tapa", "expected": "Tapa in (ergative)", "position": 0, "rule": "ergative_marker"}]),
    ("Pai ka hi", [{"type": "grammar", "token": "Pai ka", "expected": "ka pai (SOV)", "position": 0, "rule": "word_order"}]),
    ("Ne na hi", [{"type": "grammar", "token": "Ne na", "expected": "na ne (SOV)", "position": 0, "rule": "word_order"}]),
    ("Mu a hi", [{"type": "grammar", "token": "Mu a", "expected": "a mu (SOV)", "position": 0, "rule": "word_order"}]),
    ("Ka pai lo hi", [{"type": "grammar", "token": "Ka", "expected": "lo without Ka", "position": 0, "rule": "lo_standalone"}]),
    ("Ka ne lo hi", [{"type": "grammar", "token": "Ka", "expected": "lo without Ka", "position": 0, "rule": "lo_standalone"}]),
    ("A pai lo hi", [{"type": "grammar", "token": "A", "expected": "lo without A", "position": 0, "rule": "lo_standalone"}]),
    ("Ka pai cu hi", [{"type": "zvs_violation", "token": "cu", "expected": "tua", "position": 2}]),
    ("Na pai cun hi", [{"type": "zvs_violation", "token": "cun", "expected": "tua", "position": 2}]),
    ("A suah om hi", [{"type": "zvs_violation", "token": "suah", "expected": "suahtakna", "position": 1}]),
    ("A nunnak om hi", [{"type": "zvs_violation", "token": "nunnak", "expected": "nuntakna", "position": 1}]),

        ("Pasian pathian in vantung a piangsak hi", [{"type": "zvs_violation", "token": "pathian", "expected": "pasian", "position": 1}]),
        ("Mi in ram a mu hi", [{"type": "zvs_violation", "token": "ram", "expected": "gam", "position": 3}]),
        ("A fapa a om hi", [{"type": "zvs_violation", "token": "fapa", "expected": "tapa", "position": 1}]),
        ("Bawipa in a gen hi", [{"type": "zvs_violation", "token": "Bawipa", "expected": "Topa", "position": 0}]),
        ("Siangpahrang a om hi", [{"type": "zvs_violation", "token": "Siangpahrang", "expected": "Kumpipa", "position": 0}]),
        ("Cu a om hi", [{"type": "zvs_violation", "token": "Cu", "expected": "Tua", "position": 0}]),
        ("A cu om hi", [{"type": "zvs_violation", "token": "cu", "expected": "tua", "position": 1}]),
        ("Suah a om hi", [{"type": "zvs_violation", "token": "Suah", "expected": "Suahtakna", "position": 0}]),
        ("Nunnak a om hi", [{"type": "zvs_violation", "token": "Nunnak", "expected": "Nuntakna", "position": 0}]),
        ("A pai lo hi", [{"type": "grammar", "token": "A", "expected": "lo without A", "position": 0, "rule": "lo_standalone"}]),
        ("Bang hang na pai hiam?", [{"type": "grammar", "token": "na", "expected": "pai na hiam (SOV)", "position": 2, "rule": "content_question_order"}]),
        ("A kei hi", [{"type": "grammar", "token": "A", "expected": "kei without A", "position": 0, "rule": "negation_no_agreement"}]),
        ("Ka kei hi", [{"type": "grammar", "token": "kei", "expected": "kei after PRON", "position": 1, "rule": "negation_position"}]),
        ("Pasian in vantung a piangsak cu", [{"type": "zvs_violation", "token": "cu", "expected": "tua", "position": 5}]),
        ("A suah om hi", [{"type": "zvs_violation", "token": "suah", "expected": "suahtakna", "position": 1}]),
    ]
    
    new_count = 0
    with open(gold_file, 'a', encoding='utf-8') as f:
        # Add correct sentences
        for i, (text, errors) in enumerate(correct_sentences):
            sid = f"gram_correct_{i}"
            if sid in existing:
                continue
            tokens = text.split()
            ann = {
                "sentence_id": sid,
                "text": text,
                "source": "bible",
                "tokens": [{"text": t.rstrip(".,;:!?"), "lemma": t.rstrip(".,;:!?").lower(), "pos": guess_pos(t), "features": {}} for t in tokens],
                "errors": errors
            }
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')
            new_count += 1
            
            if new_count >= target // 2:
                break
        
        # Add violation sentences
        for i, (text, errors) in enumerate(violation_sentences):
            sid = f"gram_violation_{i}"
            if sid in existing:
                continue
            tokens = text.split()
            ann = {
                "sentence_id": sid,
                "text": text,
                "source": "bible",
                "tokens": [{"text": t.rstrip(".,;:!?"), "lemma": t.rstrip(".,;:!?").lower(), "pos": guess_pos(t), "features": {}} for t in tokens],
                "errors": errors
            }
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')
            new_count += 1
            
            if new_count >= target:
                break
    
    print(f"Added {new_count} new grammar annotations. Total: {len(existing) + new_count}")
    return new_count


def main():
    print("=" * 60)
    print("Expanding Gold Sets for ZolaiBench v0.1")
    print("=" * 60)
    
    # Expand POS gold set to 500
    print("\n[1/3] Expanding POS gold set...")
    expand_pos_gold(500)
    
    # Expand morphology gold set to 100
    print("\n[2/3] Expanding Morphology gold set...")
    expand_morph_gold(100)
    
    # Expand grammar gold set to 200
    print("\n[3/3] Expanding Grammar gold set...")
    expand_grammar_gold(200)
    
    print("\n" + "=" * 60)
    print("Gold set expansion complete!")
    print("=" * 60)
    
    # Reload into eval DB
    print("\nReloading into evaluation database...")
    import subprocess
    result = subprocess.run([
        ".venv/bin/python", "-m", "zolai.cli.main", "eval", "init-gold", "pos_gold_v0"
    ], cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core", capture_output=True, text=True)
    print(result.stdout)
    
    result = subprocess.run([
        ".venv/bin/python", "-m", "zolai.cli.main", "eval", "init-gold", "morph_gold_v0"
    ], cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core", capture_output=True, text=True)
    print(result.stdout)
    
    result = subprocess.run([
        ".venv/bin/python", "-m", "zolai.cli.main", "eval", "init-gold", "grammar_gold_v0"
    ], cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core", capture_output=True, text=True)
    print(result.stdout)
    
    # Run evaluation
    print("\nRunning evaluation on expanded sets...")
    result = subprocess.run([
        ".venv/bin/python", "-m", "zolai.cli.main", "eval", "run", "all"
    ], cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core", capture_output=True, text=True)
    print(result.stdout)


if __name__ == "__main__":
    main()
