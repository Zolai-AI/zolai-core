# Tool wire formats — one canonical definition, two rendered surfaces

**Scope:** how a server-side tool becomes an OpenAI `tools` entry and an MCP
`tools/list` entry, and what the OpenAI-*compatible* function-calling path
actually does. Status: current (2026-10-18).

## 1. One definition, two renderers

`zolai.agent.tools.registry.SPECS` holds one
`zolai.agent.tools.types.ToolSpec` per tool (`name`, `description`,
`parameters` — a JSON Schema object). Nothing else defines a tool shape.
`zolai.agent.tools.render` turns that one definition into each wire shape:

| Format | Renderer | Shape | Consumed by |
|---|---|---|---|
| `openai` | `render_openai(spec)` | `{"type": "function", "function": {name, description, parameters}}` | `build_chat_body` → the provider `tools` array |
| `mcp` | `render_mcp(spec)` | `{"name", "title", "description", "inputSchema", "annotations"}` | `POST /api/v1/mcp` → `tools/list` |

`registry.openai_tool_defs(names)` and `registry.mcp_tools(names)` wrap the
renderers; `registry.native_tool_defs` and `ToolSpec.native_def()` are thin
aliases of the OpenAI renderer kept for existing call sites (identical output).
A parameter schema that is not a JSON-Schema object raises `RenderError` — an
uncallable tool is never published.

There is deliberately **no Gemini or Anthropic renderer**. See §2.

## 2. The OpenAI-compatible function-calling path (kept, not replaced)

The `anthropic` and `google-gemini` catalog rows are `adapter="openai"` pointed
at those vendors' official OpenAI-compatibility endpoints:

* `https://api.anthropic.com/v1/chat/completions`
* `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`

`NATIVE_TOOL_ADAPTERS = {"openai", "openrouter"}` is therefore the complete
function-calling gate — `build_chat_body` attaches `tools` for those rows, and
that includes the two compat rows. The `brain` branch structurally omits
`tools` (asserted by unit test) because the brain path has no native
function calling.

**Proven offline** (`tests/test_llm_compat_function_calling.py`, no socket):

1. the body assembled for the `google-gemini` and `anthropic` rows carries the
   canonical `tools` array (`== registry.openai_tool_defs(...)`);
2. a captured OpenAI-wire response whose `choices[0].message.tool_calls` holds
   `{"id", "type": "function", "function": {"name", "arguments"}}` parses
   through `chat()` into `{"id", "name", "input", "arguments"}` — `input` is the
   decoded dict the loop executes, `arguments` is the raw string a native
   multi-turn replay echoes back verbatim.

## 3. Caveats (real, not hypothetical)

* **`max_tokens`.** The compatibility layers are not required to default
  `max_tokens`, and Anthropic's documents it as a **required** field for its
  OpenAI-compatible endpoint — a request without it can be answered `400
  max_tokens: Field required`. `adapter.chat()` and `build_chat_body()` both
  accept `max_tokens` and forward it **only when a caller supplies it**; the
  default body stays `{model, messages}` (+ `tools` when offered). Nothing is
  invented for a caller.
* **`max_completion_tokens`.** Not used. It is a newer OpenAI-only parameter and
  the compat endpoints do not share its semantics; `max_tokens` is the
  denominator common to all of them. No request is rewritten.
* **`tool_choice`.** Never sent. The OpenAI default (`auto`) is what every
  compat layer implements; sending it would be an unnecessary field.
* **`content: null` with `tool_calls`** is accepted; the adapter reads
  `content or ""`, so a tool-call-only assistant turn yields `text == ""`.
* **One tool per turn.** The agent loop executes at most one tool call per
  turn, and a native replay echoes only that call in the assistant
  `tool_calls` array — echoing ids that have no matching `role:"tool"` message
  is what makes strict OpenAI validation (`tool_call_ids did not have response
  messages`) fail.

## 4. MCP surface

`zolai.mcp.protocol` is a pure, stateless JSON-RPC dispatcher
(`initialize`, `notifications/initialized`, `ping`, `tools/list`,
`tools/call`) advertised over `POST /api/v1/mcp`. The caller's tool set is
derived **server-side** from the authenticated caller's role
(`rbac.role_for` → `registry.allow_list`): anonymous 5 tools, member 7, admin
12. An unknown *or* not-allowed tool name returns JSON-RPC `-32602` with the
same message, so the surface never discloses that an admin tool exists.
