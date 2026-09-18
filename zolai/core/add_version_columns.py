"""
Add version tracking columns to existing tables for JSONL import pipeline.
"""
import sqlite3

from zolai.config import config
DB_PATH = str(config.paths.zolai_db)

# Tables that will receive JSONL imports
TABLES_TO_UPDATE = [
    "bible_verses",
    "word_alignments",
    "grammar_patterns",
    "phrases",
    "vocab",
    "zolai_vocabulary",
    "phrases_from_bible",
    "translations",
    "training_exercises",
    "proverbs",
    "word_collocations",
    "word_usage",
    "dictionary",
    "dictionary_en_zo",
    "zvs_corrections",
    "training_corpus_qwen3",
    "training_valid_sentences",
    "training_seed_data",
    "bible_book_analysis",
    "bible_chapter_analysis",
    "phrase_context",
    "topic_clusters",
    "sentence_patterns",
    "word_usage_profiles",
    "dictionary_my",
    "dictionary_en_my",
    "dictionary_trilingual",
]

def add_version_columns():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for table in TABLES_TO_UPDATE:
        try:
            # Check if table exists
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                print(f"Skipping {table} - does not exist")
                continue

            # Check if columns already exist
            cur.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]

            columns_to_add = []
            if "import_batch_id" not in cols:
                columns_to_add.append(("import_batch_id", "TEXT"))
            if "source_file" not in cols:
                columns_to_add.append(("source_file", "TEXT"))
            if "version" not in cols:
                columns_to_add.append(("version", "INTEGER DEFAULT 1"))
            if "imported_at" not in cols:
                columns_to_add.append(("imported_at", "TEXT"))

            for col_name, col_type in columns_to_add:
                try:
                    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN {col_name} {col_type}')
                    print(f"Added {col_name} to {table}")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        print(f"Error adding {col_name} to {table}: {e}")

            if columns_to_add:
                # Create index on import_batch_id
                try:
                    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_batch ON "{table}"(import_batch_id)')
                    print(f"Created index on import_batch_id for {table}")
                except sqlite3.OperationalError:
                    pass

        except Exception as e:
            print(f"Error processing {table}: {e}")

    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    add_version_columns()
