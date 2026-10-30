# Permissions model — roles, scopes, route classes, invariants

**Canonical.** `zolai/api/rbac.py`, `zolai/api/auth.py`, `zolai/api/session_auth.py`
and `zolai/api/rate_limit.py` cite this file as the authority for what a role
means, which actions exist, and which rules may never be traded away. When code
and this page disagree, one of them is wrong — fix the code or this page in the
same change, never silently.

Every claim below names the symbol that implements it, because the tables in
code are the thing tests assert on. This page explains *why* they are shaped
this way; it deliberately does not re-list every number (rate limits, tool
inventories) that would drift.

---

## §1 Roles and the hierarchy

```
anonymous  <  member  <  admin  <  superadmin
```

`zolai.api.rbac.ROLES` is the one ordering (`_RANK` derives from it); nothing
else in the repo may rank roles by hand. `rank_of()` / `has_role()` are the only
comparisons.

| Role | Who | What it is for |
|---|---|---|
| `anonymous` | no credential presented | the public dictionary, search, word reads, assistant chat, MCP tool calls on the public set |
| `member` | any account (every account starts here), or a key holding only member scopes | reading the corpus, running RAG, contributing |
| `admin` | an account with `role='admin'`, or a key holding `apikey:manage` / `settings:write` / `user:manage` | operating the platform: users, keys, settings, review queues |
| `superadmin` | an account with `role='superadmin'`, or a key holding `role:manage` or `*` | **granting roles** — the power that decides who the above are |

One resolver answers "who is this caller": `rbac.role_for(record)`, in two
ordered steps, because a credential may be either kind:

1. **`record["role"]`** — a session or OAuth record carries `users.role`, read
   live. A promotion or a demotion therefore takes effect on the holder's *next
   request*, not at token expiry (`session_auth.record_from_user`,
   `oauth.resolve_access_token`).
2. **The record's scopes** — an API key has no account behind it, so the widest
   scope it holds *is* its rank: `SUPERADMIN_SCOPES = {"*", "role:manage"}`,
   `ADMIN_SCOPES = {"apikey:manage", "settings:write", "user:manage"}`, anything
   else is `member`.

An unrecognised explicit role is **ignored, not trusted** — the scope reading
applies instead, and `role_scopes()` degrades an unknown role to `member`. No
path from a hand-edited database row to a 500 or to a privilege.

### A person is not a credential

`rbac.person_identity(record)` returns `(user_id, username, role)` **only** for
`auth_source == "session"`. An API key has no `user_id`, and an OAuth access
token is held by a client process rather than the human who consented to it.
Surfaces that attribute work to a human — the role-request queue, "my
contributions" — require a person and answer `403 session_required` otherwise.
A key can do a job; it cannot claim a promotion.

---

## §2 The frozen 33-action vocabulary

`zolai.api.auth.VALID_ACTIONS` is **frozen**: adding an action is a design
change, not a bugfix. Scopes are `resource:action` pairs, and a grant may use
three sugar forms (`auth.validate_scopes`):

| Form | Means |
|---|---|
| `dataset:read` | exactly this action |
| `*` | every action (this makes a key a superadmin) |
| `<resource>:*` | every action on that resource, e.g. `dataset:*` |
| `*:action` | that action across resources, e.g. `*:read` |

`auth.scope_allows(scopes, action)` is the only matcher. Note what it does
**not** do: there is no implication between actions. `dataset:edit` does not
grant `dataset:read`, and `user:manage` does not grant `audit:read`. A route
that needs two actions asks for two, and a caller missing the exact one gets
`403 {error: forbidden, action, reason: missing_scope}`.

The 33 actions, by resource (source of truth: `auth.VALID_ACTIONS`):

- `dashboard:` `read`
- `dataset:` `read` `create` `edit` `run_quality` `validate` `publish` `deprecate`
- `source:` `read` `write`
- `pos:` `read` `annotate` `review` `adjudicate`
- `annotation:` `read` `review`
- `quality:` `read` `run` `waive`
- `eval:` `read` `run`
- `pipeline:` `read` `run`
- `rag:` `read` · `catalog:` `read` · `audit:` `read`
- `user:` `manage` · `role:` `manage` · `apikey:` `manage`
- `settings:` `read` `write`
- `agent:` `read` `run`  *(the P2 amendment, 31 → 33)*

