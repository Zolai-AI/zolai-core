#!/usr/bin/env python3
"""Run full Zolai data pipeline.
Uses Foundation's ETL pipeline for data operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zolai.config import config
from zolai.pipeline.foundation import FoundationETL

from . import clean, deduplicate, export


@dataclass
class PipelineConfig:
    """Configuration for full pipeline."""

    raw_dir: Path
    clean_dir: Path
    dataset_dir: Path
    skip_collect: bool = False
    skip_clean: bool = False
    skip_deduplicate: bool = False
    skip_align: bool = False
    skip_export: bool = False
    skip_foundation: bool = False


class Pipeline:
    """Full Zolai data pipeline with Foundation integration."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.stats = {}
        self._foundation_etl: FoundationETL | None = None

    @property
    def foundation_etl(self) -> FoundationETL:
        """Lazy-initialized Foundation ETL pipeline."""
        if self._foundation_etl is None:
            self._foundation_etl = FoundationETL(db_path=str(config.paths.db))
        return self._foundation_etl

    def run(self) -> dict:
        """Run full pipeline."""
        print("=" * 50)
        print("ZOLAI DATA PIPELINE")
        print("=" * 50)

        # Stage 1: Collect
        if not self.config.skip_collect:
            print("\n[1/6] Collecting data...")
            # collect.run() - handled separately
            self.stats["collect"] = {"status": "skip or run manually"}
        else:
            print("\n[1/6] Skipping collect")

        # Stage 2: Clean
        if not self.config.skip_clean:
            print("\n[2/6] Cleaning data...")
            cleaner = clean.ZolaiCleaner()
            stats = cleaner.clean_directory(self.config.raw_dir, self.config.clean_dir)
            self.stats["clean"] = stats
            print(f"   Cleaned: {stats} entries")
        else:
            print("\n[2/6] Skipping clean")

        # Stage 3: Deduplicate
        if not self.config.skip_deduplicate:
            print("\n[3/6] Deduplicating...")
            deduplicator = deduplicate.Deduplicator()
            stats = deduplicator.deduplicate_directory(self.config.clean_dir)
            self.stats["deduplicate"] = stats
            print(f"   Unique: {stats['total_unique']} / {stats['total_original']}")
        else:
            print("\n[3/6] Skipping deduplicate")

        # Stage 4: Align (optional)
        if not self.config.skip_align:
            print("\n[4/6] Aligning translations...")
            self.stats["align"] = {"status": "manual - provide translations file"}
        else:
            print("\n[4/6] Skipping align")

        # Stage 5: Export
        if not self.config.skip_export:
            print("\n[5/6] Exporting to dataset...")
            exporter = export.Exporter()
            stats = exporter.export_directory(self.config.clean_dir, self.config.dataset_dir)
            self.stats["export"] = stats
            print(f"   Exported: {stats}")
        else:
            print("\n[5/6] Skipping export")

        # Stage 6: Foundation ETL (optional)
        if not self.config.skip_foundation:
            print("\n[6/6] Running Foundation ETL pipeline...")
            try:
                foundation_stats = self.foundation_etl.run_full_pipeline()
                self.stats["foundation"] = {
                    "ingest": foundation_stats["ingest"].records_processed,
                    "staged": foundation_stats["build_staging"].records_staged,
                    "promoted": foundation_stats["promote"].records_promoted,
                }
                print(f"   Foundation: {self.stats['foundation']}")
            except Exception as e:
                print(f"   Foundation ETL error: {e}")
                self.stats["foundation"] = {"error": str(e)}
        else:
            print("\n[6/6] Skipping Foundation ETL")

        print("\n" + "=" * 50)
        print("PIPELINE COMPLETE")
        print("=" * 50)

        return self.stats


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    import argparse

    ROOT = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description="Run Zolai data pipeline")
    parser.add_argument("--skip-collect", action="store_true", help="Skip collect stage")
    parser.add_argument("--skip-clean", action="store_true", help="Skip clean stage")
    parser.add_argument("--skip-deduplicate", action="store_true", help="Skip deduplicate stage")
    parser.add_argument("--skip-align", action="store_true", help="Skip align stage")
    parser.add_argument("--skip-export", action="store_true", help="Skip export stage")
    parser.add_argument("--skip-foundation", action="store_true", help="Skip Foundation ETL stage")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "raw", help="Raw data directory")
    parser.add_argument("--clean-dir", type=Path, default=ROOT / "clean", help="Clean data directory")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "dataset", help="Dataset output directory")
    args = parser.parse_args(argv or [])

    pipeline_config = PipelineConfig(
        raw_dir=args.raw_dir,
        clean_dir=args.clean_dir,
        dataset_dir=args.dataset_dir,
        skip_collect=args.skip_collect,
        skip_clean=args.skip_clean,
        skip_deduplicate=args.skip_deduplicate,
        skip_align=args.skip_align,
        skip_export=args.skip_export,
        skip_foundation=args.skip_foundation,
    )

    pipeline = Pipeline(pipeline_config)
    stats = pipeline.run()

    print(f"\nStats: {stats}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
