[Español](../es/calidad-medida.md) | **English**

# Measured quality

Measurements of search, answers and load. The tools are in `tools/` and the question sets in `eval/`. The question sets and the model's answers are in Spanish, so the Spanish wording of the questions is kept where it appears.

## Search quality (measured)

`tools/eval_retrieval.py` measures retrieval against sets of reference
questions, written in Spanish and each one with the function that answers it:

- `eval/requests.yaml`: 30 questions about psf/requests (Python).
- `eval/microservices-demo.yaml`: 22 questions about
  GoogleCloudPlatform/microservices-demo, 11 services in Go, C#,
  JavaScript, Python and Java.
- `eval/requests-identificadores.yaml` and
  `eval/microservices-demo-identificadores.yaml`: 15 and 10 questions that
  name an identifier ("¿para qué sirve `super_len`?", "¿dónde se usa
  `max_redirects`?" — "what is `super_len` for?", "where is `max_redirects`
  used?"), as asked by someone who has already seen the name in an error or a
  log.

```bash
python tools/eval_retrieval.py <project_id> eval/requests.yaml
```

If an expected function does not exist in the index, the tool stops before
measuring, so that a mistake in the set is not counted as a search failure.

| Configuration | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---|---|---|---|
| nomic-embed-text (previous version) | 7% | 33% | 40% | 0.19 |
| qwen3-embedding:0.6b + test files penalized | 70% | 93% | 100% | 0.80 |
| + file path in the embedded text, without `@overload` signatures (current) | 73% | 93% | 100% | 0.83 |

With the adaptive context, the fragment that answers the question reaches the
model in 97% of cases (8 fragments on average).

Results per repository with the current configuration:

| Repository | Questions | Hit@1 | Hit@5 | Hit@10 | MRR@10 | In context |
|---|---|---|---|---|---|---|
| psf/requests (Python) | 30 | 73% | 93% | 100% | 0.83 | 97% |
| microservices-demo (5 languages) | 22 | 41% | 95% | 95% | 0.63 | 95% |
| psf/requests, with identifiers | 15 | 67% | 93% | 100% | 0.79 | 100% |
| microservices-demo, with identifiers | 10 | 100% | 100% | 100% | 1.00 | 100% |

In the polyglot repository, the biggest improvement came from embedding each
fragment together with the path of its file (Hit@5 from 77% to 95%): the code
alone does not say which service it belongs to, and the load generator
(`locustfile.py`), which repeats the names of the shop's operations, beat the
service that implements them. What is handed to the model is still only the code.

When the question names an identifier, the search is hybrid: besides semantic
similarity, the fragments that are named that way or that use it rank higher
(if the question is "where is … used?", whatever uses it weighs more than the
definition). In the identifier questions for psf/requests, Hit@5 went from 80%
to 93% and the correct fragment reaches the model in 100% of cases (before
87%); the other sets did not change.

The sets are small, so the numbers are useful for comparing configurations,
not as a general guarantee.

If you change `embedding_model` in `config.yaml`, the next `beacon sync`
rebuilds the index by itself (vectors from different models are not
comparable), and in the meantime queries answer with a clear notice.

## Answer quality (measured)

`tools/eval_answers.py` calls the real model and checks each answer with
verifiable criteria, with no human judgment: whether it mentions the key
facts of the question, whether it cites the file where the answer is, whether,
faced with a question about something that does not exist in the repository,
it says there is no information (without adding filler code), whether it writes
identifiers that do not appear in the context provided (possible invention)
and whether it answers in Spanish.

```bash
python tools/eval_answers.py <project_id> eval/answers-requests.yaml
```

On psf/requests (12 answerable questions and 4 that are not), with
llama3:8b:

| Configuration | Content | Cites the file | Both | False refusal | Correct refusal |
|---|---|---|---|---|---|
| Previous prompt | 75% | 33% | 33% | 8% | 100% |
| Adjusted citation and refusal rules | 75% | 92% | 67% | 0% | 100% |
| + fragments labeled by path and false-premise rule | 100% | 83-92% | 83-92% | 0% | 100% |
| + hybrid search (current configuration) | 92% | 83% | 75% | 0% | 100% |

What each measurement showed:

- The model cited "fragment 3" instead of the file (the user does not see that
  numbering) and sometimes closed a correct answer with the "I found no
  information" phrase. Asking for something else in the prompt helped, but
  removing the number solved it: each fragment now arrives labeled with its
  path and lines, which is what the model copies when citing.
- Faced with a question with a false premise ("what machine-learning model
  does requests use to predict the encoding?") the model accepted it and
  filled in with what it knew about an external library. An explicit rule for
  that case fixed it.
- With the default temperature, the same question gave different results
  between runs (the correct content varied between 67% and 92%), which made it
  impossible to compare changes. Answers are generated with temperature 0.2 and
  a fixed seed; even so, between two identical runs an answer can change, hence
  the range in the table. With 12 questions, each one is worth 8 points:
  differences of one question between configurations (such as the last row)
  are within that variation.

As a control, the set `eval/answers-microservices-demo.yaml` was written after
these adjustments and was not used for any of them. There the results are
lower: content 80%, citation 60%, both 60%, no undue refusals and 100% correct
refusals (with the current configuration). The answers get the main point
right but are short and often do not cite the file; this is a limit of the
8B-parameter model that is worth knowing (the interface still shows the
sources of each answer). An answer takes about 5 seconds with an 8 GB GPU.

## Stress tests (measured)

`tools/stress_test.py` generates a synthetic repository of thousands of files
and measures indexing, search, simultaneous queries against the real server
and queries while another console rebuilds the index:

```bash
python tools/stress_test.py generar ../stress-repo --archivos 2000
python tools/stress_test.py medir ../stress-repo --concurrencia 1 4 8
```

With 2,000 files in Python, Go and JavaScript, 8 GB GPU:

| Measurement | Result |
|---|---|
| Full indexing (18,012 fragments) | 6 min (50 fragments/s), 260 MB of memory, 122 MB on disk |
| Sync after changing 20 files | 5.6 s |
| Search without LLM (p95) | 35 ms |
| Simultaneous full queries: 1 / 4 / 8 | median 3.2 / 6.4 / 12.1 s; maximum 24.9 s; no errors |
| Queries during a full rebuild | 128 of 128 answered |

With several simultaneous queries the wait grows almost proportionally: Ollama
generates the answers one at a time on the GPU. The first version of this test
found two faults, both already fixed: queries with a 500 error while another
console ran `sync --full`, and half-deleted projects on Windows when the
server had the index open. The full report of the last run is kept in
`eval/stress_report.json`.
