[Español](../es/uso.md) | **English**

# Usage

## CLI

```bash
beacon doctor                        # checks Ollama, models and configured repos
beacon projects                      # lists registered projects

# --- project management (CRUD) ---
beacon add <id> --repo-path <path>   # registers an already cloned local repo
beacon add <id> --url <repo_url>     # or clones a remote one and registers it
beacon add <id> --url <repo_url> --private   # private repo: asks for a token (hidden while typing)
beacon edit <id> --name "..."        # edits the name and/or repo_path of a registered project
beacon edit <id> --repo-path <path>
beacon edit <id> --auto-watch        # enables the automatic watcher (--no-auto-watch disables it)
beacon remove <id>                   # unregisters a project (config.yaml)
beacon remove <id> --purge-data      # also deletes data/<id>/ (index + docs) from disk

# --- indexing and queries ---
beacon sync <project_id>             # indexes/reindexes incrementally (for URL-cloned repos, runs git pull first)
beacon sync <project_id> --docs      # also updates the documentation (same as the UI "Sync" button)
beacon sync <project_id> --full      # rebuilds the whole index (e.g. after updating Beacon)
beacon docs <project_id>             # generates .md documentation (requires an up-to-date 'sync')
beacon docs <project_id> --full      # regenerates all the documentation
beacon ask <project_id> "question"   # queries the RAG from the terminal
beacon export <project_id>           # packs the index + docs into a portable .zip
beacon import <file.zip>             # registers a project from an exported .zip
beacon serve                         # starts the API + UI at http://127.0.0.1:8000
```

> **Note on Windows terminals**: the CLI commands use `rich` for colored
> output and check marks, which requires a real Win32 console. In **Git Bash /
> MinTTY** this can make commands such as `beacon doctor` fail with a
> traceback when they try to write a colored character (the terminal does not
> expose the console handle that `rich` needs). It is not a Beacon bug — run
> the CLI from **PowerShell**, **cmd.exe** or **Windows Terminal**, where it
> works without problems.

**Console language**: help and messages are shown in Spanish if the operating system is in Spanish (any variant) and in English otherwise. To force one, set `BEACON_LANG=es` or `BEACON_LANG=en`. Command and option names are always in English.

## Open questions about the whole repository

Questions such as "what does this project do?" or "how is it organized?" do not resemble any single piece of code, so similarity search cannot answer them. When documentation exists, Beacon builds a **repository overview** from it (`OVERVIEW.md`): a summary per folder, built bottom-up from the purpose of each file, and a summary of the project. It is updated together with the documentation (`beacon docs`, `beacon sync --docs`) and only the folders whose content changed are summarized again. Open questions are answered from the overview plus a few code chunks, and the overview appears as its own group in the sources. Without documentation, they are answered like any other question.

## Disk space

Each project keeps three things in `data/<id>/` that can be deleted separately: the **clone** of the repository (only if Beacon cloned it from a URL; your local folder is never touched), the **index** and the generated **documentation**.

```bash
beacon space                              # how much each project uses
beacon clean <id> --clone                 # deletes the clone (downloaded again on the next sync)
beacon clean <id> --index --docs          # deletes the index and the documentation (rebuilt with sync)
```

The project stays registered: whatever was deleted is recreated on sync. The same information and the same buttons are in **Settings → Storage** in the web interface. To remove a project completely, use `beacon remove <id> --purge-data`.

## Web interface

With `beacon serve` running (and the UI already built, see [manual-installation.md](manual-installation.md)), open `http://127.0.0.1:8000`:

- **Chat**: natural-language questions about the indexed code. It keeps the session's question history (reset when you change project) and shows, next to each answer, its sources split into semantic match and call graph, with the similarity, the lines and the code of each one. The header shows the model in use and whether Ollama responds; if a query fails, the question is kept and can be retried.
- **Docs**: tree of the documentation generated per file, with collapsible folders and a text filter. `OVERVIEW.md` is the repository overview: one summary per folder and one for the whole project.
- **Settings**: projects (registration by local path or cloning by URL from GitHub/GitLab/Bitbucket, syncing several at once, optional automatic watcher and deletion with confirmation), AI provider and models (detected from Ollama, with a warning if a configured model is not installed) and API key.

