# utrains - your local terminal operations agent

`utrains` is a command-line AI agent that runs **on your machine**. You tell it
what you want in plain English; it figures out the shell commands, shows them to
you, runs them once you approve, reads the output, and keeps going until the job
is done.

The "brain" is a **local model served by Ollama**, so your commands and data
never leave your computer. There is no API key and no cloud bill.

```text
you › list the docker containers that are using the most memory and stop the biggest one

[step 1] 🤔 First I'll see what containers are running and their memory use.
  $ docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"
  Run this command? [Y/n] y
  | web      512MiB / 2GiB
  | worker   1.8GiB / 2GiB
  (exit code 0)
[step 2] 🤔 'worker' uses the most; stopping it.
  $ docker stop worker
  Run this command? [Y/n] y
  | worker
  (exit code 0)
============================================================
Stopped the highest-memory container, 'worker' (1.8 GiB). 'web' is still running.
```

---

## What it can do

`utrains` drives your real shell (PowerShell on Windows, bash on Linux/macOS), so
it can use **any CLI you have installed**, including:

| Area        | Tools it knows about                              |
|-------------|---------------------------------------------------|
| System      | files, processes, services, networking            |
| Python      | `python`, `pip`, virtualenvs, running scripts     |
| Git/GitHub  | `git`, `gh` (repos, PRs, issues, releases)        |
| Containers  | `docker`, `docker-compose`, `kubectl`, `helm`     |
| Cloud       | `aws` (AWS CLI), `az` (Azure CLI), `gcloud` (GCP) |
| Infra       | `terraform`, `ansible`                            |
| Web         | `curl`, `wget`                                    |

It detects which of these are actually installed and only reaches for those.
Want it to manage AWS? Install and log into the AWS CLI. Want GitHub? Install
and authenticate `gh`. utrains uses whatever credentials those tools already have.

---

## Requirements

- **Python 3.10+**
- **~5–20 GB free disk** for the local model (depends on which one)
- **RAM** is what decides the model size (see the table further down)
- Internet access **once**, to download Ollama and pull the model

---

## Install - get the `utrains` command ready

### 1. Get the code
Copy/clone this `utrains-cli` folder onto the target computer, then open a
terminal **inside it**.

### 2. Install the command

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**Linux / macOS:**
```bash
bash install.sh
```

Either script installs the package with pip and creates the `utrains` command.

> Prefer to do it by hand? It's just:
> ```bash
> pip install --user .
> ```

### 3. Make sure `utrains` is on your PATH
- **Windows:** the command lives in your Python user `Scripts` folder. The
  installer prints the exact path - add it to your PATH if `utrains` isn't found.
- **Linux/macOS:** add `~/.local/bin` to your PATH if needed:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  ```

Verify it works:
```bash
utrains version
```

### 4. One-time setup (Ollama + a model)
```bash
utrains setup
```
This runs an **interactive** setup that:
1. Shows your machine's specs (OS, RAM, CPU, detected tools).
2. **Suggests models in a menu**, sized to your RAM - the best fit is marked
   `← recommended`, and any model too big for your RAM is flagged. Pick by number,
   press Enter to accept the recommendation, or type any Ollama model name:
   ```text
   Suggested models for this machine (based on your RAM):

     1. llama3.2:3b   ~2 GB    light & fast, basic reasoning
     2. llama3.1:8b   ~4.7 GB  great all-rounder
     3. qwen2.5:14b   ~9 GB    strong reasoning
     4. qwen2.5:32b   ~20 GB   best quality, needs the RAM  ← recommended
     5. other (type any Ollama model name)

   Pick a model [Enter = qwen2.5:32b]:
   ```
3. **Installs Ollama** if it isn't already (Linux: official script · Windows:
   winget · macOS: Homebrew - otherwise it points you to the download).
4. **Starts the Ollama server**.
5. **Pulls the chosen model** and remembers it in `~/.utrains/config.json`.

Skip the prompts entirely with `utrains setup --yes` (takes the recommendation),
or pre-pick with `utrains setup --model qwen2.5:14b`.

You're ready. 🎉

---

## How the model is chosen by your computer's capacity

`utrains setup` reads your total RAM and recommends:

| RAM        | Recommended model | Notes                          |
|------------|-------------------|--------------------------------|
| < 8 GB     | `llama3.2:3b`     | light, fast, modest reasoning  |
| 8–16 GB    | `llama3.1:8b`     | good all-rounder (default)     |
| 16–32 GB   | `qwen2.5:14b`     | stronger reasoning             |
| ≥ 32 GB    | `qwen2.5:32b`     | best quality, needs the RAM    |

Override anytime:
```bash
utrains setup --model qwen2.5:14b
```

---

## Usage

```bash
# Interactive session
utrains chat

