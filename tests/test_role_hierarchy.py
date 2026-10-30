"""RBAC — the four-role hierarchy, the one resolver, and tier/scope parity.

Everything in this file answers one question: **who is this caller, and does the
answer come from one place?**  Four roles
(``anonymous < member < admin < superadmin``), one resolver
(:func:`zolai.api.rbac.role_for`), one writer for account changes
(:func:`zolai.api.session_auth.set_user_role`), one tool-tier map
:data:`zolai.agent.tools.registry.ROLE_TO_TIER`.  Each of those "one"s is a
separate guard below, because every one of them has been a second source of
truth at some point in this repo's history.

What is *not* here: the admin route behaviour (``test_admin_users_api.py``), the
guard rejections and their bodies (``test_superadmin_guards.py``), the
request/approval queue (``test_role_requests_api.py``), attribution
(``test_contributions_attribution.py``).  Those exercise the surface; this file
pins the model underneath it, so that a widening in one module fails *here* even
when the surface test still passes.

The subtle one is ``TestTierScopeParity``.  A tool's ``scope`` and a route's
``require_scope(action)`` narrow against a **role's scope set**, so an action no
role holds is reachable only by a hand-minted key — which makes the signed-in
person weaker than the bot on the same tier.  That was true of ``pos:read``
until this task (``POST /api/v1/linguistics/pos`` and the member-tier ``pos_tag``
tool were unreachable from any account), and it is exactly the kind of hole a
tier count of "42 admin tools" hides.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from zolai.agent.tools import registry
from zolai.agent.tools.registry import ROLE_TO_TIER, allow_list, tool_tier_for_record
from zolai.api import auth, rbac, session_auth
from zolai.api.rbac import ROLE_ADMIN, ROLE_ANONYMOUS, ROLE_MEMBER, ROLE_SUPERADMIN, ROLES

ZOLAI_SRC = Path(rbac.__file__).resolve().parent.parent  # <repo>/zolai


# ---------------------------------------------------------------------------
# role_for — the truth table
# ---------------------------------------------------------------------------


class TestRoleForTruthTable:
    """Every row a credential can arrive as, and what the resolver answers.

    Two sources of truth, in order: an account's ``users.role`` (carried on the
    record) beats the scope reading; a bare key has no row, so its widest scope
    is its role.  The table below is that paragraph made executable.
    """

    @pytest.mark.parametrize(
        "record,expected",
        [
            # ── no credential ────────────────────────────────────────────────
            (None, ROLE_ANONYMOUS),
            ({}, ROLE_ANONYMOUS),
            # ── member: a key/account with no admin power ────────────────────
            ({"scopes": ["dataset:read"]}, ROLE_MEMBER),
            ({"scopes": ["rag:read", "catalog:read"]}, ROLE_MEMBER),
            ({"scopes": ["pos:read"]}, ROLE_MEMBER),  # a read is not a rank
            ({"scopes": []}, ROLE_MEMBER),
            ({"scopes": None}, ROLE_MEMBER),
            # ── admin: any one of the three admin-defining actions ───────────
            ({"scopes": ["apikey:manage"]}, ROLE_ADMIN),
            ({"scopes": ["settings:write"]}, ROLE_ADMIN),
            ({"scopes": ["user:manage"]}, ROLE_ADMIN),
            ({"scopes": ["dataset:read", "user:manage"]}, ROLE_ADMIN),
            # ── superadmin: the role-granting action, or the wildcard ────────
            ({"scopes": ["role:manage"]}, ROLE_SUPERADMIN),
            ({"scopes": ["apikey:manage", "role:manage"]}, ROLE_SUPERADMIN),
            ({"scopes": ["*"]}, ROLE_SUPERADMIN),
            # ── an account row answers from the row ──────────────────────────
            ({"role": "superadmin", "scopes": session_auth.SUPERADMIN_SCOPES}, ROLE_SUPERADMIN),
            ({"role": "admin", "scopes": session_auth.ADMIN_SCOPES}, ROLE_ADMIN),
            ({"role": "member", "scopes": session_auth.MEMBER_SCOPES}, ROLE_MEMBER),
            ({"role": "anonymous"}, ROLE_ANONYMOUS),
            # Case and whitespace are noise in a hand-edited row, not power.
            ({"role": " SuperAdmin "}, ROLE_SUPERADMIN),
        ],
    )
    def test_role_for(self, record: dict | None, expected: str) -> None:
        assert rbac.role_for(record) == expected

    def test_the_account_row_wins_over_the_scopes_it_holds(self) -> None:
        """Demotion takes effect on the next request, not on the mint.

        A record whose row says ``member`` but which still carries the scopes it
        was granted as an admin is a **member**.  Resolving the other way round
        would mean a demoted account keeps its power until something re-mints its
        credential — and for a session, nothing ever re-mints.
        """
        demoted = {"role": "member", "scopes": list(session_auth.SUPERADMIN_SCOPES)}
        assert rbac.role_for(demoted) == ROLE_MEMBER

    def test_an_unreadable_role_falls_through_to_the_scopes(self) -> None:
        """Never raises, never trusts: a hand-edited row degrades, it does not 500."""
        assert rbac.role_for({"role": "root", "scopes": ["dataset:read"]}) == ROLE_MEMBER
        assert rbac.role_for({"role": "root", "scopes": ["role:manage"]}) == ROLE_SUPERADMIN
        assert rbac.role_for({"role": "superuser", "scopes": ["settings:write"]}) == ROLE_ADMIN
        assert rbac.role_for({"role": None, "scopes": ["dataset:read"]}) == ROLE_MEMBER

    def test_no_scope_that_defines_admin_is_a_member_scope(self) -> None:
        """The admin-defining set is exactly three actions, and members hold none."""
        held = set(session_auth.MEMBER_SCOPES)
        assert held & rbac.ADMIN_SCOPES == set()
        assert held & rbac.SUPERADMIN_SCOPES == set()
        # ...while an admin account *does* hold them, so role_for agrees with the row.
        assert set(session_auth.ADMIN_SCOPES) & rbac.ADMIN_SCOPES == rbac.ADMIN_SCOPES


# ---------------------------------------------------------------------------
# Rank + has_role
# ---------------------------------------------------------------------------


class TestRankAndHasRole:
    def test_hierarchy_order(self) -> None:
        assert ROLES == (ROLE_ANONYMOUS, ROLE_MEMBER, ROLE_ADMIN, ROLE_SUPERADMIN)
        assert rbac.rank_of(None) == 0
        for idx, name in enumerate(ROLES):
            assert rbac.rank_of({"role": name}) == idx

    def test_has_role_is_at_least_not_equals(self) -> None:
        """The whole point of four roles: a superadmin satisfies every minimum.

        Every ``== "admin"`` equality this repo replaced with ``has_role`` is a
        place where superadmin used to be refused its own power.
        """
        for minimum in ROLES:
            assert rbac.has_role({"role": ROLE_SUPERADMIN}, minimum) is True
        assert rbac.has_role({"role": ROLE_ADMIN}, ROLE_MEMBER) is True
        assert rbac.has_role({"role": ROLE_ADMIN}, ROLE_SUPERADMIN) is False
        assert rbac.has_role({"role": ROLE_MEMBER}, ROLE_ADMIN) is False
        assert rbac.has_role({"role": ROLE_MEMBER}, ROLE_MEMBER) is True
        assert rbac.has_role(None, ROLE_ANONYMOUS) is True
        assert rbac.has_role(None, ROLE_MEMBER) is False

    def test_has_role_accepts_a_key_derived_role(self) -> None:
        assert rbac.has_role({"scopes": ["role:manage"]}, ROLE_ADMIN) is True
        assert rbac.has_role({"scopes": ["user:manage"]}, ROLE_SUPERADMIN) is False

    def test_require_role_rejects_an_unknown_minimum(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        with pytest.raises(ValueError, match="unknown role"):
            rbac.require_role("owner")

    def test_valid_roles_is_the_assignable_subset(self) -> None:
        """An account may hold three of the four; ``anonymous`` is not a person."""
        assert session_auth.VALID_ROLES == {ROLE_MEMBER, ROLE_ADMIN, ROLE_SUPERADMIN}
        assert ROLE_ANONYMOUS not in session_auth.VALID_ROLES
        assert set(session_auth.VALID_ROLES) <= set(ROLES)


# ---------------------------------------------------------------------------
# Role → scope grants (superset rule + vocabulary legality)
# ---------------------------------------------------------------------------


class TestScopeGrants:
    def test_each_role_is_a_superset_of_the_one_below(self) -> None:
        member = set(session_auth.role_scopes("member"))
        admin = set(session_auth.role_scopes("admin"))
        superadmin = set(session_auth.role_scopes("superadmin"))
        assert member < admin < superadmin
        assert superadmin - admin == {"role:manage"}
        assert "role:manage" not in admin and "role:manage" not in member

    def test_no_account_role_holds_the_wildcard(self) -> None:
        """Accounts are widened by rank, keys by ``*`` — never both at once.

        A ``*`` in a role set would make ``role_for``'s scope branch and the
        account row indistinguishable, and would silently mint a superadmin out
        of an admin account.
        """
        for role in session_auth.VALID_ROLES:
            assert "*" not in session_auth.role_scopes(role), role

    def test_every_granted_action_is_in_the_frozen_vocabulary(self) -> None:
        legal = set(auth.VALID_ACTIONS)
        for role in session_auth.VALID_ROLES:
            unknown = set(session_auth.role_scopes(role)) - legal
            assert unknown == set(), f"{role} holds actions outside the 33: {unknown}"

    def test_unknown_role_degrades_to_member_instead_of_raising(self) -> None:
        assert session_auth.role_scopes("wizard") == list(session_auth.MEMBER_SCOPES)
        assert session_auth.role_scopes(None) == list(session_auth.MEMBER_SCOPES)
        assert session_auth.role_scopes("") == list(session_auth.MEMBER_SCOPES)


# ---------------------------------------------------------------------------
# Tier ↔ scope parity — the hole a tool count hides
# ---------------------------------------------------------------------------


class TestTierScopeParity:
    """A role holds the action behind every scoped tool in its tier.

    ``allow_list(tier, granted=…)`` drops a tool whose ``scope`` action the
    caller lacks, and :func:`registry.allow_list_for_record` feeds it the
    account's own scope set.  So if a member-tier tool declares ``pos:read`` and
    no role set carries ``pos:read``, **every signed-in caller loses that tool**
    while an anonymous caller — ``granted=None``, no narrowing — keeps whatever
    its tier has.  Nothing but this parity rule makes the advertised tier count
    true for a person.
    """

    TIERS: tuple[tuple[str, str], ...] = (
        ("public", ROLE_ANONYMOUS),
        ("member", ROLE_MEMBER),
        ("admin", ROLE_ADMIN),
    )

    def test_the_scoped_tools_exist_where_we_think_they_do(self) -> None:
        for tier, _role in self.TIERS:
            scoped = {name for name in allow_list(tier) if registry.SPECS[name].scope}
            if tier == "public":
                # Anonymous narrows against nothing, so a scoped public tool would
                # be unreachable for the callers that have no scopes at all.
                assert scoped == set(), f"anonymous-tier tools must carry no scope: {scoped}"
            else:
                assert scoped, f"{tier} tier expected scoped tools"

    def test_every_tier_stays_whole_for_its_lowest_role(self) -> None:
        """The rule, applied at each tier's *least* privileged holder."""
        for tier, role in self.TIERS:
            base = allow_list(tier)
            granted = set(session_auth.role_scopes(role))
            kept = allow_list(tier, granted=granted)
            lost = sorted(base - kept)
            assert kept == base, (
                f"a signed-in {role!r} loses {len(lost)} {tier}-tier tool(s) the "
                f"tier advertises: {lost}. Add the missing action(s) to "
                "session_auth.MEMBER_SCOPES / ADMIN_EXTRA_SCOPES — every one of "
                "them is a read the tier already says this role may do."
            )

    def test_admin_and_superadmin_records_see_the_same_tools(self) -> None:
        admin = allow_list("admin", granted=set(session_auth.role_scopes("admin")))
        superadmin = allow_list("admin", granted=set(session_auth.role_scopes("superadmin")))
        assert superadmin == admin, "superadmin is a superset, so it can never see fewer"

    def test_the_rest_side_of_the_same_rule(self) -> None:
        """The three actions this task added, pinned individually.

        ``pos:read`` gates ``GET|POST /api/v1/linguistics/pos`` (member class)
        and the member-tier ``pos_tag`` tool; ``pipeline:read`` and ``eval:read``
        are the admin-tier learning-run reads.  A member-class route that
        required an action only admin held would have been the mirror image of
        this bug, and no test here would have caught it — hence the explicit
        trio.
        """
        assert "pos:read" in session_auth.MEMBER_SCOPES
        assert "pipeline:read" in session_auth.ADMIN_EXTRA_SCOPES
        assert "eval:read" in session_auth.ADMIN_EXTRA_SCOPES
        assert auth.scope_allows(list(session_auth.MEMBER_SCOPES), "pos:read") is True
        assert auth.scope_allows(list(session_auth.MEMBER_SCOPES), "eval:read") is False
        # Widening a member's read set must not have widened its *rank*.
        assert rbac.role_for({"scopes": list(session_auth.MEMBER_SCOPES)}) == ROLE_MEMBER

    def test_the_linguistics_read_is_member_reachable(self, monkeypatch) -> None:
        """The concrete consequence: a member key can POS-tag, and it is not admin."""
        for method in ("GET", "POST"):
            assert rbac.classify_route(method, "/api/v1/linguistics/pos") == "member", method
        dep = auth.require_scope("pos:read")
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        record = {"scopes": list(session_auth.MEMBER_SCOPES), "key_prefix": "z"}
        request = _request("POST", "/api/v1/linguistics/pos", {"api_key": record})
        assert dep(request) == record


