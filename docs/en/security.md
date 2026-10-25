[Español](../es/seguridad.md) | **English**

# Security — known limitations

Beacon is designed for local or internal-network use, by a developer or a team — **not** to be exposed directly to the internet:

- **The API has no authentication by default.** Anyone with network access to the port can read/write the configuration, register projects and see indexed code. A simple key can be enabled with the `BEACON_API_KEY` environment variable (see below) — it keeps the API from being open to anyone on the same network, but it does not replace a real authentication proxy if you are going to expose it beyond `localhost`.
- **No rate limiting.** There is no frequency limit on `sync`/`query`; in normal internal use this is not a problem, but it is not designed for public traffic.
- The folder browser (`/system/browse-dirs`) is limited to the home directory of the user running the process, and repository cloning validates the URL scheme (only http(s)/ssh) — but both assume that whoever reaches the API is already trusted, given the previous point.
- `/system/available-models?host=...` makes the server query the indicated host (to populate the model list in Settings). With the API open, that allows Beacon to be used to make HTTP requests to other machines on its network; another reason to enable `BEACON_API_KEY` if Beacon is not running alone on `localhost`.

## Enabling the API key (optional)

```bash
# Without Docker
BEACON_API_KEY=something-secret beacon serve      # Linux/Mac
$env:BEACON_API_KEY="something-secret"; beacon serve   # PowerShell

# With Docker: create a .env file next to docker-compose.yml
echo "BEACON_API_KEY=something-secret" > .env
docker compose up -d
```

With the variable set, all data/configuration endpoints require the `X-API-Key` header. The web UI lets you enter the same key in **Settings > Security** (it is stored only in your browser, via `localStorage`). Without the variable set (default), the API stays open as before.