Per-action rate budgets live in `rate_limit.ACTION_LIMITS` (a write action is
10–20/min, a read 60/min, `role:manage` 10/min). This page does not duplicate
those numbers; it states the rule: **an action that changes state costs less
traffic than one that reads.**

---

## §3 Role → scope grants

`session_auth.role_scopes(role)` is the only function that turns a role into a
scope set, and the three tuples below are its whole body. They are strict
subsets of §2, and **never contain `*`** — `*` is a key's shape, not a role's
grant.

| Tuple | Contents |
|---|---|
| `MEMBER_SCOPES` | `dataset:read`, `rag:read`, `catalog:read`, `agent:read`, `agent:run`, `pos:read` |
| `ADMIN_EXTRA_SCOPES` | `apikey:manage`, `settings:read`, `settings:write`, `user:manage`, `audit:read`, `dataset:edit`, `pos:review`, `annotation:review`, `quality:read`, `pipeline:read`, `eval:read` |
| `SUPERADMIN_EXTRA_SCOPES` | `role:manage` |

The rule that picks the contents is one sentence: **a role holds the action
behind every tool and every REST read its tier advertises.** A tool declaring
`scope = "pos:read"` is dropped from the allow-list for a record that lacks it,
so a member tier that lists `pos_tag` while no member account carries
`pos:read` leaves the signed-in person weaker than a hand-minted key — and
`POST /api/v1/linguistics/pos` unreachable from every account while a
`pos:read` key could call it. That was true until this task; `pos:read`,
`pipeline:read` and `eval:read` are what closed it. `TestTierScopeParity`
(tests/test_role_hierarchy.py) recomputes all three tiers on every run, so
adding a scoped tool without deciding which role holds the action fails.

`ADMIN_SCOPES = MEMBER + ADMIN_EXTRA`, `SUPERADMIN_SCOPES = ADMIN_SCOPES +
role:manage` — so a superadmin is a strict superset of an admin, which in turn
is a strict superset of a member. The sets are asserted against §2 by tests.

Two decisions worth reading twice:

- **`dataset:edit`, `pos:review`, `annotation:review`, `quality:read` are
  admin, not superadmin.** A signed-in admin must be able to run the review
  queue (`PATCH /api/v1/review/records/...`) and read quality; withholding
  those made the admin tier decorative.
- **`role:manage` is superadmin only, and nothing that tops out at admin
  contains it.** `user:manage` creates accounts and resets passwords; if it
  also set roles, every admin key would be one misclick from minting a
  superadmin.

`session_auth.VALID_ROLES` is `{member, admin, superadmin}` — the subset an
*account* may hold. `anonymous` is a rank, not a row.

---

## §4 Route classes

`rbac.classify_route(method, path)` answers one of four values, and the
declaration tables below are the only input. **`unclassified` is a bug, not a
fourth class**: `tests/test_rbac_public_matrix.py` walks every `/api/v1` path in
the OpenAPI document and fails if any of them is unclassified.

| Class | Anonymous caller | Resolved caller without the scope | Enforcement |
|---|---|---|---|
| **public** | served, in every mode | 403 | `PUBLIC_ROUTES` (exact `(method, path)` pairs) |
| **member** | 401 in `enforce`; dual-accept in `warn` | 403 | `MEMBER_PREFIXES` (prefix match) |
| **admin** | 401 in **`warn` and** `enforce` | 403 | `ADMIN_PREFIXES`, `ADMIN_METHOD_ROUTES` |
| unclassified | — | — | fails CI; add a declaration |

- `PUBLIC_CHAT_PATHS` narrows the *anonymous* bucket on expensive public routes
  (`/api/v1/assistant/chat`, both MCP paths, `/api/v1/learning/run`).
- `OPS_ADMIN_PREFIXES` gates paths that live outside `/api/v1` and carry no
  route-level dependency, so the middleware does it: `/metrics`, `/api/metrics`,
  `/review`, and FastAPI's `/docs` `/redoc` `/openapi.json` (the schema is a
  route inventory, i.e. reconnaissance value). `ZOLAI_DEV_DOCS` (default **off**)
  re-opens only the last three.
- `AUTH_MODE` (`ZOLAI_API_AUTH`) is `warn` (default) / `enforce` / `off`.
  Anything that must *never* be reachable anonymously is declared
  `strict=True` — `auth.require_scope(action, strict=True)` and
  `rbac.require_role(role, strict=True)` — because `warn` mode dual-accepts an
  absent key, and a scope that protects a column cannot tolerate that. The
  records surface (§5, PII) is the case this was written for.
