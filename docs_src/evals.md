# `evals/` — measuring quality instead of guessing

**Role:** you cannot improve what you cannot measure. The eval harness gives each model a fixed set of small coding
tasks in clean temporary workspaces and checks the *result* automatically (does the code actually behave correctly?).
Use it to compare models (`--model qwen3:8b` vs `qwen2.5-coder:7b`), to compare settings (`--type-check`), and to catch
regressions after changing the prompt or tools. It reports pass/fail, model calls, tokens and time per task.

Run: `python -m evals.run_evals --model qwen3:8b` (needs a running model server).

## `evals/__init__.py`
@@ evals/__init__.py 1-1 @@
- Marks the folder as a package (so `python -m evals.run_evals` works). The line is a docstring.

## `evals/tasks.py`
@@ evals/tasks.py header @@
- Imports `shutil` (find Node/Go), `subprocess`/`sys` (to run checks in separate processes), `dataclass`/`field`,
  `Path`, `Callable`, and `walk_files` (list a workspace without caches).

@@ evals/tasks.py python_ok @@
- Runs a snippet of Python with the task's workspace as the working folder, so `import fizzbuzz` finds the agent's
  file. Returns `True` when the snippet exits with code 0 (all its `assert`s held). 60 s timeout.

@@ evals/tasks.py tests_pass @@
- Runs `unittest discover` in the workspace; success means the agent fixed the failing tests.

@@ evals/tasks.py Task @@
- One evaluation: a `name`, the user `prompt`, a `check` function (workspace → bool), optional `seed` files created
  before the model starts, and an optional `answer_check` that judges the **reply text** — needed for questions, where
  the right outcome is an answer, not a file. `field(default_factory=dict)` gives each task its own empty dict.

@@ evals/tasks.py after_Task @@
@@ evals/tasks.py node_ok @@
@@ evals/tasks.py after_node_ok @@
- The first eleven tasks:
  - `FIZZBUZZ`: create a module from scratch; check imports it and asserts the 1st, 3rd, 5th and 15th results.
  - `FIX_BUG`: `calc.py` divides by `len+1`; a seeded unittest fails; the check is that tests now pass.
  - `ADD_METHOD`: extend a seeded `Stack` class with `peek()`; check covers normal use and the `IndexError` on empty.
  - `WORD_COUNT`: write a script; check runs it on a seeded file with five words and expects exactly `5`.
  - `COUNTER_CLASS`: create a class from a spec (`increment`, `decrement`, read-only `value`); check exercises it.
  - `RENAME_FUNCTION`: a two-file project; the name `calc_total` must vanish from both files, the new name must work and
    `main.py` must still print `6` - tests that the agent edits *every* place.
  - `JSON_KEY`: write a script that reads a seeded `config.json` and prints only the `name` value (`orion`).
  - `DEFAULT_ARG`: a one-word change in an existing file ("change nothing else"); the check also proves the other
    behaviour is intact - guards against over-editing.
  - `ADD_TESTS`: write unittest tests for a seeded module; needs the file, at least two `def test_`, and passing tests.
  - `PACKAGE_FEATURE`: a small **multi-file package** (`inventory/` + `tests/`); add `Store.remove_item` with a
    `KeyError` for unknown names and a test for both cases; all tests must pass and the behaviour is checked directly.
  - `JS_SLUGIFY`: a **JavaScript** task, checked by running Node.js (`node_ok`). Either export style
    (`module.exports = slugify` or `{ slugify }`) is accepted, because the request allows both readings - the first
    version of this check accepted only one and failed a correct solution.
- `node_ok` runs a JavaScript snippet with Node in the workspace (False when Node is missing).

@@ evals/tasks.py only_seed_files @@
- Lists the workspace with `walk_files` (so caches like `.ruff_cache` do not count) and compares it with the seed: a
  question must not create or delete files.

@@ evals/tasks.py go_prints @@
- Runs `go run main.go` in the workspace (two-minute limit; the first Go build is slow) and compares the output.

@@ evals/tasks.py ts_ok @@
- Runs a JavaScript **module** snippet with Node's type stripping on, so it can `await import('./x.ts')` and test a
  TypeScript file directly.

@@ evals/tasks.py footer @@
- The four newest tasks, measuring the two things a coding agent must also do well:
  - `QUESTION_GENERAL`: a general-knowledge question (what HTTP 404 means). Passes if the reply says the resource was
    not found — in any of the usual wordings (`NOT_FOUND_PHRASES`) — and no file was created. The first version
    accepted only the words "not found" and failed the correct answer "the server cannot find the requested resource".
  - `QUESTION_PROJECT`: a question about a seeded mini shop project; the reply must name `compute_total` and
    `pricing.py`, and the project must be unchanged.
  - `GO_ADD`: write a Go program; `go run` must print `5`.
  - `TS_TEMPERATURE`: write a TypeScript module; importing it must give `cToF(100) == 212` and `cToF(0) == 32`.
- `TASKS` is the list the runner iterates over (15 tasks). Add new tasks here; seed file names may include folders.

## `evals/run_evals.py`
@@ evals/run_evals.py header @@
- Imports the agent pieces (agent, backend registry, the shared flags, `choose_mode` and `model_label` from `cli.py`,
  config with its shared rules, `LLMError`, meter, toolbox), `os` (is `LOCALAGENT_BACKEND` set?) and the task
  definitions.

@@ evals/run_evals.py Result @@
- Immutable scoreboard row: name, pass/fail, model calls, total tokens, seconds, and a note for errors.

@@ evals/run_evals.py run_task @@
- Creates a temporary workspace (deleted afterwards), writes the seed files (creating their folders), and builds a config pointing at it with
  `auto_approve=True` (unattended). Builds a fresh `Agent` with a fresh meter, runs the prompt, and records any
  `LLMError` as a note. The toolbox is **closed in a `finally` block** before the temporary folder is deleted, so a Docker sandbox container
  is always removed. With `trace=True` each tool call and its first result line are printed live (`on_event`). The task's `check` judges the final workspace and, when present,
  `answer_check` judges the reply text (questions); a model error counts as a fail. The backend is a parameter so
  tests can inject a fake.

@@ evals/run_evals.py main @@
- Parses the shared flags (`add_model_flags`), repeated `--only NAME` filters, `--type-check` and `--trace` (print every
  tool call); overlays them on the environment config, applies the mode like the chat (`--offline` measures the local
  model, `--online` Gemini alone) unless `--backend` was given, and applies `gemini_history`, so the evals measure
  exactly the setup the chat uses; the summary names the model chain via `model_label`; builds the backend; runs each selected task and prints one line each, then a summary. Exit code is
  0 only if every task passed, so scripts can gate on it.

@@ evals/run_evals.py footer @@
- Standard script entry point.
