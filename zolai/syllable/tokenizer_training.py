"""SylBreak4All Milestone 8: Tokenizer Training with SentencePiece/FastText.

Trains syllable-based tokenizers for Zolai using SentencePiece (BPE, Unigram)
and FastText on the syllable corpus (189K words, 24MB).

Usage:
    python -m zolai.syllable.tokenizer_training --train-all \
        --vocab-sizes 1000,2000,4000,8000,16000,32000 --output-dir models/
    python -m zolai.syllable.tokenizer_training --evaluate \
        --model models/zolai_sp_8000.model --test data/syllable/splits/gold_test.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fasttext
    import sentencepiece as spm

log = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    import sentencepiece as spm

    _SENTENCEPIECE_AVAILABLE = True
except ImportError:
    _SENTENCEPIECE_AVAILABLE = False
    spm = None  # type: ignore

try:
    import fasttext

    _FASTTEXT_AVAILABLE = True
except ImportError:
    _FASTTEXT_AVAILABLE = False
    fasttext = None  # type: ignore


class SyllableTokenizerTrainer:
    """Train syllable-based tokenizers for Zolai."""

    def __init__(
        self,
        corpus_path: str = "data/syllable/splits/gold_train.jsonl",
        output_dir: str = "models",
    ):
        self.corpus_path = Path(corpus_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.corpus_text: str | None = None
        self.syllable_vocab: set[str] = set()

    def load_corpus(self) -> str:
        """Load and preprocess corpus for training.

        Reads JSONL, extracts syllables, joins with spaces for SentencePiece training.

        Returns:
            Preprocessed corpus text (syllables separated by spaces).
        """
        if self.corpus_text is not None:
            return self.corpus_text

        log.info("Loading corpus from %s", self.corpus_path)

        syllables_list: list[str] = []
        word_count = 0

        with self.corpus_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    syllables = data.get("syllables", [])
                    if syllables:
                        syllables_list.extend(syllables)
                        for syl in syllables:
                            self.syllable_vocab.add(syl)
                        word_count += 1
                except json.JSONDecodeError:
                    continue

        # Join syllables with spaces (SentencePiece expects space-separated tokens)
        self.corpus_text = " ".join(syllables_list)
        log.info(
            "Loaded %d words, %d syllables, %d unique syllables",
            word_count,
            len(syllables_list),
            len(self.syllable_vocab),
        )
        return self.corpus_text

    def write_corpus_text(self, text: str, temp_path: Path) -> None:
        """Write corpus text to temporary file for SentencePiece training."""
        temp_path.write_text(text, encoding="utf-8")

    def train_sentencepiece(
        self,
        vocab_size: int = 8000,
        model_type: str = "bpe",
        output_prefix: str | None = None,
        character_coverage: float = 1.0,
        input_sentence_size: int = 0,
        shuffle_input_sentence: bool = True,
    ) -> str:
        """Train SentencePiece model on syllable corpus.

        Args:
            vocab_size: Target vocabulary size.
            model_type: Model algorithm - "bpe", "unigram", "word", or "char".
            output_prefix: Output file prefix (default: models/zolai_sp_{vocab_size}).
            character_coverage: Character coverage for model (1.0 for Zolai).
            input_sentence_size: Max sentences to use (0 = all).
            shuffle_input_sentence: Shuffle input sentences.

        Returns:
            Path to trained model file.
        """
        if not _SENTENCEPIECE_AVAILABLE:
            raise ImportError(
                "sentencepiece is required. Install with: pip install sentencepiece"
            )

        if output_prefix is None:
            output_prefix = str(self.output_dir / f"zolai_sp_{vocab_size}")

        corpus_text = self.load_corpus()

        # Write corpus to temp file for SentencePiece
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(corpus_text)
            tmp_path = tmp.name

        try:
            log.info("Training SentencePiece (%s, vocab=%d)...", model_type, vocab_size)

            # Train model
            spm.SentencePieceTrainer.train(
                input=tmp_path,
                model_prefix=output_prefix,
                vocab_size=vocab_size,
                model_type=model_type,
                character_coverage=character_coverage,
                input_sentence_size=input_sentence_size,
                shuffle_input_sentence=shuffle_input_sentence,
                # Zolai-specific settings
                split_by_whitespace=True,  # Our corpus is space-separated syllables
                split_by_number=False,
                split_by_unicode_script=False,
                # Control symbols for special tokens
                control_symbols=["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"],
                user_defined_symbols=[],
                byte_fallback=True,
                unk_id=1,
                pad_id=0,
                bos_id=2,
                eos_id=3,
            )

            model_path = f"{output_prefix}.model"
            vocab_path = f"{output_prefix}.vocab"

            log.info("Model saved to %s", model_path)
            log.info("Vocab saved to %s", vocab_path)

            return model_path

        finally:
            # Clean up temp file
            os.unlink(tmp_path)

    def train_fasttext(
        self,
        dim: int = 100,
        model_type: str = "skipgram",
        output_path: str | None = None,
        epoch: int = 10,
        lr: float = 0.05,
        word_ngrams: int = 1,
        loss: str = "softmax",
        bucket: int = 2000000,
        min_count: int = 5,
        min_count_label: int = 0,
        minn: int = 0,
        maxn: int = 0,
        thread: int = 4,
        lr_update_rate: int = 100,
        t: float = 1e-4,
        label_prefix: str = "__label__",
        verbose: int = 2,
    ) -> str:
        """Train FastText on syllable corpus.

        Args:
            dim: Embedding dimension.
            model_type: "skipgram" or "cbow".
            output_path: Output model path (default: models/zolai_fasttext.bin).
            epoch: Number of epochs.
            lr: Learning rate.
            word_ngrams: Max length of word n-grams.
            loss: Loss function ("softmax", "ns", "hs").
            bucket: Number of buckets for hashing.
            min_count: Minimal number of word occurrences.
            minn: Min length of char n-gram.
            maxn: Max length of char n-gram.
            thread: Number of threads.
            lr_update_rate: Learning rate update rate.
            t: Sampling threshold.
            label_prefix: Label prefix.
            verbose: Verbosity level.

        Returns:
            Path to trained model file.
        """
        if not _FASTTEXT_AVAILABLE:
            raise ImportError(
                "fasttext is required. Install with: pip install fasttext"
            )

        if output_path is None:
            output_path = str(self.output_dir / "zolai_fasttext.bin")

        corpus_text = self.load_corpus()

        # Write corpus to temp file for FastText
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(corpus_text)
            tmp_path = tmp.name

        try:
            log.info("Training FastText (%s, dim=%d)...", model_type, dim)

            if model_type == "skipgram":
                model = fasttext.train_unsupervised(
                    input=tmp_path,
                    model="skipgram",
                    dim=dim,
                    epoch=epoch,
                    lr=lr,
                    wordNgrams=word_ngrams,
                    loss=loss,
                    bucket=bucket,
                    minCount=min_count,
                    minCountLabel=min_count_label,
                    minn=minn,
                    maxn=maxn,
                    thread=thread,
                    lrUpdateRate=lr_update_rate,
                    t=t,
                    labelPrefix=label_prefix,
                    verbose=verbose,
                )
            elif model_type == "cbow":
                model = fasttext.train_unsupervised(
                    input=tmp_path,
                    model="cbow",
                    dim=dim,
                    epoch=epoch,
                    lr=lr,
                    wordNgrams=word_ngrams,
                    loss=loss,
                    bucket=bucket,
                    minCount=min_count,
                    minCountLabel=min_count_label,
                    minn=minn,
                    maxn=maxn,
                    thread=thread,
                    lrUpdateRate=lr_update_rate,
                    t=t,
                    labelPrefix=label_prefix,
                    verbose=verbose,
                )
            else:
                raise ValueError(f"model_type must be 'skipgram' or 'cbow', got {model_type}")

            model.save_model(output_path)
            log.info("FastText model saved to %s", output_path)

            return output_path

        finally:
            os.unlink(tmp_path)

    def load_sentencepiece(self, model_path: str) -> "spm.SentencePieceProcessor":
        """Load a trained SentencePiece model."""
        if not _SENTENCEPIECE_AVAILABLE:
            raise ImportError("sentencepiece not available")

        sp = spm.SentencePieceProcessor()
        sp.load(model_path)
        return sp

    def load_fasttext(self, model_path: str) -> "fasttext.FastText._FastText":
        """Load a trained FastText model."""
        if not _FASTTEXT_AVAILABLE:
            raise ImportError("fasttext not available")

        return fasttext.load_model(model_path)

    def evaluate_tokenizer(
        self,
        model_path: str,
        test_words: list[str] | None = None,
        test_file: str | Path | None = None,
    ) -> dict:
        """Evaluate tokenizer: compression ratio, OOV rate, token count.

        Args:
            model_path: Path to SentencePiece model.
            test_words: List of words to test (optional).
            test_file: Path to test JSONL file (optional).

        Returns:
            Dictionary with evaluation metrics.
        """
        if not _SENTENCEPIECE_AVAILABLE:
            raise ImportError("sentencepiece not available")

        sp = self.load_sentencepiece(model_path)

        # Load test words
        if test_file:
            test_words = []
            with Path(test_file).open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        word = data.get("word", "")
                        if word:
                            test_words.append(word)
                    except json.JSONDecodeError:
                        continue

        if not test_words:
            # Default test words from Zolai
            test_words = [
                "pasian", "vantung", "leitung", "piangsak", "tapa", "topa",
                "kumpipa", "nuntakna", "suahtakna", "gam", "tui", "mi",
                "numei", "sing", "nek", "hiam", "kei", "lo", "ding", "ta",
                "laisiangtho", "bawl", "gen", "mu", "pai", "kal", "aw",
                "ngaih", "lungdam", "khawngaih", "thu", "kham", "zia",
            ]

        # Evaluate
        total_chars = 0
        total_tokens = 0
        oov_count = 0
        oov_words = []

        for word in test_words:
            # Tokenize with SentencePiece
            tokens = sp.encode_as_pieces(word)
            total_chars += len(word)
            total_tokens += len(tokens)

            # Check for OOV (unk token)
            ids = sp.encode_as_ids(word)
            if sp.unk_id() in ids:
                oov_count += 1
                oov_words.append(word)

        compression_ratio = total_chars / total_tokens if total_tokens > 0 else 0
        oov_rate = oov_count / len(test_words) if test_words else 0
        avg_tokens_per_word = total_tokens / len(test_words) if test_words else 0

        return {
            "model_path": model_path,
            "vocab_size": sp.get_piece_size(),
            "test_words": len(test_words),
            "total_chars": total_chars,
            "total_tokens": total_tokens,
            "compression_ratio": round(compression_ratio, 4),
            "oov_count": oov_count,
            "oov_rate": round(oov_rate, 4),
            "avg_tokens_per_word": round(avg_tokens_per_word, 4),
            "oov_words": oov_words[:20],  # Limit output
        }

    def evaluate_fasttext(
        self,
        model_path: str,
        test_pairs: list[tuple[str, str]] | None = None,
    ) -> dict:
        """Evaluate FastText embeddings with similarity tests.

        Args:
            model_path: Path to FastText model.
            test_pairs: List of (syllable1, syllable2) pairs to test similarity.

        Returns:
            Dictionary with evaluation metrics.
        """
        if not _FASTTEXT_AVAILABLE:
            raise ImportError("fasttext not available")

        model = self.load_fasttext(model_path)

        if test_pairs is None:
            # Default test pairs for Zolai syllables
            test_pairs = [
                ("pa", "sian"),
                ("van", "tung"),
                ("lei", "tung"),
                ("piang", "sak"),
                ("bawl", "sak"),
                ("nun", "tak"),
                ("suah", "tak"),
                ("dam", "na"),
                ("in", "hong"),
                ("ka", "na"),
            ]

        results = []
        for syl1, syl2 in test_pairs:
            try:
                sim = model.get_word_vector(syl1)
                sim2 = model.get_word_vector(syl2)
                # Cosine similarity
                import numpy as np
                sim_score = float(np.dot(sim, sim2) / (np.linalg.norm(sim) * np.linalg.norm(sim2)))
                results.append({"pair": f"{syl1}-{syl2}", "similarity": round(sim_score, 4)})
            except Exception:
                results.append({"pair": f"{syl1}-{syl2}", "similarity": None})

        # Vocab coverage
        vocab_size = len(model.words)

        return {
            "model_path": model_path,
            "vocab_size": vocab_size,
            "dim": model.get_dimension(),
            "similarities": results,
        }

    def train_all_variants(
        self,
        vocab_sizes: list[int] | None = None,
        model_types: list[str] | None = None,
        fasttext_dims: list[int] | None = None,
        fasttext_models: list[str] | None = None,
    ) -> dict:
        """Train all configurations.

        Args:
            vocab_sizes: List of vocabulary sizes for SentencePiece.
            model_types: List of SentencePiece model types.
            fasttext_dims: List of embedding dimensions for FastText.
            fasttext_models: List of FastText model types.

        Returns:
            Dictionary with all training results.
        """
        if vocab_sizes is None:
            vocab_sizes = [1000, 2000, 4000, 8000, 16000, 32000]
        if model_types is None:
            model_types = ["bpe", "unigram"]
        if fasttext_dims is None:
            fasttext_dims = [100, 200, 300]
        if fasttext_models is None:
            fasttext_models = ["skipgram"]

        results = {
            "sentencepiece": [],
            "fasttext": [],
            "start_time": time.time(),
        }

        # Train SentencePiece models
        for vocab_size in vocab_sizes:
            for model_type in model_types:
                try:
                    log.info("Training SP: vocab=%d, type=%s", vocab_size, model_type)
                    model_path = self.train_sentencepiece(
                        vocab_size=vocab_size,
                        model_type=model_type,
                        output_prefix=str(self.output_dir / f"zolai_sp_{model_type}_{vocab_size}"),
                    )
                    # Evaluate
                    eval_result = self.evaluate_tokenizer(model_path)
                    eval_result.update({"vocab_size_target": vocab_size, "model_type": model_type})
                    results["sentencepiece"].append(eval_result)
                    log.info(
                        "  Compression: %.4f, OOV: %.4f",
                        eval_result["compression_ratio"],
                        eval_result["oov_rate"],
                    )
                except Exception as e:
                    log.error("Failed SP vocab=%d type=%s: %s", vocab_size, model_type, e)
                    results["sentencepiece"].append({
                        "vocab_size_target": vocab_size,
                        "model_type": model_type,
                        "error": str(e),
                    })

        # Train FastText models
        for dim in fasttext_dims:
            for model_type in fasttext_models:
                try:
                    log.info("Training FastText: dim=%d, type=%s", dim, model_type)
                    model_path = self.train_fasttext(
                        dim=dim,
                        model_type=model_type,
                        output_path=str(self.output_dir / f"zolai_fasttext_{model_type}_{dim}.bin"),
                    )
                    eval_result = self.evaluate_fasttext(model_path)
                    eval_result.update({"dim": dim, "model_type": model_type})
                    results["fasttext"].append(eval_result)
                except Exception as e:
                    log.error("Failed FastText dim=%d type=%s: %s", dim, model_type, e)
                    results["fasttext"].append({
                        "dim": dim,
                        "model_type": model_type,
                        "error": str(e),
                    })

        results["total_time"] = time.time() - results["start_time"]
        return results

    def save_results(self, results: dict, output_file: str | Path) -> None:
        """Save training results to JSON."""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert numpy types to native Python types for JSON serialization
        def convert(obj):
            import numpy as np
            if isinstance(obj, (np.integer, np.floating)):
                return obj.item()
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj

        results = convert(results)

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        log.info("Results saved to %s", output_path)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.tokenizer_training",
        description="SylBreak4All M8: Tokenizer Training with SentencePiece/FastText",
    )
    parser.add_argument(
        "--corpus",
        type=str,
        default="data/syllable/splits/gold_train.jsonl",
        help="Path to syllable corpus JSONL file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="models",
        help="Output directory for models",
    )
    parser.add_argument(
        "--train-all",
        action="store_true",
        help="Train all variants (SP + FastText)",
    )
    parser.add_argument(
        "--vocab-sizes",
        type=str,
        default="1000,2000,4000,8000,16000,32000",
        help="Comma-separated vocabulary sizes for SentencePiece",
    )
    parser.add_argument(
        "--model-types",
        type=str,
        default="bpe,unigram",
        help="Comma-separated SentencePiece model types (bpe,unigram,word,char)",
    )
    parser.add_argument(
        "--fasttext-dims",
        type=str,
        default="100,200,300",
        help="Comma-separated FastText embedding dimensions",
    )
    parser.add_argument(
        "--fasttext-models",
        type=str,
        default="skipgram",
        help="Comma-separated FastText model types (skipgram,cbow)",
    )
    parser.add_argument(
        "--train-sp",
        action="store_true",
        help="Train single SentencePiece model",
    )
    parser.add_argument(
        "--sp-vocab-size",
        type=int,
        default=8000,
        help="Vocabulary size for single SP model",
    )
    parser.add_argument(
        "--sp-model-type",
        type=str,
        default="bpe",
        help="Model type for single SP model (bpe,unigram,word,char)",
    )
    parser.add_argument(
        "--train-fasttext",
        action="store_true",
        help="Train single FastText model",
    )
    parser.add_argument(
        "--ft-dim",
        type=int,
        default=100,
        help="Embedding dimension for FastText",
    )
    parser.add_argument(
        "--ft-model-type",
        type=str,
        default="skipgram",
        help="FastText model type (skipgram,cbow)",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Evaluate trained model",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Path to model file for evaluation",
    )
    parser.add_argument(
        "--test",
        type=str,
        default="data/syllable/splits/gold_test.jsonl",
        help="Path to test file for evaluation",
    )
    parser.add_argument(
        "--save-results",
        type=str,
        help="Path to save evaluation results JSON",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    trainer = SyllableTokenizerTrainer(
        corpus_path=args.corpus,
        output_dir=args.output_dir,
    )

    # Train all variants
    if args.train_all:
        vocab_sizes = [int(x) for x in args.vocab_sizes.split(",")]
        model_types = args.model_types.split(",")
        fasttext_dims = [int(x) for x in args.fasttext_dims.split(",")]
        fasttext_models = args.fasttext_models.split(",")

        results = trainer.train_all_variants(
            vocab_sizes=vocab_sizes,
            model_types=model_types,
            fasttext_dims=fasttext_dims,
            fasttext_models=fasttext_models,
        )

        # Save results
        if args.save_results:
            trainer.save_results(results, args.save_results)
        else:
            results_path = Path(args.output_dir) / "training_results.json"
            trainer.save_results(results, results_path)

        # Print summary
        print("\n=== Training Summary ===")
        print(f"Total time: {results['total_time']:.1f}s")
        print(f"SentencePiece models: {len(results['sentencepiece'])}")
        print(f"FastText models: {len(results['fasttext'])}")

        for r in results["sentencepiece"]:
            if "error" not in r:
                print(f"  SP {r['model_type']} {r['vocab_size_target']}: "
                      f"compression={r['compression_ratio']:.4f}, "
                      f"OOV={r['oov_rate']:.4f}")
            else:
                print(f"  SP {r['model_type']} {r['vocab_size_target']}: ERROR - {r['error']}")

        for r in results["fasttext"]:
            if "error" not in r:
                print(f"  FT {r['model_type']} {r['dim']}d: vocab={r['vocab_size']}")
            else:
                print(f"  FT {r['model_type']} {r['dim']}d: ERROR - {r['error']}")

        return 0

    # Train single SentencePiece
    if args.train_sp:
        if not _SENTENCEPIECE_AVAILABLE:
            print("Error: sentencepiece not installed. Run: pip install sentencepiece", file=sys.stderr)
            return 1
        model_path = trainer.train_sentencepiece(
            vocab_size=args.sp_vocab_size,
            model_type=args.sp_model_type,
            output_prefix=str(Path(args.output_dir) / f"zolai_sp_{args.sp_model_type}_{args.sp_vocab_size}"),
        )
        print(f"Model saved to {model_path}")
        return 0

    # Train single FastText
    if args.train_fasttext:
        if not _FASTTEXT_AVAILABLE:
            print("Error: fasttext not installed. Run: pip install fasttext", file=sys.stderr)
            return 1
        model_path = trainer.train_fasttext(
            dim=args.ft_dim,
            model_type=args.ft_model_type,
            output_path=str(Path(args.output_dir) / f"zolai_fasttext_{args.ft_model_type}_{args.ft_dim}.bin"),
        )
        print(f"Model saved to {model_path}")
        return 0

    # Evaluate
    if args.evaluate:
        if not args.model:
            print("Error: --model required for evaluation", file=sys.stderr)
            return 1

        model_path = Path(args.model)
        if not model_path.exists():
            print(f"Error: Model not found: {model_path}", file=sys.stderr)
            return 1

        if model_path.suffix == ".model":
            # SentencePiece evaluation
            if not _SENTENCEPIECE_AVAILABLE:
                print("Error: sentencepiece not installed", file=sys.stderr)
                return 1
            result = trainer.evaluate_tokenizer(str(model_path), test_file=args.test)
        elif model_path.suffix == ".bin":
            # FastText evaluation
            if not _FASTTEXT_AVAILABLE:
                print("Error: fasttext not installed", file=sys.stderr)
                return 1
            result = trainer.evaluate_fasttext(str(model_path))
        else:
            print(f"Error: Unknown model type: {model_path.suffix}", file=sys.stderr)
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))

        if args.save_results:
            trainer.save_results(result, args.save_results)

        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
