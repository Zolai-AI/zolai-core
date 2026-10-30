import json
import sys
from typing import Dict, List

import requests
from transformers import pipeline

from store import connect, upsert_edge, upsert_node

# Configuration
GEMINI_SERVER_URL = "http://localhost:8000/v1/chat/completions"

# Initialize NER (mBERT or XLM-R)
# Note: 'dslim/bert-base-NER' is English-centric but supports multilingual context via mBERT fine-tuning
# For Zolai, a multilingual model like xlm-roberta-large-ner-hrl is often preferred
ner_pipeline = pipeline("ner", model="xlm-roberta-large-ner-hrl", aggregation_strategy="simple")

def get_relationships(text: str, entities: List[Dict]) -> List[Dict]:
    """Calls local gemini-server to extract predicates between entities."""
    prompt = f"""
    Text: {text}
    Entities: {', '.join([e['word'] for e in entities])}
    Extract relationships in ZSP format: [Subject] --[Predicate]--> [Object]
    Output JSON list only: [{"s": "...", "p": "...", "o": "..."}]
    """
    try:
        resp = requests.post(GEMINI_SERVER_URL, json={
            "model": "gemini-pro",
            "messages": [{"role": "user", "content": prompt}]
        })
        return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"RE Error: {e}")
        return []

def save_to_kg(triples: List[Dict]):
    """Write triples into the canonical ``kg_nodes`` / ``kg_edges`` tables."""
    conn = connect()
    try:
        for t in triples:
            start = upsert_node(conn, label=t["s"], source_id=f"zolai:{t['s']}")
            end = upsert_node(conn, label=t["o"], source_id=f"zolai:{t['o']}")
            upsert_edge(conn, source_row=start, target_row=end, relation=t["p"])
        conn.commit()
    finally:
        conn.close()

def process_text(text: str):
    print(f"Processing: {text[:50]}...")
    ner_results = ner_pipeline(text)
    print(f"Extracted {len(ner_results)} entities.")

    relationships = get_relationships(text, ner_results)
    if isinstance(relationships, str):
        relationships = json.loads(relationships)

    save_to_kg(relationships)
    print(f"Saved {len(relationships)} triples to the canonical kg_nodes / kg_edges tables.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_text(sys.argv[1])