def _request(method: str = "GET", path: str = "/api/v1/records", state: dict | None = None):
    from fastapi import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "state": dict(state or {}),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Role → MCP tool tier (four roles, three tiers)
# ---------------------------------------------------------------------------


class TestRoleToToolTier:
    def test_the_map_covers_exactly_the_roles(self) -> None:
        assert set(ROLES) == set(ROLE_TO_TIER)
        assert set(ROLE_TO_TIER.values()) <= {"public", "member", "admin"}

    def test_superadmin_earns_the_admin_tier_and_allow_list_accepts_it(self) -> None:
        """The one line that catches a ``"superadmin"`` reaching :func:`allow_list`.

        ``allow_list`` raises on an unknown scope, so a map that grew a fourth
        tier value without growing a set would have 500'd every superadmin's
        ``tools/list``.  Superadmin's extra power is *granting roles* — a route
        guard, not a larger tool inventory.
        """
        record = {"role": ROLE_SUPERADMIN, "scopes": list(session_auth.SUPERADMIN_SCOPES)}
        assert tool_tier_for_record(record) == "admin"
        assert allow_list(tool_tier_for_record(record)) == allow_list("admin")

    def test_each_role_lands_on_its_tier(self) -> None:
        assert tool_tier_for_record(None) == "public"
        assert tool_tier_for_record({"role": ROLE_ANONYMOUS}) == "public"
        assert tool_tier_for_record({"role": ROLE_MEMBER}) == "member"
        assert tool_tier_for_record({"role": ROLE_ADMIN}) == "admin"
        assert tool_tier_for_record({"role": ROLE_SUPERADMIN}) == "admin"

    def test_an_unknown_role_degrades_to_member_not_public(self) -> None:
        """A caller the server identified is never demoted to the anonymous set."""
        assert tool_tier_for_record({"role": "wizard"}) == "member"
        assert tool_tier_for_record({"role": "wizard", "scopes": ["role:manage"]}) == "admin"

    def test_tiers_are_nested(self) -> None:
        assert allow_list("public") < allow_list("member") < allow_list("admin")
        assert {len(allow_list(t)) for t in ("public", "member", "admin")} == {8, 31, 42}


