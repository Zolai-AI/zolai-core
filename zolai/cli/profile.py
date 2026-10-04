"""Profiling CLI for Phase 8 Production.

CPU/memory profiling of engines and API endpoints.
"""

from __future__ import annotations

import cProfile
import json
import logging
import pstats
import time
from io import StringIO
from typing import Any

import typer
from sqlalchemy.engine import Engine

from zolai.engines import ENGINES, get_engine

log = logging.getLogger(__name__)
app = typer.Typer(name="profile", help="Profiling commands (Phase 8)")


def _get_db_engine() -> Engine:
    return get_engine()


def _run_engine(spec, engine: Engine, dry_run: bool = False) -> Any:
    """Run an engine by importing its target."""
    import importlib
    module_path, attr_name = spec.target.rsplit(":", 1)
    module = importlib.import_module(module_path)
    func = getattr(module, attr_name)

    if hasattr(func, 'run'):
        return func.run(engine, dry_run=dry_run)
    elif hasattr(func, 'build'):
        return func.build(engine, dry_run=dry_run)
    else:
        return func(engine, dry_run=dry_run)


@app.command("engine")
def profile_engine(
    engine_name: str = typer.Argument(..., help="Engine name to profile"),
    runs: int = typer.Option(10, "--runs", "-n", help="Number of runs"),
    output: str = typer.Option(None, "--output", "-o", help="Output file (JSON)"),
    sort_by: str = typer.Option("cumulative", "--sort", help="Sort by: cumulative, time, calls"),
) -> None:
    """Profile an engine with cProfile."""
    spec = next((e for e in ENGINES if e.name == engine_name), None)
    if not spec:
        raise typer.BadParameter(f"Unknown engine: {engine_name}")

    log.info("Profiling engine: %s (%d runs)", engine_name, runs)

    profiler = cProfile.Profile()
    profiler.enable()

    engine = _get_db_engine()
    latencies = []

    for i in range(runs):
        start = time.perf_counter()
        try:
            _result = _run_engine(spec, engine, dry_run=True)
            latency = time.perf_counter() - start
            latencies.append(latency)
        except Exception as e:
            log.error("Run %d failed: %s", i, e)
            latencies.append(None)

    profiler.disable()

    # Stats
    stats = pstats.Stats(profiler).sort_stats(sort_by)
    stream = StringIO()
    stats.print_stats(30)
    profile_text = stream.getvalue()

    # Summary
    valid_latencies = [lat for lat in latencies if lat is not None]
    summary = {
        "engine": engine_name,
        "runs": runs,
        "successful": len(valid_latencies),
        "failed": runs - len(valid_latencies),
        "latency_ms": {
            "min": min(valid_latencies) * 1000 if valid_latencies else 0,
            "max": max(valid_latencies) * 1000 if valid_latencies else 0,
            "avg": sum(valid_latencies) / len(valid_latencies) * 1000 if valid_latencies else 0,
            "p50": sorted(valid_latencies)[len(valid_latencies)//2] * 1000 if valid_latencies else 0,
            "p95": sorted(valid_latencies)[int(len(valid_latencies)*0.95)] * 1000 if valid_latencies else 0,
            "p99": sorted(valid_latencies)[int(len(valid_latencies)*0.99)] * 1000 if valid_latencies else 0,
        },
        "profile_top30": profile_text,
    }

    if output:
        with open(output, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info("Profile saved to %s", output)
    else:
        print(json.dumps(summary, indent=2, default=str))


@app.command("api")
def profile_api(
    endpoint: str = typer.Argument(..., help="API endpoint to profile (e.g., /api/v1/word/pasian)"),
    runs: int = typer.Option(100, "--runs", "-n", help="Number of requests"),
    concurrency: int = typer.Option(1, "--concurrency", "-c", help="Concurrent requests"),
    output: str = typer.Option(None, "--output", "-o", help="Output file (JSON)"),
) -> None:
    """Profile an API endpoint with load."""
    import asyncio

    import httpx

    base_url = "http://localhost:8000"

    async def run_profile():
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            latencies = []
            errors = 0

            semaphore = asyncio.Semaphore(concurrency)

            async def make_request():
                nonlocal errors
                async with semaphore:
                    start = time.perf_counter()
                    try:
                        resp = await client.get(endpoint)
                        latency = time.perf_counter() - start
                        if resp.status_code < 400:
                            latencies.append(latency)
                        else:
                            errors += 1
                    except Exception:
                        errors += 1

            tasks = [make_request() for _ in range(runs)]
            await asyncio.gather(*tasks)

            return latencies, errors

    latencies, errors = asyncio.run(run_profile())

    if not latencies:
        log.error("All requests failed")
        return

    latencies.sort()
    summary = {
        "endpoint": endpoint,
        "runs": len(latencies),
        "errors": errors,
        "latency_ms": {
            "min": latencies[0] * 1000,
            "max": latencies[-1] * 1000,
            "avg": sum(latencies) / len(latencies) * 1000,
            "p50": latencies[len(latencies)//2] * 1000,
            "p95": latencies[int(len(latencies)*0.95)] * 1000,
            "p99": latencies[int(len(latencies)*0.99)] * 1000,
        },
    }

    if output:
        with open(output, "w") as f:
            json.dump(summary, f, indent=2)
        log.info("Profile saved to %s", output)
    else:
        print(json.dumps(summary, indent=2))


@app.command("memory")
def profile_memory(
    engine_name: str = typer.Argument(..., help="Engine name to profile"),
    duration: int = typer.Option(60, "--duration", "-d", help="Duration in seconds"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Sample interval (seconds)"),
    output: str = typer.Option(None, "--output", "-o", help="Output file (JSON)"),
) -> None:
    """Profile memory usage of an engine."""
    import tracemalloc

    spec = next((e for e in ENGINES if e.name == engine_name), None)
    if not spec:
        raise typer.BadParameter(f"Unknown engine: {engine_name}")

    engine = _get_db_engine()
    tracemalloc.start()

    snapshots = []
    start_time = time.time()

    try:
        while time.time() - start_time < duration:
            _snapshot = tracemalloc.take_snapshot()
            current, peak = tracemalloc.get_traced_memory()
            snapshots.append({
                "timestamp": time.time(),
                "current_mb": current / 1024 / 1024,
                "peak_mb": peak / 1024 / 1024,
            })
            time.sleep(interval)

            # Run engine once per interval
            import importlib
            module_path, attr_name = spec.target.rsplit(":", 1)
            module = importlib.import_module(module_path)
            func = getattr(module, attr_name)
            if hasattr(func, 'run'):
                func.run(engine, dry_run=True)
            elif hasattr(func, 'build'):
                func.build(engine, dry_run=True)
            else:
                func(engine, dry_run=True)

    except KeyboardInterrupt:
        pass
    finally:
        tracemalloc.stop()

    summary = {
        "engine": engine_name,
        "duration_seconds": duration,
        "samples": len(snapshots),
        "memory_mb": {
            "min": min(s["current_mb"] for s in snapshots),
            "max": max(s["current_mb"] for s in snapshots),
            "avg": sum(s["current_mb"] for s in snapshots) / len(snapshots),
        },
        "snapshots": snapshots,
    }

    if output:
        with open(output, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info("Memory profile saved to %s", output)
    else:
        print(json.dumps(summary, indent=2, default=str))


@app.command("compare")
def compare_engines(
    engines: list[str] = typer.Argument(..., help="Engine names to compare"),
    runs: int = typer.Option(20, "--runs", "-n", help="Runs per engine"),
    output: str = typer.Option(None, "--output", "-o", help="Output file (JSON)"),
) -> None:
    """Compare multiple engines."""
    results = {}

    for engine_name in engines:
        spec = next((e for e in ENGINES if e.name == engine_name), None)
        if not spec:
            log.warning("Unknown engine: %s", engine_name)
            continue

        engine = _get_db_engine()
        latencies = []

        for _ in range(runs):
            start = time.perf_counter()
            try:
                import importlib
                module_path, attr_name = spec.target.rsplit(":", 1)
                module = importlib.import_module(module_path)
                func = getattr(module, attr_name)
                if hasattr(func, 'run'):
                    func.run(engine, dry_run=True)
                elif hasattr(func, 'build'):
                    func.build(engine, dry_run=True)
                else:
                    func(engine, dry_run=True)
                latencies.append(time.perf_counter() - start)
            except Exception:
                latencies.append(None)

        valid = [lat for lat in latencies if lat is not None]
        if valid:
            results[engine_name] = {
                "runs": len(valid),
                "avg_ms": sum(valid) / len(valid) * 1000,
                "p50_ms": sorted(valid)[len(valid)//2] * 1000,
                "p95_ms": sorted(valid)[int(len(valid)*0.95)] * 1000,
            }

    summary = {"comparison": results}

    if output:
        with open(output, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info("Comparison saved to %s", output)
    else:
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    app()
