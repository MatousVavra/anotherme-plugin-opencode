# anotherme-plugin-opencode

Drive an [opencode](https://opencode.ai) AI coding-agent server from
AnotherMe — create sessions, send prompts, and watch the agent work, all
from the AnotherMe web UI. Because AnotherMe sits behind its own auth and
is reachable from your phone, this gives you a pocket coding agent for any
project on your server.

## How it works

```
phone ── AnotherMe UI (auth) ── opencode plugin ── opencode sidecar ── your code
```

- The plugin is a **thin, validated proxy**: it forwards a fixed set of
  operations (status, sessions, messages, prompt, abort) to a sidecar
  `opencode serve` instance. It is deliberately *not* a generic passthrough,
  and session ids are regex-validated before ever touching a URL.
- Prompts are sent with `prompt_async` and the UI polls — robust on flaky
  mobile connections.
- All routes inherit AnotherMe's auth; the sidecar itself never needs to be
  exposed.

## Deploy

### 1. Sidecar (main repo `docker-compose.yml`)

```yaml
  opencode:
    image: ghcr.io/anomalyco/opencode:latest
    restart: unless-stopped
    command: ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
    working_dir: /workspace
    volumes:
      - ./workspace:/workspace                      # code the agent works on
      - opencode_auth:/root/.local/share/opencode   # provider keys persist here
    environment:
      - OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD:-}
```

Then configure a provider once (keys persist in the `opencode_auth` volume):

```bash
docker compose run --rm opencode opencode auth login
```

### 2. Install the plugin in AnotherMe

Install via the store (`opencode`) or drop this repo's `plugin/` folder into
`{DATA_DIR}/plugins/opencode/` — the watcher hot-loads it.

Settings (Plugins → opencode → Settings):

| Key       | Default               | Meaning                                  |
|-----------|-----------------------|------------------------------------------|
| base_url  | http://opencode:4096  | Base URL of the sidecar                  |
| password  | (empty)               | `OPENCODE_SERVER_PASSWORD`, if you set one |

The `base_url` can also be set via env (`ANOTHERME_PLUGIN_OPENCODE_BASE_URL`);
saved settings take precedence.

## API

| Method | Path                                  | Purpose                          |
|--------|---------------------------------------|----------------------------------|
| GET    | `/plugins/opencode/status`            | Health + version + projects      |
| GET    | `/plugins/opencode/statuses`          | Per-session run status           |
| GET    | `/plugins/opencode/sessions`          | List sessions                    |
| POST   | `/plugins/opencode/sessions`          | Create session `{title?}`        |
| GET    | `/plugins/opencode/sessions/{id}`     | Session details                  |
| DELETE | `/plugins/opencode/sessions/{id}`     | Delete session                   |
| GET    | `/plugins/opencode/sessions/{id}/messages` | Message history             |
| POST   | `/plugins/opencode/sessions/{id}/prompt` | Send `{text, agent?, model?}` |
| POST   | `/plugins/opencode/sessions/{id}/abort`  | Stop a running agent         |

`model` accepts `"provider/model"` (e.g. `anthropic/claude-sonnet`).
`agent` accepts opencode agents (`build`, `plan`, ...).

## Tests

Standalone, no host needed:

```bash
python -m pytest tests/ -q
```

Uses `tests/fake_plugin_context.py` (the verbatim plugin-contract shim) and
`httpx.MockTransport` to fake the opencode server.

## Notes

- One opencode instance serves one working directory (`/workspace` here).
  For multiple projects, mount them as subdirectories or run more sidecars
  with different `base_url` settings.
- Everything the agent does (file edits, shell) happens inside the sidecar
  container against `/workspace` — scope what you mount accordingly.