# ---------------------------------------------------------------------------
# Person ≠ credential
# ---------------------------------------------------------------------------


class TestPersonIdentity:
    """Only a username session is a *person*; a key and a grant are machines."""

    def test_a_session_is_a_person(self) -> None:
        record = {
            "auth_source": "session",
            "user_id": 7,
            "username": "founder",
            "role": "superadmin",
        }
        assert rbac.person_identity(record) == (7, "founder", "superadmin")

    @pytest.mark.parametrize(
        "record",
        [
            None,
            {},
            {"scopes": ["dataset:read"], "key_prefix": "zolai_ak_1"},  # API key
            {"user_id": 7, "username": "founder", "auth_source": "oauth"},  # a grant
            {"user_id": 7, "username": "founder"},  # a key, no marker at all
            {"auth_source": "session", "user_id": None, "username": "ghost"},
        ],
    )
    def test_a_credential_is_not_a_person(self, record: dict | None) -> None:
        assert rbac.person_identity(record) is None


# ---------------------------------------------------------------------------
# The single writer, verified against the source
# ---------------------------------------------------------------------------


class TestSingleWriterSourceShape:
    """No module outside :mod:`session_auth` may write ``users.role``/``enabled``.

    The guards (no self-role-change, no removing the last superadmin, nothing
    below superadmin touches an admin) live in ``set_user_role`` /
    ``set_user_enabled``.  A second ``UPDATE users SET role`` anywhere in the
    package is a second writer, and a second writer is an unguarded promotion —
    which is why this reads the source instead of calling the API.
    """

    _RAW_SQL = re.compile(r"UPDATE\s+users\s+SET([^\n]*)", re.IGNORECASE)
    _CORE_UPDATE = re.compile(r"\.update\(\s*\{[^}]*(?:\"|')(?:role|enabled)(?:\"|')", re.IGNORECASE)

    def _offenders(self) -> list[str]:
        found: list[str] = []
        for path in sorted(ZOLAI_SRC.rglob("*.py")):
            if path.name == "session_auth.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.lstrip().startswith("#"):
                    continue
                raw = self._RAW_SQL.search(line)
                writes_rank = bool(raw) and re.search(r"\b(role|enabled)\s*=", raw.group(1), re.I)
                if writes_rank or self._CORE_UPDATE.search(line):
                    found.append(f"{path.relative_to(ZOLAI_SRC)}:{number} {line.strip()}")
        return found

    def test_only_the_service_writes_role_and_enabled(self) -> None:
        offenders = self._offenders()
        assert offenders == [], (
            "users.role / users.enabled written outside session_auth — route the "
            f"change through set_user_role/set_user_enabled instead: {offenders}"
        )

    def test_the_writer_really_is_in_the_service(self) -> None:
        """The other half: the guard must exist, not just be the only one there."""
        source = (ZOLAI_SRC / "api" / "session_auth.py").read_text(encoding="utf-8")
        assert "UPDATE users SET role =" in source
        assert "UPDATE users SET enabled =" in source
        assert "def set_user_role" in source and "def set_user_enabled" in source
        # Both take the actor's role, so a call without one is a type error, not
        # an unguarded write.
        for name in ("set_user_role", "set_user_enabled"):
            body = source.split(f"def {name}(", 1)[1].split("\n\n\n", 1)[0]
            assert "actor_role" in body, f"{name} lost its actor_role argument"

    def test_the_http_surfaces_delegate_to_the_service(self) -> None:
        # Matched on the ``text("UPDATE users`` *call form*, not the bare phrase:
        # both routers *describe* having no raw SQL in their docstrings, and a
        # guard that fails on prose is a guard nobody can edit through.
        for filename in ("admin_users_router.py", "role_requests_router.py"):
            source = (ZOLAI_SRC / "api" / filename).read_text(encoding="utf-8")
            assert "set_user_role(" in source, f"{filename} changes roles without the guard"
            assert 'text("UPDATE users' not in source, f"{filename} grew a second writer"