The light or dark theme is chosen in the sidebar, and the typefaces are bundled: the interface makes no requests to external services.

It supports Spanish and English (selector in the sidebar).

## Changing the language model and its context window

The model that writes the answers and the documentation is chosen in `config/config.yaml` (or in Settings, in the interface) with `llm_model`, and can be any model Ollama has installed. The context window requested is `llm_num_ctx` and defaults to 8,192 tokens, the maximum of `llama3:8b`. With a model that supports more context, and the video memory to sustain it, you can raise it:

```yaml
ai_provider:
  llm_model: llama3.1:8b
  llm_num_ctx: 16384
```

Beacon builds the prompt with the budget of the configured window, so a larger window lets more fragments into each query. Keep in mind:

- **Video memory grows with the window and with the model size.** On an 8 GB GPU, `llama3.1:8b` with 16,384 tokens used 7.3 GB and Ollama split it between GPU (84 %) and CPU (16 %); the same query took about 23 s with the model loaded, compared with about 6 s with `llama3:8b` and 8,192 tokens. A 32B or larger model needs a GPU with more capacity.
- **The quality measurements of this project correspond to `llama3:8b` with 8,192 tokens.** With another model or window it is advisable to repeat `tools/eval_answers.py` and `tools/eval_retrieval.py`.
- If the window is not set or the value is not valid (an integer of at least 2,048), 8,192 is used. Saving the configuration from the interface keeps the value.

## Automatic watcher: when it runs

The watcher is not a separate process or a cron job: it is a **background
thread inside the `beacon serve` process** (`_auto_watch_loop` in
`src/core/api.py`). Every 5 minutes it checks the projects with `auto_watch`
enabled; if the project is remote it runs `git pull`, and then syncs only if
there are new commits. It works the same with or without Docker: all it needs
is for the server to be running.

Edge cases to keep in mind:

- **Without `beacon serve` there is no watcher.** CLI commands (`beacon sync`,
  `beacon ask`, etc.) do not start the thread; if you only use the CLI, sync
  by hand with `beacon sync <id>`.
- **It stops with the server.** The thread is a *daemon*: when `beacon serve`
  is closed (or the `beacon` container is stopped) the watcher stops with it
  and resumes when you start it again.
- **The first check happens 5 minutes after startup**, not at the start (the
  loop sleeps before checking). If you need the index up to date right away,
  use `beacon sync <id>` or the "Sync" button in the UI.
- **Changes to `auto_watch` do not require a restart.** Each cycle rereads
  `config.yaml`, so `beacon edit <id> --auto-watch` / `--no-auto-watch` (or the
  UI toggle) takes effect on the next cycle.
- **It does not collide with other syncs, not even from another console.**
  Each project has an operating-system-level lock (`data/<id>/.sync.lock`). If
  the watcher, the UI or a `beacon sync`/`beacon docs` in another console is
  already working on a project, the second one does not start: the CLI ends
  with a notice, the UI shows the message in the sync status and the watcher
  retries on the next cycle. If a process dies midway (Ctrl+C, the console is
  closed), the operating system releases the lock by itself; there is nothing
  to delete by hand.
- **An error does not stop the watcher.** If a project fails (for example, a
  `git pull` without network), it is logged, the project is left with status
  `error` in the UI and the cycle continues with the others.
- **A single server process.** `beacon serve` starts Uvicorn with a single
  process, so there is a single watcher. With several *workers* (`uvicorn
  --workers N`) there would be one watcher per worker; the per-project lock
  prevents them from writing at the same time, but there would be redundant
  checks, so that configuration is not recommended.

## Exporting/importing projects

To take an already indexed project to another machine (useful for a demo or a defense, without depending on reindexing live or on Ollama being fast that day):

```bash
beacon export <project_id> --output my-project.beacon.zip

# on the other machine:
beacon import my-project.beacon.zip
# or with another id/path if the machine changed:
beacon import my-project.beacon.zip --id other-id --repo-path /new/path/to/repo
```

The `.zip` includes the ChromaDB index and the generated documentation — queries (`ask`/`query`) work immediately after importing. Incremental `sync`/`docs` will need `repo_path` to point to a real clone of the repo on the destination machine.
