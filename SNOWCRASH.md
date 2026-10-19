# Per-user upstream model catalogs

This opt-in patch targets Open WebUI v0.12.3, upstream base `91ea4e342`, on
`snowcrash/user-model-catalog`. It adds `model_catalog_scope: "user"` to an
OpenAI connection. There is no schema migration or new credential store.

## Behavior

A scoped connection must use `auth_type: "system_oauth"`. Each authenticated
request fetches `/models` using that user's OAuth **access token**. Discovery,
chat dispatch, continuations, tool follow-ups and auxiliary generation share a
request-local snapshot of model IDs and connection settings. Parallel requests
and users do not share these snapshots. A new HTTP request gets a new catalog.
SC must still enforce current identity, tenant, entitlement and spending policy
at inference time, including when policy changes during a running chat/task.

The scoped path bypasses aiocache, `BASE_MODELS`, `OPENAI_MODELS`, `MODELS` and
Redis model caches. Missing credentials, HTTP errors, malformed catalogs and
valid empty catalogs yield no models from that connection, with a visible error.
They never substitute manual IDs, a shared API key or a cached user's list.
Catalog errors are returned alongside `data` as `errors` and
`model_catalog_scope: "user"`; the selector displays them. Upstream response
bodies and credentials are not included in these errors.

Membership authorizes raw upstream IDs for users **and administrators**,
regardless of OWUI ACL bypass settings. Scoped IDs ignore local preset rows,
manual `model_ids` and connection prefixes. They do not need workspace model
records or grants. Synthetic presets, arena entries and pipes do not augment a
catalog with only scoped connections. In mixed configurations, ordinary
connections keep OWUI ACLs and presets; scoped entries take precedence on ID
collisions. Prefer a dedicated SC connection/instance configuration below for
an exact SC-only selector. Personal/direct connections are excluded when the
user catalog is active. Connection settings are preserved when edited in the UI.

Scoped `system_oauth` header construction fails closed if the access token cannot be
retrieved. An explicit OAuth session cookie must succeed; an invalid/expired
cookie does not try a different session. Cookie-less execution can use the
existing user-scoped OAuth-session resolver only when `request.state.user`
matches the task owner. Automations, subagents and timers supply their
revalidated active owner. Subagent dispatch uses that freshly loaded user.
Configured Authorization headers cannot replace the user's OAuth token.
Other authentication modes retain their behavior.

The selector refreshes when opened, on window/tab focus, and every 15 seconds
while open. It refreshes before sending, regenerating or continuing a reply.
Empty/error refreshes clear vanished selections. No different paid model is
chosen automatically. The 15-second interval bounds the timer delay while the
tab is active; network time and browser background throttling can extend it.
Backend authorization does not depend on the displayed list.

Connections without this field retain the normal catalog/cache and authentication
behavior. The fail-closed OAuth path is enabled by the connection opt-in.

## Configuration

Apply the following environment from the integration design:

```dotenv
WEBUI_URL=https://chat.snowcrashlabs.com
WEBUI_AUTH=true
ENABLE_LOGIN_FORM=false
ENABLE_SIGNUP=false
ENABLE_OAUTH_SIGNUP=true
DEFAULT_USER_ROLE=user

OAUTH_CLIENT_ID=snowcrash-chat
OPENID_PROVIDER_URL=https://auth.snowcrashlabs.com/realms/snowcrash-customers/.well-known/openid-configuration
OPENID_REDIRECT_URI=https://chat.snowcrashlabs.com/oauth/oidc/login/callback
OAUTH_SCOPES=openid email profile snowcrash-customer-claims snowcrash-inference
OAUTH_CODE_CHALLENGE_METHOD=S256
OAUTH_TOKEN_ENDPOINT_AUTH_METHOD=client_secret_basic
OAUTH_AUTO_REDIRECT=true

ENABLE_OAUTH_ROLE_MANAGEMENT=true
OAUTH_ROLES_CLAIM=owui_roles
OAUTH_ALLOWED_ROLES=owui-user,owui-admin
OAUTH_ADMIN_ROLES=owui-admin
ENABLE_OAUTH_GROUP_MANAGEMENT=false
ENABLE_OAUTH_GROUP_CREATION=false
OAUTH_MERGE_ACCOUNTS_BY_EMAIL=false
ENABLE_OAUTH_EMAIL_FALLBACK=false
ENABLE_OAUTH_ID_TOKEN_COOKIE=false

ENABLE_OPENAI_API=true
OPENAI_API_BASE_URLS=http://sc:8000/v1
OPENAI_API_KEY=
OPENAI_API_KEYS=
ENABLE_OLLAMA_API=false
ENABLE_DIRECT_CONNECTIONS=false
ENABLE_FUNCTIONS=false
ENABLE_EVALUATION_ARENA_MODELS=false

BYPASS_MODEL_ACCESS_CONTROL=false
BYPASS_ADMIN_ACCESS_CONTROL=false
ENABLE_FORWARD_USER_INFO_HEADERS=false
ENABLE_BASE_MODELS_CACHE=false

WEBUI_AUTH_COOKIE_SECURE=true
WEBUI_SESSION_COOKIE_SECURE=true
ENABLE_OAUTH_PERSISTENT_CONFIG=false
RESET_CONFIG_ON_START=false

TASK_MODEL=
TASK_MODEL_EXTERNAL=
```

