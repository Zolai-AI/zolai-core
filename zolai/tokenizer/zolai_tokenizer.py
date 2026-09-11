#!/usr/bin/env python3
"""Custom Zolai SentencePiece tokenizer.

Trains a unigram/BPE tokenizer on Zolai corpus, saves the model,
and provides encode/decode/coverage API.

Usage:
    python -m zolai.tokenizer.zolai_tokenizer train --data <jsonl> --vocab-size 8000 --output <prefix>
    python -m zolai.tokenizer.zolai_tokenizer encode --model <prefix.model> --text "..."
    python -m zolai.tokenizer.zolai_tokenizer decode --model <prefix.model> --ids "1 2 3"
    python -m zolai.tokenizer.zolai_tokenizer coverage --model <prefix.model> --data <jsonl>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

import sentencepiece as spm


class ZolaiTokenizer:
    """SentencePiece tokenizer wrapper for Zolai language."""

    def __init__(self, model_path: str | Path | None = None) -> None:
        self._sp = spm.SentencePieceProcessor()
        if model_path is not None:
            self.load(str(model_path))

    def load(self, model_path: str) -> None:
        """Load a pre-trained model."""
        self._sp.Load(model_path)

    def train(
        self,
        data_path: str,
        model_prefix: str,
        vocab_size: int = 8000,
        model_type: str = "unigram",
    ) -> None:
        """Train SentencePiece on a JSONL file containing Zolai text.

        Extracts all Zolai fields: zo_tdb77, zo_tedim2010, zolai.
        Writes a temporary plain-text file for SentencePiece training.
        """
        data_file = Path(data_path)
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {data_path}")

        # Extract Zolai text from JSONL
        tmp_dir = Path(model_prefix).parent / ".tmp_tokenizer"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_text = tmp_dir / "zolai_corpus.txt"

        count = 0
        with open(data_file, "r", encoding="utf-8") as f, open(
            tmp_text, "w", encoding="utf-8"
        ) as out:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Extract all Zolai text fields
                for key in ("zo_tdb77", "zo_tedim2010", "zolai"):
                    text = obj.get(key)
                    if text and isinstance(text, str) and text.strip():
                        out.write(text.strip() + "\n")
                        count += 1

        if count == 0:
            raise ValueError(f"No Zolai text found in {data_path}")

        print(f"Extracted {count} Zolai sentences to {tmp_text}")

        # Train SentencePiece
        spm.SentencePieceTrainer.Train(
            input=str(tmp_text),
            model_prefix=model_prefix,
            vocab_size=vocab_size,
            model_type=model_type,
            character_coverage=1.0,
            byte_fallback=True,
            normalization_rule_name="nmt_nfkc",
            # Zolai-specific options
            split_digits=False,
            allow_whitespace_only_pieces=True,
            remove_extra_whitespaces=False,
        )

        # Cleanup temp
        tmp_text.unlink(missing_ok=True)
        tmp_dir.rmdir()

        print(
            f"Trained {model_type} tokenizer: {model_prefix}.model "
            f"(vocab_size={vocab_size})"
        )

    def encode(self, text: str) -> List[int]:
        """Encode text to token IDs."""
        return self._sp.EncodeAsIds(text)

    def decode(self, ids: List[int]) -> str:
        """Decode token IDs to text."""
        return self._sp.DecodeIds(ids)

    def vocab_size(self) -> int:
        """Return vocabulary size."""
        return self._sp.GetPieceSize()

    def coverage(self, text: str) -> float:
        """Return fraction of characters covered by the vocabulary.

        A token is considered 'known' if it doesn't map to the
        unknown token piece (id 0 by default).
        """
        ids = self._sp.EncodeAsIds(text)
        if not ids:
            return 0.0
        unk_id = self._sp.unk_id()
        known = sum(1 for i in ids if i != unk_id)
        return known / len(ids)

    def id_to_piece(self, piece_id: int) -> str:
        """Return the piece string for a given ID."""
        return self._sp.IdToPiece(piece_id)

    def piece_to_id(self, piece: str) -> int:
        """Return the ID for a given piece string."""
        return self._sp.PieceToId(piece)

    @property
    def unk_id(self) -> int:
        return self._sp.unk_id()

    @property
    def bos_id(self) -> int:
        return self._sp.bos_id()

    @property
    def eos_id(self) -> int:
        return self._sp.eos_id()


def _load_jsonl_texts(path: str) -> List[str]:
    """Load Zolai text from a JSONL file."""
    texts: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("zo_tdb77", "zo_tedim2010", "zolai"):
                val = obj.get(key)
                if val and isinstance(val, str) and val.strip():
                    texts.append(val.strip())
    return texts


def cmd_train(args: argparse.Namespace) -> None:
    tok = ZolaiTokenizer()
    tok.train(
        data_path=args.data,
        model_prefix=args.output,
        vocab_size=args.vocab_size,
        model_type=args.model_type,
    )


def cmd_encode(args: argparse.Namespace) -> None:
    tok = ZolaiTokenizer(args.model)
    ids = tok.encode(args.text)
    print(" ".join(str(i) for i in ids))


def cmd_decode(args: argparse.Namespace) -> None:
    tok = ZolaiTokenizer(args.model)
    ids = [int(x) for x in args.ids.split()]
    print(tok.decode(ids))


def cmd_coverage(args: argparse.Namespace) -> None:
    tok = ZolaiTokenizer(args.model)
    texts = _load_jsonl_texts(args.data)
    if not texts:
        print("No Zolai text found.")
        sys.exit(1)

    total_chars = 0
    covered_chars = 0
    for text in texts:
        ids = tok.encode(text)
        unk_id = tok.unk_id
        known = sum(1 for i in ids if i != unk_id)
        covered_chars += known
        total_chars += len(ids)

    coverage_pct = (covered_chars / total_chars * 100) if total_chars else 0.0
    print(f"Coverage: {coverage_pct:.2f}% ({covered_chars}/{total_chars} tokens)")
    print(f"Vocabulary size: {tok.vocab_size()}")
    print(f"Corpus sentences: {len(texts)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zolai SentencePiece tokenizer"
    )
    sub = parser.add_subparsers(dest="command")

    # train
    p_train = sub.add_parser("train", help="Train tokenizer on Zolai JSONL")
    p_train.add_argument("--data", required=True, help="Path to JSONL file")
    p_train.add_argument("--output", required=True, help="Model prefix (e.g. data/tokenizer/zolai_spm)")
    p_train.add_argument("--vocab-size", type=int, default=8000, help="Vocabulary size")
    p_train.add_argument("--model-type", default="unigram", choices=["unigram", "bpe"])

    # encode
    p_enc = sub.add_parser("encode", help="Encode text to token IDs")
    p_enc.add_argument("--model", required=True, help="Path to .model file")
    p_enc.add_argument("--text", required=True, help="Text to encode")

    # decode
    p_dec = sub.add_parser("decode", help="Decode token IDs to text")
    p_dec.add_argument("--model", required=True, help="Path to .model file")
    p_dec.add_argument("--ids", required=True, help="Space-separated token IDs")

    # coverage
    p_cov = sub.add_parser("coverage", help="Compute vocabulary coverage on a corpus")
    p_cov.add_argument("--model", required=True, help="Path to .model file")
    p_cov.add_argument("--data", required=True, help="Path to JSONL file")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    {"train": cmd_train, "encode": cmd_encode, "decode": cmd_decode, "coverage": cmd_coverage}[
        args.command
    ](args)


if __name__ == "__main__":
    main()
