[Español](../es/estudio.md) | **English**

# Study mode

`beacon study` opens an assistant to take part in the Beacon study: you choose a public repository,
ask free-form questions and rate each answer. Everything happens on the participant's machine; the only
result is a file that the person reviews and, if they want, sends with a button or on their own. Nothing leaves the machine unless they ask for it.

## For participants

Requirements: Python 3.10, 3.11 or 3.12 (3.13 or later does not work yet), Git, Node 20 or later (to build the interface once) and
[Ollama](https://ollama.com) with the models Beacon uses (`ollama pull llama3:8b` and
`ollama pull qwen3-embedding:0.6b`). A GPU speeds up the answers a lot, but it is not required.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python tools/install.py    # environment, dependencies, web interface and Ollama models
.\beacon                   # opens a console with the environment active (Linux/macOS: ./beacon)
beacon study
```

If Python 3.13 or later is your default, run the installer with a supported version (for example `py -3.11 tools/install.py`).

The assistant opens in the browser (`http://127.0.0.1:8000/study`) and guides you through five steps:

1. **Participation.** Before you accept, the assistant checks that everything needed is in order: Ollama responds, both models are downloaded (if one is missing, it shows the exact `ollama pull`), there is a GPU and disk space, and there is a connection to clone. If something essential is missing, it does not let you start until you fix it and press "Retry". Participation is accepted explicitly; without that nothing is saved.
2. **Machine.** It shows exactly what would be saved (CPU, RAM, GPU and VRAM, operating system, Ollama
   version and models). It is optional and can be skipped.
3. **Repository.** An https address of a public repository (up to 3,000 code files). Beacon clones it, indexes it and loads the answer model into memory, so the time of the first question does not include that load.
4. **Questions.** At least 3, about things you really want to know about the code. Each answer is rated
   (correct, partly, incorrect, don't know). Beacon measures the time, not the browser. An answer has a length cap and, if the model takes more than 3 minutes, it stops by itself with a notice; you can also press "Cancel" while waiting.
5. **Wrap-up.** Experience, optional identity and review of the file. When it is generated, `beacon-estudio-resultados-<id>.json` (and `beacon-estudio-identidad-<id>.json`, if one was given) are left in the project's root folder; they can also be downloaded or copied. If the study has a submission destination configured, a **Send my results** button appears: it sends exactly the content shown on screen (and the identity only if the box is ticked) and shows a receipt code. If there is no destination, or sending fails, download the file and send it through the channel given in the invitation.

### Where the cloned repository ends up

The repository you choose is cloned into `data/estudio/` inside the Beacon folder (the assistant shows the exact path). It is deleted automatically when you discard the session ("Discard everything and exit", which also stops the server) or when you answer yes as you close `beacon study` with Ctrl+C. If you prefer to delete it by hand to recover space, that folder is the only one that takes up room. To see and clean up the space of other projects: `beacon space` and `beacon clean`.

### What the file contains and what it does not

It contains the questions, the answers, the times, the ratings, the chosen repository (address and
commit) and, if authorized, the machine's details. It does **not** contain source code, machine paths, user
name, machine name or IP. The identity (GitHub profile or introduction) goes in a separate file and
is optional. You can leave at any moment with "Discard everything and exit", which deletes the session.

`beacon study --new` discards a previous session and starts from scratch; `--port` changes the port and `--no-browser` prevents the browser from opening.

## For whoever receives the results

There are three routes, from the most convenient to the simplest. The participant chooses: if the first is not configured or fails, they use the others.

1. **"Send my results" button (recommended).** The assistant sends the file to a collector of your own, which stores each submission as a JSON file in a private Google Drive folder. Participants need no account or form. One-time setup:
   1. Go to [script.google.com](https://script.google.com), create a new project and paste the content of `tools/study_collector.gs`.
   2. Create a Drive folder for the results and copy its id (the last part of its address). In the project, *Project settings → Script properties*, add `FOLDER_ID` with that value.
   3. *Deploy → New deployment → Web app*: execute as **you**, access for **anyone**. Authorize Drive access and copy the address ending in `/exec` (opened in a browser it must answer `{"ok":true,"servicio":"beacon-estudio"}`).
   4. Put that address in `DEFAULT_SUBMIT_URL` in `src/core/study/submit.py` (so it travels with the repository and participants configure nothing). To try another without editing code: the `BEACON_STUDY_SUBMIT_URL` environment variable.
   The collector only accepts study files (schema 1, short id, consent and at least 3 questions, up to 2 MB), never overwrites an earlier submission and stores nothing else. Anyone who knows the address could send it data, but not read what was already received; files are validated when analysed.
2. **A form without login** (for example Google Forms or Tally) with a paragraph field where the participant pastes the content of the "Copy results" button, and another optional field for contact details. It exports to CSV.
3. **Through the invitation channel**: the participant downloads the file and sends it by hand.

Files are analysed locally, either from the downloaded Drive folder or from the CSV:

```bash
python tools/study_analysis.py folder_with_json/
python tools/study_analysis.py --csv responses.csv --columna "Resultados"
```

The script validates each file with the same rules as study mode, discards invalid and duplicate ones and
summarizes times by VRAM, percentage of correct answers and usefulness of the sources. Files named
"identidad" (identity) are never read.