- **A prefix is a blunt instrument.** `/api/v1/role-requests` is declared
  **member**, never admin, because `is_admin_path` prefix-matches and would
  otherwise 401 a member on `GET /api/v1/role-requests/mine` — their own queue.
  The admin-only routes in that family are gated at the route
  (`role:manage` + `require_role("superadmin", strict=True)`), not by the class
  table. Where one prefix carries both public reads and admin mutations
  (`/api/v1/learning`), `ADMIN_METHOD_ROUTES` names the mutation pairs
  explicitly; method-scoped entries are matched **before** `ADMIN_PREFIXES`.

---

## §5 Invariants

These are not preferences. Each is enforced in **one** place, and the place is
named so nobody re-implements it slightly differently.

1. **`session_auth.set_user_role()` is the only writer of `users.role`.**
   `set_user_enabled()` is the only writer of `users.enabled`. A source-shape
   test (`tests/test_role_hierarchy.py`) fails if any other module contains an
   `UPDATE users` touching those columns. Every guard therefore runs on one
   connection, inside one transaction, next to the `UPDATE` it protects.
2. **Only a superadmin changes a role at all.** Below that: `403
   role_change_requires_superadmin`. The HTTP surface stays `user:manage` +
   admin-rank, so an admin can still create members and reset passwords — a
   role field simply refuses, loudly, rather than being silently ignored.
3. **Nobody changes their own role.** `403 self_role_change`. A session may not
   promote itself while it holds it; a peer must decide.
4. **Nobody disables their own account.** `403 self_disable`.
5. **An actor may not touch an account at or above its own rank.** Below
   superadmin, `admin` and `superadmin` rows are read-only to it: `403
   target_requires_superadmin`. (Member rows stay editable — that is what
   `user:manage` is for.)
6. **The last enabled superadmin survives.** Demoting or disabling the only
   enabled superadmin is `409 last_superadmin` — a *conflict*, not a permission
   failure: promote a second one and the same call succeeds. The count runs on
   the same connection as the write, so two concurrent demotions cannot both
   see "there are two".
7. **No HTTP route can create the first superadmin.** Bootstrap is
   `zolai user set-role <name> superadmin` — the CLI is the one caller allowed
   to assert its own actor role, because it is a local operator holding the
   database file, not a network surface. `create_user` refuses a non-superadmin
   actor asking for anything but `member`, so "create an admin" over HTTP is
   `403` too.
8. **A key may never hold power above the credential that minted it.**
   `admin_api_keys_router._refuse_wider_than_actor`: `*` and `role:manage` are
   superadmin-only at `create` **and** `rotate`. Rotation replays the old key's
   scopes, so it is re-checked against the caller — an admin rotating a legacy
   `*` key does not get `*` back.
9. **Guarded writes append an audit row.** `session_auth.record_audit` →
   `data_audit_log`: one row per changed field, with actor, actor role,
   old → new and the reason. `data_audit_log` is created by
   `migrations.create_data_audit_log_table` (registered first in
   `run_all_migrations`, and by `session_auth.ensure_audit_log_table()` for
   CLI-only flows), because a role change with no trail is the one thing you
   cannot reconstruct later. **A password hash, a session token and a key
   plaintext never reach an audit row, a log line, or a response body.**
10. **Contributing requires no role, and a contributor's email is not a public
    record.** `POST /api/v1/contributions` stays open (§6). `email` is dropped
    from the row payload and from the search fields, and
    `GET /api/v1/records?table=contributions` requires `dataset:edit`
    (`records_router.RECORDS_READ_SCOPE`, strict, so `warn` cannot dual-accept
    around it). Consequence stated honestly: `/data` can no longer be used to
    contact a submitter.
11. **A role request is decided by a human, and applying it goes through the
    writer.** `POST /api/v1/role-requests/{id}/decision` calls
    `set_user_role()` and nothing else — so invariants 2–6 refuse an
    approval exactly as they refuse a direct edit, and the queue row stays
    `pending` rather than recording a decision that did not happen. A key
    cannot ask (invariant: a person is not a credential). Rate limit is 5/min
    per `user_id`; the partial unique index on
    `(user_id, requested_role) WHERE status='pending'` is the durable anti-flood
    guard.
