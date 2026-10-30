"""Secret hygiene for tracked operational files — the shape GitGuardian saw.

Incident F3 (2026-10-29, commit ``c1119c4``): ``scripts/deploy/setup_vps.sh``
fed the 3x-ui installer its answers as a here-document, and the password answer
was a **literal line** in that block.  The fix deleted the line, which is also
what disclosed it — a removed line is still a diff, and GitGuardian scans diffs.
So "we removed it" is not a defense, and neither is a scan that only looks for
``PASSWORD=<literal>``: the leaked value was never on the right of an ``=``.

Two guards, both narrow enough to stay green and both aimed at a reintroduction:

1. **the answer block** (:class:`TestInstallerAnswerBlock`) — the installer
   sequence in ``setup_vps.sh`` must contain only expansions and the two
   non-secret answers.  This is the exact shape of the incident.
2. **assignments** (:class:`TestNoLiteralCredentials`) — no tracked shell or
   config file may assign a credential-named variable a literal value.  This is
   the ordinary way a value like this comes back (someone tests a script by
   hardcoding and forgets to revert).

Both have a non-vacuity test with a **synthetic fake** value, because a guard
that silently matches nothing is worse than no guard — it is the reason
``c1119c4`` was possible after the earlier F1/F2 incidents.

The real values are never written here.  Nothing in this file may contain a
credential, including in a test that is checking for credentials: the fakes are
a nonsense string with a ``synthetic-`` prefix and the prescribed
``Not-A-Real-Passw0rd`` shape.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "setup_vps.sh"

#: A value used only inside this file's non-vacuity checks.  It is not a
#: credential anywhere, on any machine, ever.  It is deliberately **not** shaped
#: like a documented placeholder, because the point of those checks is that the
#: scan fires on it; :data:`DOCUMENTED_PLACEHOLDER` is the opposite case.
FAKE_SECRET = "synthetic-9f3-checkvalue"

#: One of the unmistakable-fake shapes the root AGENTS.md prescribes for tracked
#: files, which the scanner must *not* report.
DOCUMENTED_PLACEHOLDER = "Not-A-Real-Passw0rd"


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout


pytestmark = pytest.mark.skipif(
    not _git(["rev-parse", "--git-dir"]).strip(),
    reason="needs a git checkout to enumerate tracked files",
)


# ---------------------------------------------------------------------------
# Guard 1 — the installer answer block
# ---------------------------------------------------------------------------

_HEREDOC_OPEN = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def _heredoc_body(text: str, opener_pattern: re.Pattern[str]) -> list[str]:
    """The body lines of the first heredoc whose opening line matches."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not opener_pattern.search(line):
            continue
        tag_match = _HEREDOC_OPEN.search(line)
        if not tag_match:  # pragma: no cover - the pattern requires a tag
            continue
        tag = tag_match.group(1)
        body: list[str] = []
        for row in lines[index + 1 :]:
            if row.strip() == tag:
                return body
            body.append(row.strip())
    raise AssertionError(f"no heredoc matching {opener_pattern.pattern!r} was found")


#: Opens the 3x-ui installer invocation — the line whose answers are
#: username/password/port.  Matched on the installer URL so a refactor that
#: renames the heredoc tag still lands on the same block.
_INSTALLER = re.compile(r"install\.sh.*<<")

#: What the installer is allowed to be handed.  ``y`` (confirm), the two
#: expansions, and the panel port.  Anything else in this position is a literal
#: being typed into a tracked file, and one of those three positions is a
#: credential.
ALLOWED_ANSWERS = {"y", "${PANEL_USER}", "${PANEL_PASSWORD}", "443"}


class TestInstallerAnswerBlock:
    def test_the_answer_block_holds_only_expansions(self) -> None:
        assert DEPLOY_SCRIPT.is_file(), f"missing {DEPLOY_SCRIPT}"
        body = _heredoc_body(DEPLOY_SCRIPT.read_text(encoding="utf-8"), _INSTALLER)
        unexpected = [line for line in body if line not in ALLOWED_ANSWERS]
        assert unexpected == [], (
            "the 3x-ui answer block must be y / ${PANEL_USER} / ${PANEL_PASSWORD} "
            f"/ 443; a literal here is a committed credential: {unexpected}"
        )

    def test_the_password_is_required_from_the_environment(self) -> None:
        """``${VAR:?message}`` — no default, so a missing value stops the run.

        ``${VAR:-default}`` would be the bug: a default is where the literal
        goes, and the whole point of c1119c4 was to move the value out of the
        file.  Asserting the form (not merely "no literal") is what keeps a
        well-meaning convenience patch from re-creating the incident.
        """
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        assert re.search(r'PANEL_PASSWORD="\$\{PANEL_PASSWORD:\?', text), (
            "PANEL_PASSWORD must be a required (${VAR:?…}) environment value, "
            "not defaulted"
        )
        assert "${PANEL_PASSWORD:-" not in text, (
            "a ${VAR:-literal} default is where the value came back; the form "
            "this guard exists to keep out"
        )

    def test_the_panel_pair_is_never_assigned_a_bare_literal(self) -> None:
        """``PANEL_USER`` is the other half of the same credential.

        Guard 2 keys on the word "password", so the *username* default — which is
        what ``PANEL_USER:-`` was before this incident was cleaned up — slipped
        through it.  Requiring both names to be assigned an expansion catches the
        pair, and only the pair, without a personal-name allowlist.
        """
        text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        assignments = [
            line.strip()
            for line in text.splitlines()
            if re.match(r"\s*PANEL_(USER|PASSWORD)=", line)
        ]
        assert assignments, "the deploy script stopped defining the panel pair"
        bare = [line for line in assignments if not line.split("=", 1)[1].startswith('"${')]
        assert bare == [], f"PANEL_* must be \"${{VAR…}}\", not a literal: {bare}"

    def test_the_guard_would_have_fired_on_the_incident_shape(self) -> None:
        """Non-vacuity, with a synthetic value that is not a credential."""
        leaked = f"""
ssh root@host bash <<EOF
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) <<ANSWERS
y
panel-admin
{FAKE_SECRET}
443
ANSWERS
EOF
"""
        body = _heredoc_body(leaked, _INSTALLER)
        unexpected = [line for line in body if line not in ALLOWED_ANSWERS]
        assert unexpected == ["panel-admin", FAKE_SECRET], (
            "the block scanner must flag a hardcoded username *and* password "
            f"position; it reported {unexpected}"
        )


