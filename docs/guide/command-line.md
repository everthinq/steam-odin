# The `odin` command-line tool

`odin` runs every app command from the terminal, no browser needed. The app's
backend does all the work, and each page button is one backend command; `odin`
sends the same commands. So the terminal and the pages always agree, and a new
feature is an `odin` command as soon as the backend has it. The app must be
running (`make raid`).

## Install once

```bash
make odin-command      # links ~/.local/bin/odin to scripts/odin
odin help
```

It is a link, so a `git pull` updates it too. If `odin` is not found, add
`~/.local/bin` to your PATH. It needs only Python 3 (no packages).

## How a command is written

```
odin <command words> <arguments> name=value name=value …
```

- **Command words** name the tool and the action: `odin ratatoskr storage`,
  `odin huginn gjallarhorn reminders`. `odin help` lists all of them;
  `odin help ratatoskr` the Ratatoskr ones; `odin help ratatoskr deposit plan`
  explains one command and its fields.
- **Arguments** follow in order, for example an account: `odin ratatoskr storage everthinklol`.
  An account can be given by its account name or its SteamID64.
- **Fields** are `name=value`. `true`, `false`, numbers, `[lists]` are understood;
  `name=@file.txt` reads the value from a file (use that for passwords, so they
  stay out of the shell history).
- `--wait` follows a long task (a job) until it ends; `--json` prints the raw answer.

## Everyday examples

| What | Command |
|------|---------|
| Accounts and their Steam Guard codes | `odin accounts` |
| Pending confirmations of one account | `odin accounts confirmations everthinklol` |
| Accept one | `odin accounts confirmations answer everthinklol <id> op=allow` |
| Check every account now | `odin confirmations check-all --wait` |
| Free places in the Storage Units | `odin ratatoskr storage everthinklol` |
| Store everything storable: plan, then run it | `odin ratatoskr deposit plan steamid=everthinklol all_storable=true`, then `odin ratatoskr plans execute <plan id> --wait` |
| Resell reminders | `odin huginn gjallarhorn reminders` |
| Portfolios | `odin draupnir portfolios` |
| How the app is doing | `odin status` (`--loops` for every loop) |
| The log file | `odin logs` (`-f` keep watching, `--errors`, `--grep text`, `-n 200`) |
| One task's progress | `odin jobs <id> --wait` |

## Safety

- **Purchases run only as dry runs.** Buy Storage Units, Andvari's Buy games and the
  Market unlocker show what they would buy; buying for real is done on the page.
- **Secrets stay on your screen.** Answers with passwords, tokens or emails (settings,
  Mímir) are printed only in a real terminal, never into a file or another program.