12. **Error bodies are authored, never echoed.** `HTTPException(detail=...)`
    carries `{"error": "<stable code>", ...}` from `errors.api_error`, and
    `errors.internal_http_error` logs the traceback without putting `str(exc)`
    on the wire. `tests/test_api_error_envelope.py` enforces it with an
    explicit allowlist. Guard refusals use `session_auth.GUARD_STATUS` (the
    code) and `session_auth.guard_message()` (the sentence) — one table, both
    HTTP surfaces, so the same violation cannot be described two ways.

---

## §6 Contributions, attribution and read-back

| Surface | Class | Notes |
|---|---|---|
| `POST /api/v1/contributions` | public | anonymous stays welcome; `email` optional |
| `GET /api/v1/contributions/mine` | member + **session** | rows where `user_id` is the caller; no `email` column in the projection |
| `GET /api/v1/records?table=contributions` | `dataset:edit`, strict | the admin/ops view; `email` never projected |

Attribution columns are additive and nullable (`migrations.CONTRIBUTIONS_ATTRIBUTION_COLUMNS`):
`user_id` is set **only** for an account-backed submission; a raw key sets
`submitted_by` + `auth_source='api_key'` and leaves `user_id` NULL; an anonymous
POST leaves all three NULL. `ContributionOut.attributed_to` reports which of
those it was. Historical rows stay NULL — an anonymous contribution remains
anonymous retroactively, which is the honest reading of a corpus that was open
before attribution existed.

`contributions` has exactly one DDL home
(`migrations.CONTRIBUTIONS_DDL`); `contributions_router.ensure_contributions_table()`
is a thin wrapper over it so `agent/tools/learning.py` keeps working unchanged.

---

## §7 OAuth grants

The MCP surface (`/mcp`, `/api/v1/mcp`) speaks OAuth 2.1 (RFC 9728 + 8414 +
7591, PKCE `S256` only, DCR). REST keeps keys and sessions.

- `oauth.granted_scopes(user_role, requested)` **can only narrow**: an empty
  request means "everything the role allows"; anything asked for outside the
  role's set is dropped, never widened.
- `oauth.grantable_scopes()` (the discovery document's `scopes_supported`) is
  the union of all three role sets, including `role:manage` — advertising a
  scope is not granting one, and a scope missing from the list simply looks
  unmintable to a well-behaved client.
- `oauth.resolve_access_token()` reports the account's **live** `users.role` and
  re-narrows the stored grant against it on every request (invariant 1's
  consequence for tokens): a demotion shrinks a live token immediately, a
  promotion does not silently widen a grant nobody re-consented to. Disabling
  or deleting an account kills its grants with it — one identity layer, one kill
  switch.

---

## §8 MCP tool tiers

**Roles are four; tool tiers are three.** `superadmin` is the power to grant
roles, which is a route guard, not a larger inventory of tools, so
`registry.ROLE_TO_TIER` maps it onto the `admin` tier and an unknown role onto
`member` — never onto a tier `allow_list()` would raise on, and never onto the
anonymous set for a caller the server just identified.

| Tier | Inventory | Which roles |
|---|---|---|
| `public` | the anonymous retrieval/analysis set (8 of the 10 retrieval tools — `analyze_text` and `word_related` need a credential) | `anonymous` |
| `member` | every Zolai analysis tool + `foundation_*` + `language` + learning reads (31) | `member` |
| `admin` | that plus the learning write-back channels, admin extras and the optional web tool (42) | `admin`, `superadmin` |

`registry.allow_list_for_record` derives the tier from `rbac.role_for` (never
from a message field) and then applies each spec's own `scope`
(`tool_scope_allows`), so **a grant is never wider than the scopes it carries**
even when the tier is broader.

---

## §9 Operating it

```bash
zolai user create --username <name> --role member --password-stdin   # default member
zolai user set-role <name> superadmin          # bootstrap role #1 — CLI only
zolai user promote <name> --to admin           # same writer, direction spelled out
zolai user demote <name>                       # refuses the last superadmin
zolai rbac-audit                               # read-only: who can grant roles, and how
zolai apikey create --name <n> --scopes dataset:read,catalog:read
zolai apikey revoke <id-or-prefix>
```

`zolai rbac-audit` reports accounts by role, the enabled-superadmin count, every
non-revoked key holding `*` or `role:manage` (prefix masked, 6+4), and pending
role requests. It **changes nothing, by design**: silently editing somebody
else's credential is worse than reporting it. Act with the commands above.

A minted key plaintext is returned once, is never stored (SHA-256 + display
prefix only), and must never be put in a URL or query string — see
`context/code-standards.md` § Secret Hygiene.
