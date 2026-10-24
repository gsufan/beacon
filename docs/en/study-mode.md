[Español](../es/estudio.md) | **English**

# Study mode

`beacon study` opens an assistant to take part in the Beacon study: you choose a public repository,
ask free-form questions and rate each answer. Everything happens on the participant's machine; the only
result is a file that the person reviews and sends on their own.

## For participants

Requirements: Python 3.10, 3.11 or 3.12 (3.13 or later does not work yet), Git, Node 20 or later (to build the interface once) and
[Ollama](https://ollama.com) with the models Beacon uses (`ollama pull llama3:8b` and
`ollama pull qwen3-embedding:0.6b`). A GPU speeds up the answers a lot, but it is not required.

```bash
git clone https://github.com/gsufan/beacon.git
cd beacon
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cd frontend && npm install && npm run build && cd ..
beacon study
```

The assistant opens in the browser (`http://127.0.0.1:8000/study`) and guides you through five steps:

1. **Participation.** Before you accept, the assistant checks that everything needed is in order: Ollama responds, both models are downloaded (if one is missing, it shows the exact `ollama pull`), there is a GPU and disk space, and there is a connection to clone. If something essential is missing, it does not let you start until you fix it and press "Retry". Participation is accepted explicitly; without that nothing is saved.
2. **Machine.** It shows exactly what would be saved (CPU, RAM, GPU and VRAM, operating system, Ollama
   version and models). It is optional and can be skipped.
3. **Repository.** An https address of a public repository (up to 3,000 code files).
4. **Questions.** At least 3, about things you really want to know about the code. Each answer is rated
   (correct, partly, incorrect, don't know). Beacon measures the time, not the browser. An answer has a length cap and, if the model takes more than 3 minutes, it stops by itself with a notice; you can also press "Cancel" while waiting.
5. **Wrap-up.** Experience, optional identity and review of the file. When it is generated, `beacon-estudio-resultados-<id>.json` (and `beacon-estudio-identidad-<id>.json`, if one was given) are left in the project's root folder; they can also be downloaded or copied.

### Where the cloned repository ends up

The repository you choose is cloned into `data/estudio/` inside the Beacon folder (the assistant shows the exact path). It is deleted automatically when you discard the session ("Discard everything and exit", which also stops the server) or when you answer yes as you close `beacon study` with Ctrl+C. If you prefer to delete it by hand to recover space, that folder is the only one that takes up room. To see and clean up the space of other projects: `beacon space` and `beacon clean`.

### What the file contains and what it does not

It contains the questions, the answers, the times, the ratings, the chosen repository (address and
commit) and, if authorized, the machine's details. It does **not** contain source code, machine paths, user
name, machine name or IP. The identity (GitHub profile or introduction) goes in a separate file and
is optional. You can leave at any moment with "Discard everything and exit", which deletes the session.

`beacon study --new` discards a previous session and starts from scratch; `--port` changes the port and `--no-browser` prevents the browser from opening.

## For whoever receives the results

No server of your own is needed. Recommended: a **form without login** (for example Google
Forms or Tally) with a paragraph field where each participant pastes the content of the "Copy
results" button, and another optional field for contact details. The form is exported to CSV and analysed locally:

```bash
python tools/study_analysis.py --csv responses.csv --columna "Resultados"
python tools/study_analysis.py folder_with_json/
```

The script validates each file with the same rules as study mode, discards invalid and duplicate ones and
summarizes times by VRAM, percentage of correct answers and usefulness of the sources. Files named
"identidad" (identity) are never read.
