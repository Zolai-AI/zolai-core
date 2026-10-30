"""
Server Monitoring and Auto-Fix System.

Monitors server logs, detects issues, and automatically fixes common problems.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from zolai.shared.clock import now, parse, stamp

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """A parsed log entry."""
    timestamp: datetime
    level: str
    message: str
    source: str = ""
    traceback: str = ""


@dataclass
class Issue:
    """A detected issue."""
    issue_id: str
    severity: str  # critical, high, medium, low
    category: str
    message: str
    first_seen: datetime
    last_seen: datetime
    count: int = 1
    status: str = "open"  # open, investigating, fixed, ignored
    fix_applied: Optional[str] = None


class LogParser:
    """Parses server logs into structured entries."""

    # Common log patterns
    PATTERNS = {
        "python_traceback": re.compile(r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)", re.DOTALL),
        "error": re.compile(r"ERROR|Error|error|Exception|exception"),
        "warning": re.compile(r"WARNING|Warning|warning"),
        "critical": re.compile(r"CRITICAL|Critical|critical|FATAL|Fatal|fatal"),
        "timestamp": re.compile(r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})"),
    }

    @classmethod
    def parse_log_line(cls, line: str) -> Optional[LogEntry]:
        """Parse a single log line."""
        # Try to extract timestamp
        ts_match = cls.PATTERNS["timestamp"].search(line)
        timestamp = now()
        if ts_match:
            try:
                # A log line carries no offset, so this parse used to return a naive
                # datetime.  `since` below is aware, and Python raises TypeError rather
                # than guessing when it compares the two.
                timestamp = parse(ts_match.group(1))
            except ValueError:
                pass

        # Determine level
        level = "info"
        if cls.PATTERNS["critical"].search(line):
            level = "critical"
        elif cls.PATTERNS["error"].search(line):
            level = "error"
        elif cls.PATTERNS["warning"].search(line):
            level = "warning"

        return LogEntry(
            timestamp=timestamp,
            level=level,
            message=line.strip(),
        )

    @classmethod
    def parse_log_file(cls, filepath: Path, since: Optional[datetime] = None) -> list[LogEntry]:
        """Parse a log file."""
        entries = []
        try:
            with open(filepath, "r") as f:
                for line in f:
                    entry = cls.parse_log_line(line)
                    if entry:
                        if since and entry.timestamp < since:
                            continue
                        entries.append(entry)
        except Exception as e:
            logger.error(f"Failed to parse log file {filepath}: {e}")
        return entries


class IssueDetector:
    """Detects issues from log entries."""

    # Known issue patterns
    ISSUE_PATTERNS = {
        "database_locked": {
            "pattern": re.compile(r"database.*locked|locked.*database|SQLite.*locked"),
            "severity": "high",
            "category": "database",
            "auto_fix": "restart_db_connections",
        },
        "memory_error": {
            "pattern": re.compile(r"MemoryError|OutOfMemory|out of memory|memory exhausted"),
            "severity": "critical",
            "category": "memory",
            "auto_fix": "restart_service",
        },
        "connection_refused": {
            "pattern": re.compile(r"Connection refused|connect.*refused|ECONNREFUSED"),
            "severity": "high",
            "category": "network",
            "auto_fix": "check_service_health",
        },
        "timeout": {
            "pattern": re.compile(r"timeout|Timeout|timed out"),
            "severity": "medium",
            "category": "performance",
            "auto_fix": "increase_timeout",
        },
        "import_error": {
            "pattern": re.compile(r"ImportError|ModuleNotFoundError|No module named"),
            "severity": "high",
            "category": "dependency",
            "auto_fix": "install_missing_dependencies",
        },
        "permission_denied": {
            "pattern": re.compile(r"Permission denied|EACCES|EACCES"),
            "severity": "high",
            "category": "permission",
            "auto_fix": "fix_permissions",
        },
        "disk_full": {
            "pattern": re.compile(r"No space left|disk full|ENOSPC"),
            "severity": "critical",
            "category": "disk",
            "auto_fix": "cleanup_disk",
        },
        "sql_error": {
            "pattern": re.compile(r"SQLite.*Error|sqlalchemy.*Error|OperationalError|IntegrityError"),
            "severity": "high",
            "category": "database",
            "auto_fix": "check_database_integrity",
        },
        "api_key_missing": {
            "pattern": re.compile(r"API.*key.*missing|PROVIDER_KEY_MISSING|needs an API key"),
            "severity": "high",
            "category": "configuration",
            "auto_fix": "configure_api_keys",
        },
        "provider_error": {
            "pattern": re.compile(r"ProviderError|provider.*error|PROVIDER_"),
            "severity": "medium",
            "category": "provider",
            "auto_fix": "check_provider_config",
        },
    }

    @classmethod
    def detect_issues(cls, entries: list[LogEntry]) -> list[Issue]:
        """Detect issues from log entries."""
        issue_map = {}  # Deduplication

        for entry in entries:
            if entry.level not in ("error", "critical", "warning"):
                continue

            for issue_type, config in cls.ISSUE_PATTERNS.items():
                if config["pattern"].search(entry.message):
                    # Create deduplication key
                    key = f"{issue_type}:{entry.message[:100]}"

                    if key in issue_map:
                        issue = issue_map[key]
                        issue.count += 1
                        issue.last_seen = entry.timestamp
                    else:
                        issue = Issue(
                            issue_id=f"{issue_type}_{int(time.time())}_{len(issue_map)}",
                            severity=config["severity"],
                            category=config["category"],
                            message=entry.message[:500],
                            first_seen=entry.timestamp,
                            last_seen=entry.timestamp,
                            count=1,
                        )
                        issue_map[key] = issue

        return list(issue_map.values())


class AutoFixer:
    """Automatically fixes detected issues."""

    def __init__(self, engine=None):
        self.engine = engine
        self.fix_history: list[dict] = []

    async def apply_fix(self, issue: Issue) -> bool:
        """Apply an automatic fix for an issue."""
        fix_method = getattr(self, f"_fix_{issue.category}", None)
        if not fix_method:
            logger.warning(f"No auto-fix available for category: {issue.category}")
            return False

        try:
            logger.info(f"Applying auto-fix for {issue.issue_id}: {issue.category}")
            result = await fix_method(issue)

            if result:
                issue.status = "fixed"
                issue.fix_applied = fix_method.__name__
                self.fix_history.append({
                    "issue_id": issue.issue_id,
                    "fix": fix_method.__name__,
                    "timestamp": stamp(),
                    "success": True,
                })
                logger.info(f"Successfully fixed {issue.issue_id}")
            else:
                issue.status = "investigating"
                logger.warning(f"Fix failed for {issue.issue_id}")

            return result

        except Exception as e:
            logger.error(f"Auto-fix failed for {issue.issue_id}: {e}")
            return False

    async def _fix_database(self, issue: Issue) -> bool:
        """Fix database issues."""
        try:
            # Try to checkpoint WAL
            if self.engine:
                from sqlalchemy import text
                with self.engine.connect() as conn:
                    conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
                    conn.execute(text("PRAGMA busy_timeout = 30000"))
            return True
        except Exception as e:
            logger.error(f"Database fix failed: {e}")
            return False

    async def _fix_memory(self, issue: Issue) -> bool:
        """Fix memory issues by clearing caches."""
        try:
            # Clear Python garbage
            import gc
            gc.collect()

            # Clear any global caches
            import sys
            for module in list(sys.modules.values()):
                if hasattr(module, "cache") and hasattr(module.cache, "clear"):
                    module.cache.clear()
            return True
        except Exception as e:
            logger.error(f"Memory fix failed: {e}")
            return False

    async def _fix_network(self, issue: Issue) -> bool:
        """Fix network connectivity issues."""
        # Would implement service health checks
        logger.warning("Network fix not implemented")
        return False

    async def _fix_performance(self, issue: Issue) -> bool:
        """Fix timeout/performance issues."""
        logger.warning("Performance fix not implemented")
        return False

    async def _fix_dependency(self, issue: Issue) -> bool:
        """Fix missing dependencies."""
        logger.warning("Dependency fix not implemented")
        return False

    async def _fix_permission(self, issue: Issue) -> bool:
        """Fix permission issues."""
        logger.warning("Permission fix not implemented")
        return False

    async def _fix_disk(self, issue: Issue) -> bool:
        """Fix disk space issues."""
        try:
            # Clean temporary files
            temp_dirs = [Path("/tmp"), Path("/var/tmp"), Path("./tmp")]
            for d in temp_dirs:
                if d.exists():
                    for f in d.glob("*.tmp"):
                        f.unlink()
                    for f in d.glob("*.log"):
                        if f.stat().st_size > 100 * 1024 * 1024:  # > 100MB
                            f.unlink()
            return True
        except Exception as e:
            logger.error(f"Disk cleanup failed: {e}")
            return False

    async def _fix_database(self, issue: Issue) -> bool:
        """Fix database integrity issues."""
        try:
            if self.engine:
                from sqlalchemy import text
                with self.engine.connect() as conn:
                    conn.execute(text("PRAGMA integrity_check"))
                    conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
            return True
        except Exception as e:
            logger.error(f"Database integrity fix failed: {e}")
            return False

    async def _fix_configuration(self, issue: Issue) -> bool:
        """Fix configuration issues."""
        logger.warning("Configuration fix not implemented")
        return False

    async def _fix_provider(self, issue: Issue) -> bool:
        """Fix provider issues."""
        logger.warning("Provider fix not implemented")
        return False


class ServerMonitor:
    """Main server monitoring class."""

    def __init__(self, engine=None, log_paths: list[str] = None):
        self.engine = engine
        self.log_paths = log_paths or [
            "/var/log/zolai/api.log",
            "/var/log/zolai/worker.log",
            "./logs/api.log",
            "./logs/error.log",
        ]
        self.parser = LogParser()
        self.detector = IssueDetector()
        self.fixer = AutoFixer(engine)
        self.issues: dict[str, Issue] = {}
        self.running = False

        logger.info("ServerMonitor initialized")

    async def scan_logs(self, since: Optional[datetime] = None) -> list[Issue]:
        """Scan all log files for issues."""
        all_entries = []

        for log_path in self.log_paths:
            path = Path(log_path)
            if path.exists():
                entries = self.parser.parse_log_file(path, since)
                all_entries.extend(entries)

        issues = self.detector.detect_issues(all_entries)

        # Update issue tracking
        for issue in issues:
            if issue.issue_id in self.issues:
                existing = self.issues[issue.issue_id]
                existing.count += issue.count
                existing.last_seen = issue.last_seen
            else:
                self.issues[issue.issue_id] = issue

        return list(self.issues.values())

    async def run_monitoring_cycle(self) -> dict[str, Any]:
        """Run a complete monitoring cycle: scan, detect, fix."""
        # Scan logs
        issues = await self.scan_logs(since=now() - timedelta(hours=1))

        # Auto-fix issues
        fixed = 0
        for issue in issues:
            if issue.status == "open" and issue.severity in ("critical", "high"):
                if await self.fixer.apply_fix(issue):
                    fixed += 1

        return {
            "timestamp": stamp(),
            "issues_found": len(issues),
            "issues_fixed": fixed,
            "open_issues": len([i for i in self.issues.values() if i.status == "open"]),
            "issues_by_severity": {
                "critical": len([i for i in self.issues.values() if i.severity == "critical"]),
                "high": len([i for i in self.issues.values() if i.severity == "high"]),
                "medium": len([i for i in self.issues.values() if i.severity == "medium"]),
                "low": len([i for i in self.issues.values() if i.severity == "low"]),
            },
        }

    async def start_monitoring(self, interval: int = 300):
        """Start continuous monitoring."""
        logger.info(f"Starting server monitoring with {interval}s interval")

        while True:
            try:
                result = await self.run_monitoring_cycle()
                logger.info(f"Monitoring cycle complete: {result}")
            except Exception as e:
                logger.error(f"Monitoring cycle error: {e}")
            await asyncio.sleep(interval)

    def get_issue_summary(self) -> dict[str, Any]:
        """Get summary of all tracked issues."""
        return {
            "total_issues": len(self.issues),
            "by_status": {
                "open": len([i for i in self.issues.values() if i.status == "open"]),
                "fixed": len([i for i in self.issues.values() if i.status == "fixed"]),
                "investigating": len([i for i in self.issues.values() if i.status == "investigating"]),
            },
            "by_severity": {
                "critical": len([i for i in self.issues.values() if i.severity == "critical"]),
                "high": len([i for i in self.issues.values() if i.severity == "high"]),
                "medium": len([i for i in self.issues.values() if i.severity == "medium"]),
                "low": len([i for i in self.issues.values() if i.severity == "low"]),
            },
            "by_category": {
                cat: len([i for i in self.issues.values() if i.category == cat])
                for cat in set(i.category for i in self.issues.values())
            },
        }


# Convenience function
async def run_health_check(engine=None) -> dict[str, Any]:
    """Run a one-time health check."""
    monitor = ServerMonitor(engine)
    return await monitor.run_monitoring_cycle()


# Import time at module level