# ---------------------------------------------------------------------------
# Bootstrap: no HTTP route can mint the first superadmin
# ---------------------------------------------------------------------------


class TestBootstrapInvariant:
    """``POST /api/v1/admin/users`` is strict-admin, so anonymous is always 401.

    A deployment with zero superadmins therefore cannot create one over HTTP: the
    only writer of roles sits behind a guard that needs an actor already ranked
    superadmin.  Which is what makes ``zolai user set-role <name> superadmin``
    the only bootstrap path, and why the CLI (not an env var, not a first-run
    hook) is the documented answer.
    """

    def test_the_admin_users_surface_is_strict_admin(self) -> None:
        assert rbac.classify_route("POST", "/api/v1/admin/users") == "admin"
        assert rbac.is_admin_path("/api/v1/admin/users", "POST") is True
        assert rbac.is_public_path("POST", "/api/v1/admin/users") is False

    def test_no_public_mutation_route_touches_accounts_or_roles(self) -> None:
        """Nothing public creates an account, so nothing public creates a rank."""
        from zolai.api.server import create_app

        offenders: list[str] = []
        for path, operations in create_app().openapi()["paths"].items():
            if not path.startswith("/api/v1"):
                continue
            for method in operations:
                upper = method.upper()
                if upper not in {"POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if not rbac.is_public_path(upper, path):
                    continue
                if re.search(r"/(users|admin|role|apikey|api-keys)", path, re.IGNORECASE):
                    offenders.append(f"{upper} {path}")
        assert offenders == [], f"a public route reached the account surface: {offenders}"

    def test_the_bootstrap_command_exists_and_names_the_superadmin_tier(self) -> None:
        source = (ZOLAI_SRC / "cli" / "main.py").read_text(encoding="utf-8")
        assert "def user_set_role" in source or "set-role" in source
        assert "superadmin" in source
