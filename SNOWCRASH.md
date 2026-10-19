# SnowCrash chat and per-user upstream model catalogs

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

## SnowCrash UI and deployment configuration

This fork presents SnowCrash to customers, prospects and staff. The supplied
logo is preserved in `static/static/snowcrash-logo.svg`. Small derived icons use
navy `#0F1729`, red `#E5322B` and white. `static/static/custom.css` is the theme
source, using Open WebUI's existing `/static/custom.css` hook. It sets navy chat
headers and table headers, red buttons/focus accents and neutral gray `#6E7787`,
with light/dark login cards. No theme loader or CSS environment setting is needed.
The source does not provide a general custom-CSS env variable; the static hook
already exists in `src/app.html`.

### Phase A: shared connection now

The complete, secret-free env block is
[`deploy/snowcrash/phase-a.env.example`](deploy/snowcrash/phase-a.env.example).
Load that file into the OWUI container and supply the existing `OPENAI_API_KEY`,
`OAUTH_CLIENT_SECRET`, stable `WEBUI_SECRET_KEY` and stable
`OAUTH_SESSION_TOKEN_ENCRYPTION_KEY` from the protected deployment env file.
The example intentionally does not assign `OPENAI_API_KEY`, so it cannot erase
the supplied key. `OPENAI_API_KEYS=` falls back to that singular key.

Key phase-specific settings (the linked file contains the entire block):

```dotenv
OPENAI_API_BASE_URL=http://sc:8000/v1
OPENAI_API_BASE_URLS=http://sc:8000/v1
OPENAI_API_KEYS=
OPENAI_API_CONFIGS={"0":{"enable":true,"auth_type":"bearer","model_ids":[],"headers":{},"forward_cookies":false}}
BYPASS_MODEL_ACCESS_CONTROL=true
DEFAULT_MODELS=quorum:auto
OAUTH_SCOPES=openid email profile snowcrash-customer-claims
ENABLE_OAUTH_ROLE_MANAGEMENT=false
```

The customers-realm client remains `snowcrash-chat`. Role management is left off
in A so existing customers do not depend on the new `owui_roles` mapper. Existing
OWUI administrator assignments remain effective; new users get role `user`.
Keycloak owns the prospect email-link and workforce password sign-in flows;
the OWUI login page has one **Sign in with SnowCrash** button. Auto-redirect is
false to keep that button visible. The other built-in OAuth providers, LDAP,
local password login/change, signup and initial local-admin signup are disabled.

Phase A discovers the shared credential's SC catalog. It does **not** provide
per-user SC identity, tenant isolation or individual $5 accounting. SC enforces
the shared key's tenant/model/budget policy. Use the later phase for the
per-prospect/user $5 monthly limit; OWUI UI permissions do not enforce spending.
Do not enable trusted user headers as a substitute for the designed token path.

### Phase B: per-user token forwarding later

The complete env block is
[`deploy/snowcrash/phase-b.env.example`](deploy/snowcrash/phase-b.env.example).
Replace A with B; do not layer their conflicting connection settings. Remove the
shared SC secret from the deployment env file and supply only the stable OWUI
and OAuth secrets listed above. B explicitly clears both OpenAI key variables.

```dotenv
OPENAI_API_BASE_URL=http://sc:8000/v1
OPENAI_API_BASE_URLS=http://sc:8000/v1
OPENAI_API_KEY=
OPENAI_API_KEYS=
OPENAI_API_CONFIGS={"0":{"enable":true,"auth_type":"system_oauth","model_catalog_scope":"user","model_ids":[],"headers":{},"forward_cookies":false}}
BYPASS_MODEL_ACCESS_CONTROL=false
DEFAULT_MODELS=quorum:auto
OAUTH_SCOPES=openid email profile snowcrash-customer-claims snowcrash-inference
ENABLE_OAUTH_ROLE_MANAGEMENT=true
OAUTH_ROLES_CLAIM=owui_roles
OAUTH_ALLOWED_ROLES=owui-user,owui-admin
OAUTH_ADMIN_ROLES=owui-admin
```

Enable B only after the design's customers-realm brokers, protected origin claims,
SC audience/scope, access-token authentication and identity/tenant bindings are
verified. Issue `owui-user` to ordinary users and `owui-admin` only to designated
chat administrators. `platform-operator` alone grants no OWUI administrator role.
Tokens need audience `snowcrash-sc-inference`; ID tokens and OWUI session tokens
are not SC inference credentials. SC remains responsible for each user's current
catalog and $5 Walkup tenant/user caps, including existing prospect snapshots.
No live Keycloak or SC changes are made by this patch.

### Effective settings on an existing installation

Both complete files set `ENABLE_PERSISTENT_CONFIG=false` and
`ENABLE_OAUTH_PERSISTENT_CONFIG=false`. This deployment-managed mode makes the
env defaults effective even when older config rows exist. It does not delete the
database; `RESET_CONFIG_ON_START=false`. Admin changes to persistent config are
not effective in this mode. Supply phase vars through the container environment
(or Compose `env_file`), not by merely placing a file in the repository. Remove
conflicting explicit Compose `environment` entries and old plural connection/key
variables; those can override an env file.

