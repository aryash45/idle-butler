# Idle Butler

Idle Butler is a local Python CLI and localhost web UI that summarizes text files and proposes safe cleanup plans while your computer is idle. It uses a local Ollama model, writes a Markdown digest, and never sends file contents to a cloud service.

The tool uses an open-weight model through Ollama on `localhost`. Files stay on the machine. Idle mode only reads files and writes the digest, versioned state, and cleanup plan. Nothing is moved until you explicitly run `--apply`.

## Install

1. Install Ollama once from [ollama.com](https://ollama.com), then start it.
2. Run `python -m pip install .` from this project directory.
3. Download a model with `ollama pull llama3.2:3b`.

The recommended 3B model typically needs about 2 GB of disk space. A smaller `llama3.2:1b` model uses less space but may produce weaker summaries and cleanup suggestions.

## Quick start

Three-command quick start:

```text
python -m pip install .
ollama pull llama3.2:3b
python -m idle_butler run --folder ./sample/inbox --messy ./sample/messy --now
```

To prepare cleanup suggestions for a copy of a messy folder:

```text
python -m idle_butler run --folder ./sample/inbox --messy ./sample/messy --now
```

Review `cleanup-plan.json` and the `Proposed cleanup` table in `digest.md`, then apply only after checking the plan:

```text
python -m idle_butler apply --messy ./sample/messy
python -m idle_butler undo --messy ./sample/messy
```

`--apply` prints every planned move, asks for confirmation, moves only inside `--messy`, and records successful moves in `cleanup-log.json`. `--undo` restores logged moves when the original path is available. Missing files and existing targets are skipped.

Use `--model`, `--idle-minutes`, `--poll-seconds`, and `--out` as needed. `--apply` and `--undo` do not call Ollama or wait for idle time. The digest output must be outside both the watched folder and `--messy`.

Start the local UI with `python -m idle_butler ui`, or use the installed `idle-butler` command in place of `python -m idle_butler`.

## Workflows

The UI is the MVP workflow builder. Give a workflow a name, choose how long the computer must be idle, select its folders, and choose one or more built-in steps: summarize text, extract explicit action items, or propose cleanup suggestions. Idle Butler shows the workflow preview before starting and pauses when you return. Only the selected steps call the local model.

The workflow builder intentionally does not run arbitrary shell commands or Python code. This keeps the first version understandable, testable, and safe. Cleanup remains review-first and reversible.

## Cleanup safety

`--messy PATH` selects the non-recursive folder to propose cleanup for. Idle mode only suggests; nothing moves until you run `--apply`. Review `cleanup-plan.json` and the digest first. `--undo` restores moves recorded in `cleanup-log.json`. The tool never recursively scans the messy folder, never touches paths outside it, and refuses target collisions. Test with a copy of a folder first. Small models can misjudge a file, so review the plan.

```text
Before: ./sample/messy/biology notes final.txt
Plan:   ./sample/messy/Study/biology-notes-final.txt
After:  the move exists only after `python -m idle_butler apply --messy ./sample/messy`
Undo:   `python -m idle_butler undo --messy ./sample/messy` restores the original path
```

`--auto` is optional and moves only high-confidence, recent candidates while idle; it is intentionally conservative.

## Idle detection

- Windows uses `GetLastInputInfo` through `ctypes`.
- macOS uses `ioreg -c IOHIDSystem` and `HIDIdleTime`.
- Linux uses `xprintidle`. Wayland may not support it; install `xprintidle` where available or use `--now`.

When input resumes, Idle Butler finishes no file mid-request and stops between files. It prints a welcome-back line and silently attempts a native notification where supported.

## Safety

Idle mode only suggests. It never modifies, moves, renames, or deletes files automatically. `--apply` is the only mode that moves files, and it moves them only into subfolders inside `--messy`; `--undo` restores logged moves. Review the plan first because small models can misjudge filenames or categories. Test on a copy of a folder before using a real folder.

Cleanup suggestions include only a file name, extension, and the first 300 characters for text and Markdown files. Cleanup, plan, log, and summary state files are versioned JSON and are written atomically; missing or corrupt state starts fresh with a warning.

## Limitations

Small models can be wrong. Text and Markdown summaries use about 6000 characters, cleanup previews use 300 characters, and all folder scans are non-recursive. Action items are model-generated and limited to tasks or deadlines explicitly present in the source text. There is no cloud sync, PDF extraction, remote administration, or recursive cleanup.

## Local UI security

The UI binds only to `127.0.0.1`, generates a new random token on every start, checks the Host header, caps JSON POST bodies at 64 KiB, and exposes only the documented page and API routes.

## Model swapping

Pass another model with `--model NAME` after downloading it with Ollama. The UI model field uses the same local model name. Binary files are not sent to Ollama; cleanup planning receives only the filename, extension, and the first 300 characters of `.txt` or `.md` files.

Tested on Python 3.11 on Windows and Linux; macOS idle detection is parser-tested and should be verified on the target machine.

## Manual test checklist

- Run the quick start command with Ollama running and confirm `digest.md` contains summaries and an `Action items` section.
- Run with `--messy ./messy --now`, review `cleanup-plan.json`, and confirm no messy files moved.
- Run `--apply`, answer `y`, and confirm the move log; run `--undo` and confirm original paths return.
- Edit a watched file and rerun; confirm it is summarized again.
- Move the mouse during a normal idle run and confirm processing stops between files.
- Run `python -m unittest -v` to exercise pure logic and the apply/undo workflow without a model.

## Idle detection notes

If idle detection is unavailable, use `--now`. Expected failures print a readable message and exit without a traceback.