Supply `OAUTH_CLIENT_SECRET`, a stable `WEBUI_SECRET_KEY` and a stable
`OAUTH_SESSION_TOKEN_ENCRYPTION_KEY` through the deployment secret mechanism.
Forward an access token with the configured SC audience/scope; an ID token or
OWUI session token is not an inference credential.

Set the effective connection JSON (persisted configuration takes precedence
over environment defaults):

```json
{
	"openai.api_base_urls": ["http://sc:8000/v1"],
	"openai.api_keys": [""],
	"openai.api_configs": {
		"0": {
			"enable": true,
			"auth_type": "system_oauth",
			"model_ids": [],
			"headers": {},
			"forward_cookies": false,
			"model_catalog_scope": "user"
		}
	}
}
```

For a fresh/unpersisted configuration the connection defaults can also be
provided as:

```dotenv
OPENAI_API_CONFIGS={"0":{"enable":true,"auth_type":"system_oauth","model_ids":[],"headers":{},"forward_cookies":false,"model_catalog_scope":"user"}}
```

Inspect `/openai/config` as an administrator after applying configuration.
Existing installations must reconcile the persisted `openai.api_configs` row;
changing environment variables alone may not replace it. Remove stale shared
keys and Authorization headers. Keep `model_ids` empty even though scoped
discovery ignores manual IDs. No SC or Keycloak changes are included here.

## Validation

Run from this repository with the project's Python dependencies plus pytest,
pytest-asyncio and Ruff, and with `npm ci --force` completed:

```sh
PYTHONPATH=backend python -m pytest -p no:cacheprovider -q backend/tests
ruff format --check . --exclude .venv --exclude venv
ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 .
npm run test:frontend -- --run
npm run check
```

Backend tests cover concurrent disjoint users, empty/error/malformed results,
entitlement changes, admin/bypass denial, raw IDs, fixed connection settings,
OAuth headers, missing/expired sessions and authenticated task ownership.
Router/helper unit tests load the actual function bodies with mocked service
boundaries so they do not start optional RAG/plugin services or a database.
Frontend Vitest covers refresh timing, focus, selection clearing, exact catalogs,
request coalescing and clearing on authentication/network failure. Full browser,
streaming and deployed Keycloak/SC accounting checks remain integration work.

CI uses Ruff format and selected Pyflakes rules for Python. Its frontend job
runs Prettier, i18n extraction, build and Vitest. `package.json` also exposes
ESLint and Svelte type checks; this base has existing failures in those checks.
Avoid repository-wide autofix scripts when preparing a minimal patch.

## Patch files and local verification

Backend: `backend/open_webui/main.py`, `models/config.py`,
`routers/{openai,tasks,chats}.py`, and
`utils/{model_catalog,models,headers,chat,middleware,embeddings,automations,subagents,timers}.py`
under `backend/open_webui/`; tests in
`backend/tests/test_user_model_catalog.py`.

Frontend: `src/lib/apis/index.ts`, `src/lib/apis/modelCatalog.test.ts`,
`src/lib/components/AddConnectionModal.svelte`,
`src/lib/components/chat/{Chat,ModelSelector}.svelte`,
`src/lib/components/chat/ModelSelector/Selector.svelte`,
`src/lib/stores/modelCatalog.ts`, and
`src/lib/utils/{modelCatalog.ts,modelCatalog.test.ts}`. Documentation: this file.