# One-off task
utrains "create a python venv, install requests, and run app.py"

# Same thing, explicit
utrains run "show disk usage and clean up docker dangling images"

# Pick a model just for this run
utrains --model qwen2.5:14b "summarise the git log of the last week"
```

### Flags

| Flag            | What it does                                                        |
|-----------------|---------------------------------------------------------------------|
| `-y`, `--auto`  | Run normal commands without asking (dangerous ones still confirm).  |
| `--force`       | Also auto-run **dangerous** commands. Use with great care.          |
| `--dry-run`     | Show the commands the agent *would* run, but don't run them.        |
| `--model NAME`  | Use a specific model for this run.                                  |

### Using a cloud model (Claude or GPT) instead of local Ollama

utrains works with **three backends**, chosen automatically by the model name:

| Model name starts with | Backend | Needs |
|------------------------|---------|-------|
| `claude-…` | Anthropic Claude API | `ANTHROPIC_API_KEY` |
| `gpt-…` / `o3-…` | OpenAI GPT API | `OPENAI_API_KEY` |
| anything else (`qwen2.5:14b`, `llama3.2:3b`) | Local Ollama | nothing (offline) |

**1. Install the cloud SDKs** (only if you'll use cloud models):
```bash
pip install "utrains[cloud]"      # both, or [anthropic] / [openai]
```

**2. Add your key(s).** Copy `.env.example` to `.env` (or `~/.utrains/.env`) and fill in:
```ini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```
utrains loads `.env` automatically on startup. (Plain environment variables work too.)

**3. Pick the model:**
```bash
utrains --model claude-opus-4-8 "is docker the latest version?"
utrains chat
you › /model gpt-4.1
```
Cloud models need **no Ollama and no download** - much stronger reasoning than a local model, billed to your API account. Check `utrains doctor` to see which keys are detected.

### Switching model mid-conversation

Inside `utrains chat` you can change the model on the fly:

```text
you › /model                 # opens the same suggestion menu as setup
you › /model qwen2.5:14b      # switch straight to a named model (pulls it if missing)
you › /models                 # list the models you have locally
you › /help                   # quick tips and commands
```

The new choice is saved as your default for next time.

### Memory - what it remembers

By default utrains keeps **session memory** (so follow-ups like "now stop that
container" have context) and can hold **persistent notes** in
`~/.utrains/memory.md` that are fed into every run - handy for facts like
*"prod cluster is eks-east"* or *"default AWS profile is acme-prod"*.

One switch turns both off (cleaner, faster prompts on small models):

```bash
utrains memory                       # show state + saved notes
utrains memory add "prod is eks-east"
utrains memory on | off | clear
```
…or inside chat: `/memory`, `/memory add <note>`, `/memory off`, `/memory clear`.

### Credentials are automatic

utrains runs commands through your real shell, so each CLI uses **its own existing
login** - `aws` reads `~/.aws` + `AWS_PROFILE`, `kubectl` reads `~/.kube/config` +
`KUBECONFIG`, `gh` uses its stored token, `az` uses its login cache. You never
hand utrains any keys, and it's told not to ask. Want a specific AWS profile or
cluster? Set the env var before launching:

```bash
# Windows PowerShell
$env:AWS_PROFILE = "prod"; utrains "list my running EC2 instances"
# Linux/macOS
AWS_PROFILE=prod KUBECONFIG=~/.kube/prod utrains "how many pods are not Ready?"
```

### MCP - extra tools from Model Context Protocol servers

Beyond the shell, utrains can use tools from **MCP servers**. Create
`~/.utrains/mcp.json`:

```json
{
  "servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

`${GITHUB_TOKEN}` is filled from your environment. On launch utrains starts each
server, lists its tools (exposed to the agent as `server.tool`, e.g.
`github.create_issue`), and the agent can call them - with the same per-action
approval as shell commands. Inspect what's available:

```bash
utrains mcp        # list configured servers and their tools
```
…or `/mcp` inside chat. No `mcp.json` → MCP is simply off; nothing changes.

> Note: for anything that already has a CLI (AWS, kubectl, GitHub via `gh`), the
> shell is usually simpler and more reliable. MCP shines for services without a CLI.

### Looks

The chat UI uses a calm **dark theme** (muted teal/violet/mint, never harsh) and
shows a **live purple spinner** with rotating, playful "thinking" lines while the
model works. Confirm prompts are a **vertical numbered menu** with option 1
(the safe default) highlighted - just press Enter to take it.

It also has a bit of personality for Utrains students: random greetings, tips,
and the occasional celebratory tag. Want it all-business?

```bash
setx UTRAINS_SERIOUS 1     # Windows: mute the jokes (new terminals)
export UTRAINS_SERIOUS=1   # Linux/macOS
```

Colours auto-disable when output isn't a terminal (or set `NO_COLOR=1`).

### Other commands

```bash
utrains doctor    # machine info + Ollama/model/memory/MCP health
utrains models    # list the models you've pulled
utrains memory    # manage what it remembers
utrains mcp       # list MCP servers and tools
utrains version
utrains help
```

---

## Safety

`utrains` is built to be careful, but it runs **real commands on your machine**:

- **Every command is shown and requires your approval** before running.
- **Destructive commands** (deleting data, formatting disks, `git push --force`,
  dropping databases, shutdown, …) are flagged and **always** ask for
  confirmation - even in `--auto` mode - unless you pass `--force`.
- The agent is instructed to gather facts with read-only commands first, to stop
  and ask when unsure, and never to print full secrets.
- `--dry-run` lets you preview a plan with zero risk.

You are always the final approver. Read each command before you say yes.

---

## How it works (under the hood)

```
your words → Ollama (local model) → one shell command (JSON)
                ▲                              │
                │                              ▼
          observation  ◄── run it (you approve) ── PowerShell/bash
```

The agent loops: the model proposes **one** command in a small JSON format,
utrains shows it, you approve, it runs through your native shell, the output is
fed back, and the model decides the next step - until it reports it's done.

### Project layout

| File                    | Role                                                    |
|-------------------------|---------------------------------------------------------|
| `utrains/cli.py`        | the `utrains` command, flags, and screen output         |
| `utrains/agent.py`      | the think→action→run→observe reasoning loop             |
| `utrains/executor.py`   | runs shell commands; flags dangerous ones               |
| `utrains/providers.py`  | routes to Ollama / Claude / GPT by model name           |
| `utrains/ollama_client.py` | talks to the local Ollama server                     |
| `utrains/installer.py`  | `utrains setup`: install Ollama, start it, pick+pull a model |
| `utrains/system_info.py`| detects OS, RAM, CPU, installed CLIs; the model catalog  |
| `utrains/prompts.py`    | the agent's instructions and JSON contract              |
| `utrains/memory.py`     | session + persistent memory (`~/.utrains/memory.md`)    |
| `utrains/mcp_client.py` | optional MCP servers (`~/.utrains/mcp.json`)            |
| `utrains/ui.py`         | dark colour theme and the live spinner                  |
| `utrains/config.py`     | remembers your model/settings in `~/.utrains/config.json` |

---

## Troubleshooting

- **`utrains: command not found`** → the Scripts/bin dir isn't on PATH. See
  install step 3, or run `python -m utrains ...`.
- **`Ollama server isn't reachable`** → run `ollama serve` in a terminal, or
  re-run `utrains setup`.
- **`Model '...' isn't pulled yet`** → `ollama pull <model>` or `utrains setup`.
- **Model is slow** → pick a smaller one: `utrains setup --model llama3.2:3b`.
- **Cloud commands fail with auth errors** → log into that CLI first
  (`aws configure`, `az login`, `gh auth login`). utrains uses their credentials.

---

## Uninstall

```bash
pip uninstall utrains
```
Remove settings with `rm -rf ~/.utrains` (or delete `%USERPROFILE%\.utrains` on
Windows). Remove Ollama and its models separately if you no longer want them.

---

MIT licensed. Built to be read - open any file in `utrains/` and it explains itself.