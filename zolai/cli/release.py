"""Release CLI for Phase 8 Production.

Release automation: prepare, promote, rollback.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any

import typer
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.publishing.release import release_knowledge, ReleaseResult

log = logging.getLogger(__name__)
app = typer.Typer(name="release", help="Release automation (Phase 8)")


def _get_db_engine() -> Engine:
    return get_engine()


@app.command("prepare")
def prepare(
    version: str = typer.Argument(..., help="Version tag (e.g., 2026.10.0)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help="Output directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validation only"),
    force: bool = typer.Option(False, "--force", help="Skip validation errors"),
) -> None:
    """Prepare a release candidate: validate, build artifact, create RC tag."""
    engine = _get_db_engine()
    rc_tag = f"v{version}-rc"

    result = release_knowledge(
        engine,
        version=version,
        output_dir=output_dir,
        dry_run=dry_run,
        force=force,
    )

    if result.success:
        # Create RC git tag
        try:
            subprocess.run(
                ["git", "tag", "-a", rc_tag, "-m", f"Release candidate {version}"],
                check=True,
                cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
            )
            log.info("Created RC tag: %s", rc_tag)
            result.git_tag = rc_tag
        except subprocess.CalledProcessError as e:
            log.error("Failed to create RC tag: %s", e)
            result.errors.append(f"Git tag creation failed: {e}")
            result.success = False

    print(json.dumps({
        "success": result.success,
        "version": result.version,
        "rc_tag": rc_tag,
        "elapsed_seconds": result.elapsed_seconds,
        "artifact_manifest": {
            "version": result.artifact_manifest.version if result.artifact_manifest else None,
            "record_counts": result.artifact_manifest.record_counts if result.artifact_manifest else None,
        } if result.artifact_manifest else None,
        "errors": result.errors,
        "warnings": result.warnings,
    }, indent=2, default=str))

    if not result.success:
        sys.exit(1)


@app.command("promote")
def promote(
    rc_tag: str = typer.Argument(..., help="RC tag to promote (e.g., v2026.10.0-rc)"),
    bucket: str = typer.Option(None, "--bucket", help="R2 bucket"),
    prefix: str = typer.Option(None, "--prefix", help="R2 prefix"),
    database_id: str = typer.Option(None, "--database-id", help="D1 database ID"),
    force: bool = typer.Option(False, "--force", help="Force promote even with warnings"),
) -> None:
    """Promote RC to stable: create stable tag, sync to R2/D1, update knowledge version."""
    from zolai.publishing.release import release_knowledge, _create_git_tag

    if not rc_tag.endswith("-rc"):
        raise typer.BadParameter("Tag must end with -rc")

    version = rc_tag[1:-3]  # Remove 'v' prefix and '-rc' suffix
    stable_tag = f"v{version}"

    engine = _get_db_engine()

    # Run full release with sync
    result = release_knowledge(
        engine,
        version=version,
        bucket=bucket,
        prefix=prefix,
        database_id=database_id,
        dry_run=False,
        force=force,
    )

    if result.success and result.git_tag:
        # Delete RC tag
        try:
            subprocess.run(
                ["git", "tag", "-d", rc_tag],
                check=True,
                cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
            )
            log.info("Deleted RC tag: %s", rc_tag)
        except subprocess.CalledProcessError:
            log.warning("Failed to delete RC tag: %s", rc_tag)

    print(json.dumps({
        "success": result.success,
        "version": result.version,
        "stable_tag": stable_tag,
        "elapsed_seconds": result.elapsed_seconds,
        "knowledge_version_id": result.knowledge_version_id,
        "git_tag": result.git_tag,
        "errors": result.errors,
        "warnings": result.warnings,
    }, indent=2, default=str))

    if not result.success:
        sys.exit(1)


@app.command("rollback")
def rollback(
    version: str = typer.Argument(..., help="Version to rollback (e.g., 2026.10.0)"),
    bucket: str = typer.Option(None, "--bucket", help="R2 bucket"),
    prefix: str = typer.Option(None, "--prefix", help="R2 prefix"),
    database_id: str = typer.Option(None, "--database-id", help="D1 database ID"),
    force: bool = typer.Option(True, "--force", help="Force rollback"),
) -> None:
    """Rollback a release: delete knowledge version, R2/D1 prefix, git tag."""
    import typer as typer_mod

    if not force:
        confirm = typer_mod.confirm(f"Rollback version {version}? This will delete data!")
        if not confirm:
            raise typer_mod.Abort()

    engine = _get_db_engine()
    tag = f"v{version}"
    r2_prefix = f"releases/{version}/"

    errors = []

    # 1. Delete knowledge version row
    try:
        with engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("DELETE FROM knowledge_versions WHERE version = :v"), {"v": version})
        log.info("Deleted knowledge version row: %s", version)
    except Exception as e:
        errors.append(f"Failed to delete knowledge version: {e}")

    # 2. Delete R2 prefix
    if bucket:
        try:
            subprocess.run(
                ["wrangler", "r2", "object", "delete", f"{bucket}/{r2_prefix}", "--recursive", "--account-id", "auto"],
                check=True,
                capture_output=True,
            )
            log.info("Deleted R2 prefix: %s", r2_prefix)
        except subprocess.CalledProcessError as e:
            errors.append(f"R2 deletion failed: {e}")

    # 3. Delete D1 data (would need custom SQL)
    # For now, just log
    if database_id:
        log.warning("D1 rollback not fully implemented for database_id: %s", database_id)

    # 4. Delete git tags
    try:
        subprocess.run(["git", "tag", "-d", f"v{version}"], check=True, cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core")
        subprocess.run(["git", "tag", "-d", f"v{version}-rc"], check=False, cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core")
        log.info("Deleted git tags for %s", version)
    except subprocess.CalledProcessError as e:
        errors.append(f"Git tag deletion failed: {e}")

    result = {
        "success": len(errors) == 0,
        "version": version,
        "errors": errors,
    }

    print(json.dumps(result, indent=2))
    if errors:
        sys.exit(1)


@app.command("status")
def status() -> None:
    """Show release status: recent versions, tags, knowledge versions."""
    import subprocess

    engine = _get_db_engine()

    # Knowledge versions
    with engine.connect() as conn:
        from sqlalchemy import text
        rows = conn.execute(
            text("SELECT version, git_commit, status, created_at FROM knowledge_versions ORDER BY id DESC LIMIT 10")
        ).fetchall()

    knowledge_versions = [
        {"version": r[0], "git_commit": r[1], "status": r[2], "created_at": r[3]}
        for r in rows
    ]

    # Git tags
    try:
        tags_output = subprocess.run(
            ["git", "tag", "-l", "v*", "--sort=-creatordate"],
            capture_output=True, text=True, check=True,
            cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
        ).stdout
        git_tags = tags_output.strip().split("\n") if tags_output.strip() else []
    except subprocess.CalledProcessError:
        git_tags = []

    print(json.dumps({
        "knowledge_versions": knowledge_versions,
        "git_tags": git_tags[:20],
    }, indent=2, default=str))


if __name__ == "__main__":
    app()