Local results on the supplied base:

- Pytest: 27 tests passed, using the base's FastAPI, Pydantic and aiohttp versions.
- Vitest: all 14 tests passed, including 10 added catalog tests.
- Repository-wide CI Ruff formatting and selected Pyflakes checks passed;
  targeted Prettier and ESLint for new files passed; `git diff --check` passed.
- Existing modified frontend files have 143 ESLint errors on both the clean
  base and patch, with no added messages. Svelte-check reports 5,696 errors and
  188 warnings in both versions. These checks are not green on this base.
- `npm run build` passed encryption verification but stopped in existing Pyodide
  asset preparation with `EPERM` copying its lockfile. The removed tracked
  lockfile was restored. `npx vite build` then completed successfully and wrote
  the production site. This does not verify a full Pyodide asset refresh.
- No deployment, live Keycloak/SC checks, commit or push was performed.

### Security review follow-up

All three findings in `out-rev-owui.md` are fixed:

- Cookie-less subagents and timers revalidate their owner's current user role
  and active auth record, then set `request.state.user` before dispatch. Valid
  sessions supply the owner's OAuth token; expired sessions remain closed.
  Tests: `test_subagent_request_uses_revalidated_owner_oauth` (4 cases),
  `test_subagent_request_rejects_invalid_owner` (4 cases), and
  `test_timer_request_uses_only_active_owner_oauth` (6 cases).
- Manual compaction resolves and passes `get_request_models(request, user)`
  throughout its handler. The HTTP route test
  `test_compact_chat_route_uses_user_snapshot_with_empty_shared_registry`
  succeeds with an empty shared registry and an allowed user model.
- Direct OpenAI completion and Anthropic target resolution reject requested IDs
  absent from an active user snapshot before looking up a preset. The test
  `test_direct_routes_reject_existing_preset_before_resolution` (4 cases)
  returns an actual `ModelModel` preset from the mocked storage boundary and
  verifies rejection for users and administrators in both paths.

Before changing the production code, the expanded backend suite reported
**15 failed, 31 passed**: all 27 existing tests passed, 15 new cases reproduced
the findings, and 4 additional owner-denial controls already passed. There were
no collection errors in that run. With the fixes, **46 backend tests and all
14 frontend tests pass (60 total)**. Repository Ruff formatting and selected
Pyflakes checks, targeted frontend formatting/ESLint, and `git diff --check`
also pass. Frontend source is unchanged by these review fixes.

The verification venv and logs are under `$HOME/.cache/owui-review-fix/`;
`before.log` records the failures against the pre-fix code. Pytest uses
`-p no:cacheprovider`. The tests mock persistence/provider boundaries; live
OAuth/SC deployment and accounting checks retain the integration limits above.

## Rebase procedure

1. Save/review this working-tree diff before switching versions. Do not commit
   deployment secrets or generate a shared SC credential.
2. Apply the patch to the new upstream release. Review connection config repair
   fields, OAuth ownership/refresh APIs, catalog/cache aggregation, every model
   dispatch path, background/continuation request construction, selector lifecycle
   and frontend default-selection logic. Preserve the upstream license metadata.
3. Check every use of `app.state.MODELS`, `OPENAI_MODELS` and `BASE_MODELS` in the
   chat, middleware, tasks and compatibility APIs. Scoped routing must use the
   same authenticated request snapshot, including valid empty snapshots.
   Never serialize scoped snapshots into process/Redis model registries.
4. Run the checks above and compare existing lint/type failures against the new
   clean upstream base. Re-run the backend tests when route signatures or helper
   bodies change, and add coverage for any new dispatch API.
5. In an isolated deployment, alternate two users with disjoint catalogs, empty
   a catalog, revoke a session and remove a model while its selector is open.
   Test administrators, manual forbidden IDs, continuation, streaming, titles,
   follow-ups and cookie-less owner tasks. Verify SC dispatch and accounting
   identity/counters; no shared-key use or cross-user model visibility is allowed.
6. Review effective persisted configuration again, then build the pinned image.
   Keep upstream database migration/backup and rollback procedures separate from
   this patch, which introduces no migration itself.