# ---------------------------------------------------------------------------
# Guard 2 — literal credential assignments in tracked operational files
# ---------------------------------------------------------------------------

#: Files that could plausibly hold an operational credential.  Deliberately not
#: "every tracked file": a Python test fixture's fake password and a doc's
#: example are not this incident class, and a scan that cries wolf gets a
#: blanket allowlist within a month.
_OPERATIONAL_SUFFIXES = (".sh", ".bash", ".env", ".example", ".ini", ".conf", ".service")

_CREDENTIAL_NAME = re.compile(
    r"\b([A-Za-z0-9_]*(?:PASSWORD|PASSWD|PASSPHRASE|PASS\b|PASS_|SECRET|ACCESS_TOKEN"
    r"|AUTH_TOKEN|API_TOKEN|API_KEY|APIKEY|CLIENT_SECRET|PRIVATE_KEY)[A-Za-z0-9_]*)"
    r"\s*=\s*[\"']?([^\s\"']+)"
)

#: Values that are not credentials: expansions, empty strings, and the
#: unmistakable placeholders the root AGENTS.md prescribes for documentation.
_PLACEHOLDER = re.compile(
    r"(?:^\$|^\"?\$\{?|^'.?\$\{?|^\s*$"
    r"|^(?:x+|\*+|-+|<[^>]*>|CHANGE[-_]?ME|REDACTED|YOUR[-_\w]*|PUT[-_\w]*HERE"
    r"|EXAMPLE|PLACEHOLDER|None|null|true|false|password|secret|token|api[-_]?key"
    r"|Not-A-Real-[\w-]+)$"
    r"|(?:os\.getenv|process\.env|\bgetenv\b|import\.environ|dotenv"
    r"|generated|from env|read -s|:?\?))",
    re.IGNORECASE,
)


def _operational_files() -> list[Path]:
    tracked = _git(["ls-files"]).split()
    found = []
    for relative in tracked:
        path = REPO_ROOT / relative
        if path.suffix in _OPERATIONAL_SUFFIXES or relative.startswith(".env"):
            if path.is_file():
                found.append(path)
    return found


def _literal_credentials(text: str) -> list[tuple[int, str, str]]:
    """``(line, variable, masked value)`` for each literal credential assignment."""
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = _CREDENTIAL_NAME.search(line)
        if not match:
            continue
        value = match.group(2).strip("\"'")
        if _PLACEHOLDER.search(value) or _PLACEHOLDER.search(line):
            continue
        masked = f"{value[:3]}…{value[-2:]} (len {len(value)})"
        hits.append((number, match.group(1), masked))
    return hits


class TestNoLiteralCredentials:
    def test_no_tracked_operational_file_assigns_a_credential_literal(self) -> None:
        files = _operational_files()
        assert len(files) >= 20, f"the scan found only {len(files)} files — is the filter broken?"
        offenders: list[str] = []
        for path in files:
            for number, name, masked in _literal_credentials(
                path.read_text(encoding="utf-8", errors="ignore")
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number} {name}={masked}")
        assert offenders == [], (
            "a credential literal in a tracked operational file — read it from "
            f"the environment instead: {offenders}"
        )

    def test_the_scan_is_not_vacuous(self) -> None:
        """The three reintroduction shapes, all with synthetic values."""
        for snippet in (
            f'PANEL_PASSWORD="{FAKE_SECRET}"',
            f"export SMTP_PASS='{FAKE_SECRET}'",
            f"CLOUDFLARE_API_TOKEN={FAKE_SECRET}",
        ):
            hits = _literal_credentials(snippet)
            assert len(hits) == 1, f"not flagged: {snippet}"
            _, name, masked = hits[0]
            assert FAKE_SECRET not in masked, "a report that reproduces the value is a leak"

    def test_the_allowed_forms_are_not_flagged(self) -> None:
        """Every shape the repo uses on purpose must stay quiet, or this test file
        becomes the thing everyone skips."""
        for snippet in (
            'PANEL_PASSWORD="${PANEL_PASSWORD:?Set PANEL_PASSWORD}"',
            'PANEL_PASSWORD="${PANEL_PASSWORD}"',
            "SMTP_PASS=\"${SMTP_PASS}\"",
            'KAGGLE_KEY="REDACTED"',
            'TOKEN="your-token-here"',
            'API_KEY="${GEMINI_API_KEY}"',
            'PASSWORD=""',
            f'PANEL_PASSWORD="{DOCUMENTED_PLACEHOLDER}"',
            "# PANEL_PASSWORD=never-write-a-literal-here",
        ):
            assert _literal_credentials(snippet) == [], snippet

    def test_masking_is_applied_to_anything_the_report_prints(self) -> None:
        _, name, masked = _literal_credentials(f'ADMIN_PASSWORD="{FAKE_SECRET}"')[0]
        assert masked.startswith(FAKE_SECRET[:3]) and FAKE_SECRET not in masked
        assert name == "ADMIN_PASSWORD"
