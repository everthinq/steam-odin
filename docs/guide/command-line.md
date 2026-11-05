# The `odin` command-line tool

`odin` runs the app's commands from the terminal. Each command is one call to the
backend (http://127.0.0.1:5001), the same call a page's button makes, so the
terminal and the pages always agree. It only works while the app runs (`make raid`).

## Install once

```bash
make odin-command      # links ~/.local/bin/odin to scripts/odin
odin --help
```

It is a link, so a `git pull` updates it too. If `odin` is not found, add
`~/.local/bin` to your PATH. It needs only Python 3 (no packages).

## Commands

| Command | What it shows |
|---------|---------------|
| `odin status` | The same as the System health page: background loops, Ratatoskr, ASF, memory and the last errors. `--loops` lists every loop, `--errors N` shows more errors |
| `odin logs` | The end of the backend's log file. `-n 200` for more lines, `-f` to keep watching (Ctrl+C stops), `--errors` only errors, `--grep text` only lines with that text |
| `odin job <id>` | One backend job (a long task such as a move or a purchase): its progress, error and result. `--wait` follows it until it finishes |
| `odin routes [text]` | Every backend endpoint with one line on what it does; `odin routes ratatoskr` or `odin routes confirm` narrows the list |

Add `--json` to any command to see the backend's raw answer.

More commands (accounts and confirmations, Ratatoskr, prices and the portfolio)
come next. Purchases will only ever be dry runs from the terminal.