If persistent config is intentionally re-enabled, reconcile the effective rows
before opening access: `openai.api_base_urls`, `openai.api_keys`,
`openai.api_configs`, `ui.default_models`, `ui.default_pinned_models`,
`ui.model_order_list`, `ui.default_interface_settings`, `user.permissions` and
all feature rows corresponding to the env switches. Use `/openai/config`,
`/api/config` and the administrator settings UI to verify the effective result.
Keep exactly one SC connection, empty `model_ids`, empty headers and no prefix.
Remove every stale `fleet:*` manual ID, preset, pin, group/default model list or
saved user default. The current router ID is **`quorum:auto`**, not `fleet:auto`.
Old chats keep their history; choose an available current ID before continuing.
A vanished per-user selection remains blank rather than choosing another model.

Normal users have no workspace/model creation, tools/functions marketplace,
arena/rating, personal connections, web search, images, code execution/interpreter,
API keys, channels, apps, calendar, automations, notes, memories or webhooks.
Chat history, deletion/export, file attachments, folders and personal interface
settings remain available. Follow-up/tag generation and multiple-model chat are
off to reduce surprise charges. Titles use the selected model; task model
variables are empty, so background tasks do not force a more expensive model.
Administrative pages retain upstream role guards. Existing group permissions
are additive: remove workspace/feature grants and old default-model assignments
from prospect/customer groups; env defaults do not revoke an explicit group grant.

For a fresh database, perform the first OAuth sign-in with the designated
administrator before admitting prospects. Upstream automatically promotes the
first/sole OAuth user even with role management enabled; the env blocks do not
change that bootstrap behavior. Verify subsequent normal users retain `user`.

`ENABLE_COMMUNITY_SHARING=false` hides community imports/sharing/reviews and now
also the About social links and translation solicitation. With
`ENABLE_VERSION_UPDATE_CHECK=false`, update network checks, release links,
automatic changelog display, its modal and its settings controls are disabled,
including for existing saved `showChangelog=true`. About keeps upstream copyright,
license and third-party attribution; upstream LICENSE files are unchanged.

### Assets and regeneration

```sh
uv pip install --python .venv/bin/python -r scripts/snowcrash/requirements.txt
.venv/bin/python scripts/snowcrash/generate-assets.py
.venv/bin/python scripts/snowcrash/generate-assets.py --check
```

The generator uses the checked-in source SVG, pinned CairoSVG/Pillow versions
and no network at generation time. It synchronizes `static/static/` and
`backend/open_webui/static/`, plus the root favicon and static manifest. Backend
startup copies the built frontend static assets, so edit the frontend source
and rebuild; a backend-only icon replacement would be overwritten.

Assets: `snowcrash-logo.svg` (original), `favicon.svg` (square derived tile),
`favicon.png` (64), `favicon-96x96.png`, `apple-touch-icon.png` (180),
`web-app-manifest-{192x192,512x512}.png`, `logo.png` (500),
`splash.png` / `splash-dark.png` (192), `favicon.ico` (16/32/48/64),
`custom.css`, `site.webmanifest`, `static/favicon.png` and `static/manifest.json`.
All binaries are small icons; no large brand image or generated build is added.
The existing external-manifest setting points to
`http://127.0.0.1:8080/static/site.webmanifest` so the runtime `/manifest.json`
uses the SnowCrash name/icons/colors. Adjust its loopback port if the backend
listens elsewhere. No public URL fetch is needed for the brand assets.

Source changes kept limited to:

- `backend/open_webui/env.py`: use the configured `WEBUI_NAME` verbatim rather
  than appending an upstream suffix. Its default remains Open WebUI.
- `src/app.html` and `src/lib/constants.ts`: SnowCrash initial page title/name
  and navy dark-theme browser chrome (also in personal General settings).
- `src/routes/auth/+page.svelte`: OIDC button label and a stable styling ID;
  its provider name and availability still come from config. Disabled local
  forms also stay hidden for `?form=` and initial onboarding.
- `src/lib/components/chat/Chat.svelte`: apply the configured default only when
  initializing a new scoped chat, only if advertised by SC; refresh/revocation
  selection clearing stays unchanged.
- `src/routes/(app)/+layout.svelte`, personal/admin General, About,
  InterfaceSettings and Sidebar/UserMenu: extend the existing update/community
  flags to the hardcoded changelog and marketing entry points.

## UI upgrade verification (2026-10-19)

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv/bin/python -m pytest
  -p no:cacheprovider -q backend/tests`: **46 passed**.
- `npm run test:frontend -- --run`: **14 passed** across three test files.
- `NODE_OPTIONS=--max-old-space-size=12288 npx vite build`: production site
  compiled with Svelte warnings. A first attempt with the default 4 GB heap hit
  Node's memory limit; use the larger heap for this repository.
- Targeted Prettier, Ruff formatting/Pyflakes and `git diff --check` passed.
- `generate-assets.py --check` passed; PNG dimensions and sizes verified
  (largest binary about 21 KB). Both env files use recognized source settings,
  empty discovered `model_ids` and `quorum:auto`, with no retired IDs.
- Local Chromium preview uses a fixture `/api/config`, not a live identity
  provider. The production login renders the SnowCrash title, one branded SSO
  button and no local login/signup inputs on desktop light and mobile dark
  previews, including `?form=signin`. Live Keycloak email-link/password
  flows, SC identity/budget accounting and deployment remain integration checks.

The UI verification reused local `node_modules` and `.venv`. CairoSVG/Pillow
and a missing httpx test dependency were installed only in the ignored `.venv`;
application dependency manifests and lockfiles were not changed. Verification
logs/screenshots are local ignored files under `.cache/snowcrash/` and
`.snowcrash-build.log`.

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
