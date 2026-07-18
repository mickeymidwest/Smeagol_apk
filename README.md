# Smeagol — core orchestrator

Smeagol runs your local GGUF model (Dolphin3.0-Llama3.2-3B) and any API
models side by side. Models can work solo, as a broadcast group, as a
plan-then-build multi-agent team, and now: they can propose changes to
Smeagol's own source code, which get validated and applied automatically.

## Setup on Manjaro

**Starting from nothing on a fresh machine** (nothing cloned yet):
```bash
curl -O https://raw.githubusercontent.com/<you>/<repo>/main/bootstrap.sh
bash bootstrap.sh https://github.com/<you>/<repo>.git
```
Clones the repo, then runs everything below automatically. If you've
already cloned it yourself, just `bash bootstrap.sh` from inside (or
above) that folder does the same thing without needing the URL.

**If you're already inside a cloned/extracted copy**, skip straight to:
```bash
cd smeagol
./setup.sh
```
Creates the venv, installs dependencies, detects whether this specific
machine has an NVIDIA GPU and installs the matching CUDA wheel
automatically (falls back to CPU-only cleanly if there's no GPU, or if
no prebuilt wheel matches your driver's CUDA version), and prompts for
your API keys -- but only the ones that aren't already set in `.env`.

That last part matters for exactly the laptop-then-desktop workflow:
run it on your laptop, enter your keys once, then copy the whole
project folder over to the desktop (skip `venv/` -- see below) and run
`./setup.sh` again there. It'll skip re-creating the venv, skip
re-asking for keys that already made it into `.env`, and this time
detect the desktop's actual GPU and install the CUDA-accelerated wheel
instead of CPU-only. Same script, correct behavior on each machine.

I tested every branch of this for real rather than just writing it and
hoping: ran it in an environment with genuinely no GPU (confirmed the
CPU fallback path), faked a `2070-Super`-style `nvidia-smi` output to
confirm the CUDA-version-to-wheel-tag parsing is exactly right
(`CUDA Version: 12.4` → `cu124`), confirmed a real failed wheel match
correctly falls back to CPU instead of just crashing, confirmed a
successful GPU match correctly skips the redundant CPU install, and
confirmed re-running the whole script a second time skips the venv and
leaves already-set keys untouched. The one thing I couldn't test here:
an actual successful CUDA wheel *installation* against a real GPU,
since this sandbox has none -- that part you'll be the first to
actually confirm.

### Or the manual steps, if you'd rather see each one

```bash
sudo pacman -S python python-pip base-devel cmake git
cd smeagol
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

GPU acceleration for local inference:
```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install --force-reinstall --no-cache-dir llama-cpp-python
```
(AMD: `-DGGML_HIPBLAS=on` with ROCm. CPU-only: skip this.)

Edit `config/models.yaml` and point `model_path` at your actual `.gguf` file
-- or skip that entirely and use `smeagol models` below to add them.

**API keys**: instead of `export`ing them every terminal session, copy
`.env.example` to `.env` and fill in your real keys -- it's loaded
automatically (via `python-dotenv`, optional: falls back to your shell
environment if not installed). `.env` is listed in `.gitignore`, which
matters more than it might look: self-improve's very first run does a
baseline `git init && git add -A` commit, and without that `.gitignore`
entry a real `.env` with actual secrets would get captured permanently
into git history the first time you ever run `smeagol improve` or
`auto-fix`. I caught this by testing the exact scenario (a real-looking
`.env` present, then triggering that baseline commit) after adding the
`.env` feature, not by assuming it was fine -- fixed before it shipped.

## The `smeagol` command

```bash
chmod +x smeagol
sudo ln -s "$(pwd)/smeagol" /usr/local/bin/smeagol
```

After that, run everything as `smeagol <command>` from anywhere instead
of `python main.py <command>` from inside the project folder. It
automatically uses this project's venv if one exists at `smeagol/venv`.

## Adding local models by scanning a folder

```bash
smeagol models                  # scans ~/Downloads by default
smeagol models ~/models         # or point it at any folder
```

```
Found 3 .gguf file(s) in ~/Downloads:

  1. Mistral-7B-Instruct-v0.3-Q4_K_M.gguf  (4.1GB)
  2. dolphin-2.9-llama3-8b.Q5_K_M.gguf  (5.7GB)  [already added]
  3. phi-3-mini-4k.Q8_0.gguf  (4.0GB)

Add which ones? (comma-separated numbers, 'all', or blank to cancel): 1,3
```

It searches recursively, skips anything already registered, auto-names
each one from its filename, and writes new entries straight into
`config/models.yaml` -- without touching or reformatting any of the
existing entries or comments in that file. Double-check `chat_format`
for whatever it adds (defaults to `chatml`; change it if a model uses a
different template), and it's ready: `smeagol chat <name>`.

Every model added this way (or via Hugging Face below) is also
automatically appended to `persona.consult_models` -- otherwise it
would just sit registered and unused, since Smeagol only ever reaches
for a model that's actually in his consult group. This applies whether
you scan a folder or download from Hugging Face; either path wires the
new model in the same way. Verified by actually building the registry
afterward and confirming the new model shows up in
`smeagol.consult_model_names`, not just by checking the raw YAML text.

## Adding models directly from Hugging Face

```bash
smeagol models --hf "dolphin llama 3b"
```

```
Searching Hugging Face for: dolphin llama 3b

  1. cognitivecomputations/dolphin-2.9-llama3-8b-gguf  (185000 downloads, 340 likes)
  2. bartowski/dolphin-2.9.4-llama3.1-8b-GGUF  (42000 downloads, 88 likes)

Which repo? (number, or blank to cancel): 1

Fetching file list for cognitivecomputations/dolphin-2.9-llama3-8b-gguf...

  1. dolphin-2.9-llama3-8b-q4_k_m.gguf  (4.9GB)
  2. dolphin-2.9-llama3-8b-q8_0.gguf  (8.5GB)

Which file (quantization)? (number, or blank to cancel): 1

Downloading to models/dolphin-2.9-llama3-8b-q4_k_m.gguf ...
Downloaded and added as 'dolphin-2-9-llama3-8b-q4-k-m'.
```

No browser, no manual download-then-scan round trip -- search, pick a
repo, pick a quantization, and it's downloaded straight into this
project's `models/` folder and registered in `config/models.yaml` in
one go. Uses Hugging Face's public, unauthenticated JSON API.

**A bug I found and fixed while building this:** testing the full
search-to-registration flow end to end (not just the search/download
parts in isolation) turned up a real, pre-existing break in
`model_scan.py` -- `insert_entries`, the function that actually writes
new model entries into `config/models.yaml`, had lost its own `def`
line somewhere in an earlier edit, leaving its body as dead code
nobody was calling. That meant the *original* local-folder `smeagol
models` scan (not just this new Hugging Face feature) had been quietly
broken in every zip shipped since whatever edit caused it -- it would
find files and let you pick them, then crash instead of actually
registering anything. Fixed now, and I re-verified the local-folder
scan specifically (not just the new Hugging Face path) actually
completes and registers a model end to end, since that's the feature
that was silently broken.

## Removing models

```bash
smeagol remove
```

```
Registered models:

  1. dolphin-3b (local_gguf)  [used by smeagol: consult_models]
  2. local-2 (local_gguf)  [used by smeagol: consult_models]
  3. mistral-7b-instruct-v0-3-q4-k-m (local_gguf)
  4. claude (anthropic)  [used by smeagol: primary_model]
  5. gemini (gemini)  [used by smeagol: fallback_models, consult_models]

Remove which one(s)? (comma-separated numbers, or blank to cancel): 3
```

Same text-surgery approach as adding: only the picked entry's block is
touched, nothing else in the file is reformatted. Two safety behaviors
worth knowing about:

- If a model is still referenced by smeagol's `fallback_models` or
  `consult_models`, you'll be warned before removing it -- confirm and
  it's removed **and** automatically scrubbed out of those lists too,
  so nothing dangles.
- Removing smeagol's `primary_model` is refused outright, always --
  there's no safe automatic substitute for "the model smeagol answers
  with by default", so that has to be a deliberate edit to
  `config/models.yaml`, not something `remove` does for you.

## Smeagol as the main interface

`smeagol chat smeagol` is meant to be the only command you
actually need day to day. Behind that one interface:

1. Smeagol answers on its own (via `primary_model`) whenever it can --
   in its own voice, since `system_prompt` (if you set one) applies here.
2. If its own answer looks uncertain (heuristic check on phrases like
   "I don't know" / "I'm not sure" / an empty answer), it automatically
   consults `consult_models` in parallel -- your local models by
   default. Local models only load at that moment, not before, and if
   more than one is needed they run in parallel rather than queuing.
   An answer only counts if it's *itself* confident -- a consulted
   model saying "I'm not sure either" doesn't count as a contribution.
3. If NOTHING in `consult_models` came back confident, `last_resort_model`
   (`gemini` by default) gets one dedicated final check -- not just
   another name in the same list, and not touched at all if a local
   model already had the answer.
4. Whatever confident material was found gets handed back to **Smeagol
   itself** to produce the actual reply. Other models only ever supply
   raw research during a consult -- they never speak to you directly.
   The final answer is always generated by calling `smeagol` again,
   which means it always carries Smeagol's own `system_prompt` if
   you've set one, same as a direct answer would.
5. Whatever it learns from a consult gets written to
   `data/learning_log.jsonl`. The exact same question asked again is
   answered from that log with zero model calls -- it remembers.

You'll see a short note under Smeagol's answer when this happens:
`(wasn't sure on its own -- consulted: dolphin-3b)`,
`(wasn't sure on its own -- last-resort check: gemini)`, or
`(answered from something learned earlier -- no model call needed)`.

**On "editing its own code" when it learns something:** this
deliberately does NOT happen automatically. Automating the *answer*
side (consult other models, remember the result) is safe -- worst case
you get a synthesized answer. Automating the *code-edit* side based on
whatever a user happened to type is a different kind of risk: it would
mean any message, including a malicious or just careless one, could
cause Smeagol to rewrite its own source with no review. So a consult
never triggers `apply_patch` on its own. If something in
`data/learning_log.jsonl` looks like it points at a real gap worth
fixing in code, that's a call for you to make with the existing
reviewed flow:

```bash
python main.py improve dolphin-3b,claude,gemini "teach smeagol to handle X" --apply
```

## Smeagol is its own identity

`smeagol` is a persona layer, not just another entry in the model list.
Talking to it (`python main.py chat smeagol`) always gets the same name,
personality, and system prompt back -- regardless of which backend
model actually generates the reply.

```yaml
persona:
  name: smeagol
  primary_model: dolphin-3b        # <-- swap this any time, no other change needed
  fallback_models: [claude, gemini]
  system_prompt: |
    You are Smeagol, a personal AI running locally on the user's own machine...
```

- Change `primary_model` and every future conversation with "smeagol" is
  instantly backed by a different engine.
- If the primary errors out (local model crashed, API down, rate
  limited), it automatically fails over to each fallback in order --
  Smeagol staying available doesn't depend on any single backend
  staying up.
- `dolphin-3b`, `claude`, and `gemini` are still directly addressable by
  name for `broadcast`/`plan`/`improve` when you want to reach a
  specific model rather than "whoever's currently answering as Smeagol."

```bash
python main.py chat smeagol
```

## Adding Claude and Gemini

Both are already set up in `config/models.yaml` as `claude` and `gemini` --
just set the two environment variables (add these to your `~/.bashrc` or
`~/.zshrc` so they persist):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
```

Get an Anthropic key at https://console.anthropic.com and a Gemini key at
https://aistudio.google.com/apikey. Once both are set:

```bash
python main.py list
# dolphin-3b (local)
# claude (api)
# gemini (api)

python main.py broadcast dolphin-3b,claude,gemini "what's the best way to do X"
python main.py plan dolphin-3b,claude,gemini "build a script that does X"
python main.py improve dolphin-3b,claude,gemini "add a retry with backoff"
```

All three now work identically everywhere in Smeagol -- broadcast, plan,
and self-improve all treat local and API models the same way.

## Using it

```bash
python main.py list
python main.py chat dolphin-3b
python main.py broadcast dolphin-3b,claude-sonnet "explain X"
python main.py plan dolphin-3b,claude-sonnet "build a script that does X"
```

## Self-improvement

This is the "help it build itself more" part -- with a hard rule: **a
self-edit only ever lands after two different models independently
approve the exact same patch.** By default that's Claude, then Gemini.
If either rejects it, their specific feedback goes to a fixer model,
the patch gets revised, and review restarts from the first reviewer --
so the version that finally lands is the version both of them actually
saw and approved, not an earlier draft one of them missed.

```bash
# dry run: see what one or more models would change, nothing is applied,
# no review happens yet -- review is only meaningful once you intend to apply
python main.py improve dolphin-3b "add a retry with backoff to API backends"

# apply: proposes, then runs it through claude -> gemini review.
# Only commits if both approve (after however many fix-and-retry rounds
# it takes, up to a max before giving up).
python main.py improve dolphin-3b,claude,gemini "add a retry with backoff" --apply

# use a local code model as the first reviewer instead of Claude
python main.py improve dolphin-3b "add a retry with backoff" --apply --reviewer-a=dolphin-3b

# also run pytest before committing, on top of the review gate
python main.py improve dolphin-3b,claude,gemini "add a retry with backoff" --apply --test
```

You'll see each reviewer's verdict printed as it happens:
```
=== Review gate: claude then gemini must both approve ===
  [claude] REQUESTED CHANGES -- doesn't handle the timeout case
  [claude] APPROVED
  [gemini] APPROVED

Both reviewers approved after 2 round(s). Applying...
```

If review never converges (a reviewer keeps rejecting past the round
limit, or is straight up unreachable), nothing is applied or committed
-- an ambiguous or failed review is treated as **not approved**, never
as a pass-through.

`--test` is opt-in on top of all this: a full pytest run adds time and
needs a test suite to mean anything, so it's off by default. If pytest
itself isn't installed, `--test` fails safely with a clear message
instead of a confusing traceback, and the patch is reverted rather than
left half-applied.

How it stays safe:
- Every model sees Smeagol's actual current source and proposes a real
  unified diff, not vague suggestions.
- If more than one model proposes, each works independently and a
  designated model merges them into one final diff -- the "multiple
  models make it better together" behavior.
- **The patch must be independently approved by two different models
  before it's ever eligible to apply** -- this is the only path to a
  self-edit; there's no way to skip straight from proposal to
  apply_patch.
- The (now-reviewed) diff is validated with `git apply --check` before
  anything touches disk.
- After applying, every changed file is `py_compile`'d. Any compile
  failure triggers an automatic full revert -- nothing broken ever stays
  applied. **This includes new files a patch adds, not just changes to
  existing ones** -- an earlier version of this check used `git diff
  --name-only`, which only lists changes to already-tracked files. A
  patch that added a brand new, broken `.py` file would silently skip
  the compile-check entirely and sail straight through to a commit,
  confirmed by testing that exact case. Fixed by staging everything
  (`git add -A`) right after applying, so newly-added files are
  included in what gets checked -- and by switching the revert itself
  from `git checkout -- .` (which only restores tracked files) to
  `git reset --hard HEAD && git clean -fd` (which also removes
  newly-created untracked files), so a reverted patch no longer leaves
  stray new files behind on disk.
- Review verdicts are parsed with markdown-fenced JSON tried first, not
  a naive greedy `\{.*\}` match -- the old approach broke (and was
  confirmed to break, not just suspected) whenever a reviewer's
  *feedback text* mentioned anything brace-like (a dict literal, a code
  snippet), since the greedy match would span from the real JSON's
  opening brace all the way to the last, unrelated closing brace
  anywhere in the response, producing invalid JSON. The fallback
  keyword check that catches a parse failure happened to still reach
  the right answer in testing, but only by accident -- not something
  to rely on. Fixed at the root: fenced JSON is tried first now, and
  only falls back to the greedy match as a last resort.
- If `--test` is used, pytest runs through `SecureExecutionSandbox`
  (`smeagol_core/sandbox.py`) rather than a raw unconfined subprocess
  call -- confined to the project's own directory, a minimal PATH, and
  a hard timeout that kills a hung test run instead of letting it hang
  the whole command indefinitely.
- Every successful change is logged to `data/mutation_log.jsonl`
  (`smeagol_core/mutation_log.py`) -- what changed, why, when -- in
  addition to the git commit itself, so there's a queryable history
  beyond scanning commit messages one at a time. `smeagol edit`'s
  successful fixes go in the same log.
- A file-based lock (`smeagol_core/process_lock.py`) stops two separate
  smeagol processes from mutating files at the same time -- e.g. the
  desktop GUI's Auto-fix button spawns its own terminal process, and
  nothing stops you from also running `smeagol improve` yourself in a
  different terminal simultaneously. This needed an actual file lock,
  not an in-process `asyncio.Lock` (which only serializes concurrent
  coroutines inside one process, not two separate OS processes) --
  confirmed by literally spawning two real, separate processes and
  watching the second one correctly get blocked immediately rather than
  racing or hanging.
- Every successful change is a discrete git commit, so `git log` shows
  the whole history of how Smeagol has modified itself, and any change
  is one `git revert <hash>` away from undone.
- This only ever touches files inside `smeagol_core/` -- there's no path
  here that lets a model reach outside the project directory.

The `--apply` flag is still separate from the dry run on purpose: you
always see the diff before review even starts. Review gates whether a
self-edit is *allowed*; `--apply` is still you deciding to actually run
the process. Both matter for different reasons.

## Auto-fix: a friendlier front door to self-improvement

```bash
smeagol auto-fix
```

Asks what you want added or fixed, then runs the exact same pipeline as
`improve ... --apply` -- propose, two-reviewer gate (Claude then
Gemini by default), compile check, git commit -- just without needing
to remember the full command or list every model by hand (it uses
every non-persona model currently registered). There's no lighter,
review-free path hiding behind this; it's the same gate every time.

## Editing any script on this machine

```bash
smeagol edit ~/scripts/backup.sh
smeagol edit ~/projects/tool.py "it crashes when the input file is empty"
```

This is a different, wider-reach feature from self-improvement, and
it's treated with different caution to match:

- Requires an explicit path -- there's no auto-discovery or scanning.
- Refuses outright to touch anything under a system directory (`/etc`,
  `/usr`, `/bin`, `/boot`, etc.) -- "a script on my computer" means
  your own stuff.
- Always backs up the original file (`name.smeagol-backup-<timestamp>`)
  before writing anything.
- For `.py` files, runs a compile check and automatically reverts if
  the fix doesn't even parse.
- For any file type, you can give an optional verify command (e.g.
  `bash -n script.sh`, or actually running the script) -- it runs
  through `SecureExecutionSandbox`, confined to the file's own
  directory, and a failing or hanging verify command reverts the fix
  just like a failed compile check does. This is what extends real
  verification to non-Python scripts, which previously got none at all.
- Always shows the diff and asks to confirm before writing anything --
  no silent edits.

## The execution sandbox

`smeagol_core/sandbox.py` is a small, dependency-free way to run a
command confined to a specific directory with a minimal `PATH` and a
hard timeout that kills a hung process rather than letting it hang
forever. Two places use it: the `--test` pytest step in self-improve,
and the optional verify command in `smeagol edit`.

Worth being precise about what it actually guarantees, since the name
"sandbox" can imply more than this delivers: it's a **process-level**
restriction (working directory + PATH + timeout), not a kernel-level
one. A command can still read or write files outside its assigned
directory using absolute paths -- nothing here uses Linux namespaces,
seccomp, or a chroot to actually block that at the OS level. For real
filesystem-level confinement on Manjaro/Arch, wrap the same command
with `bubblewrap` (see the docstring in `sandbox.py` for the exact
invocation) -- deliberately not bundled as a hard dependency, since
bubblewrap may not be installed on every system this runs on, and
silently claiming a stronger guarantee than what's enforced without it
would be worse than being upfront that it's opt-in.

**On the Android app:** there's genuinely nothing to port here. The
sandbox exists because the desktop can edit its own code and run
arbitrary scripts -- the phone app has neither capability (it's a chat
client, full stop), so there's no equivalent surface for a command
sandbox to protect. If that ever changes -- if the phone app grows any
feature that executes code or edits files -- this is the pattern to
extend to it, not something to force in now for the sake of symmetry.

## Giving Smeagol a personality

`persona.system_prompt` in `config/models.yaml` ships with an optional
example: an original character flavor (not quotes from any book or
film) loosely inspired by a certain riddling, split-voiced creature --
wary of strangers, a little possessive of things it relies on, arguing
with itself out loud. Delete it for a plain, unstyled voice, or replace
the whole block with your own -- it's just text, and it applies in
exactly two places (see "Smeagol as the main interface" below):
Smeagol's own direct answers, and the final answer after a consult. It
never affects `claude`, `gemini`, or any local model addressed directly
by name.

One honest limit worth knowing: a system prompt shapes *tone*, not what
the underlying model will actually do. Asking it to claim it's
"uncensored" wouldn't change Claude's or Gemini's real behavior, since
that's enforced server-side by Anthropic/Google, not by a local prompt
-- you'd just get a model describing itself inaccurately. Personality
and character voice, on the other hand, work exactly as shown above.

## Remote system administration (reboot, Docker/Jellyfin, anything)

Beyond chat, the phone can also run arbitrary commands on the desktop
and trigger a reboot -- genuinely useful for a headless box (no
monitor attached) running things like a Jellyfin/Docker setup. This is
a meaningfully bigger security surface than chat, so it's deliberately
built differently, not just bolted onto the existing endpoints.

**A separate admin token, never shown in the pairing QR.** Your regular
phone-pairing token is fine to have float around as a QR code -- worst
case with that alone is someone chats with your Smeagol. The admin
token additionally gates running shell commands and rebooting the
machine, so it's never embedded in the QR flow at all:

```bash
smeagol admin-token
```

Run that once, copy the token into the app's Settings → Admin section
manually. Pairing your phone for chat never grants this by itself.

**What it can do, from Settings → Admin:**
- Run any command on the desktop (`docker compose restart jellyfin`,
  `docker logs jellyfin`, editing a compose file via `smeagol edit`,
  anything) -- runs through the same `SecureExecutionSandbox` used
  elsewhere in this project (confined working directory, minimal PATH,
  hard timeout), and every command is logged to
  `data/mutation_log.jsonl` alongside self-improve/edit history.
- Reboot the desktop, behind a confirmation dialog in the app (not a
  one-tap accident) and a second confirmation server-side (the request
  goes to a completely separate endpoint requiring the admin token).

**Reboot needs one thing set up on your end that I can't do for you**
-- passwordless sudo scoped to exactly the reboot command, not broad
sudo access:

```bash
sudo visudo
```
Add this line (replace `yourusername`):
```
yourusername ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
```

## Auto-start on boot (no monitor needed)

So the server comes back up on its own after a reboot -- or after a
power blip -- without anyone logging in or opening a terminal:

```bash
sudo cp deploy/smeagol.service /etc/systemd/system/smeagol.service
sudo nano /etc/systemd/system/smeagol.service   # fill in your actual username and path
sudo systemctl daemon-reload
sudo systemctl enable --now smeagol
```

If Smeagol needs to manage Docker containers (Jellyfin, etc.), add
your user to the `docker` group rather than running this service as
root:
```bash
sudo usermod -aG docker $USER
```
(log out and back in once for that to take effect)

Check it's running: `systemctl status smeagol`. Logs:
`journalctl -u smeagol -f`.

One hardware caveat I can't verify for your specific machine: most
modern boards boot fine with no monitor attached, but a few older ones
refuse to POST without a display detected. If yours does that, a cheap
HDMI dummy plug fixes it -- not a Smeagol issue, that's motherboard
firmware behavior from before any of this software runs.

## Desktop hologram widget

```bash
sudo pacman -S webkit2gtk-4.1   # native web renderer pywebview needs on Manjaro
pip install pywebview
python gui/app.py
```

A small always-on-top window with an animated face -- an original
stylized design (not the film character's likeness, just a pale,
large-eyed wireframe head with an idle bob and a holographic scanline
flicker), not a full 3D engine, but genuinely animated rather than a
static image. Click it to open a settings panel showing every
registered model and Smeagol's current persona wiring (primary,
fallback, consult group, last resort), with buttons that open a
terminal running `smeagol chat smeagol`, `smeagol models`,
`smeagol remove`, or `smeagol auto-fix` -- the GUI is a status view and
launcher for the existing CLI, not a reimplementation of it.

**I couldn't visually test this myself** -- this sandbox has no
display, so while every piece of Python logic behind it is unit-tested
(config reading, the exact terminal command built for gnome-terminal /
konsole / xterm / xfce4-terminal, window-reopen handling) and both HTML
files pass real JS syntax checking and tag-balance validation, the
actual on-screen appearance and window behavior needs your eyes on
your actual desktop. If the hologram looks off or a button doesn't
behave right, that's the part to tell me about.

## Talking to Smeagol from your phone

Two parts: a server that runs on the desktop, and an Android app that
talks to it. Same-Wi-Fi/LAN only for now -- reaching it from outside
your home network would need TLS and port-forwarding or a tunnel,
which is a distinct, bigger feature than what's here.

### Away-mode conversations sync back automatically

If you chat with Smeagol while away from home (standalone mode, direct
Claude/Gemini calls), the desktop has no idea that happened -- until
now. The phone queues up every away-mode exchange locally
(`pending_sync.jsonl` in the app's own storage), and the moment it
successfully reconnects to the desktop, ships that whole queue along
with its very next message. No separate sync step, no button to press
-- it rides along with the first message that actually reaches home.

The desktop logs everything it receives to
`data/away_session_log.jsonl`, so there's a durable record of what was
discussed while you were out, alongside the prompt/answer/source
(claude or gemini) and both when it actually happened and when it got
synced.

The queue is only cleared once the desktop *confirms* it received the
entries -- if the connection drops mid-sync, or the very message
carrying the sync data fails to deliver, nothing is lost; it just
tries again on the next successful reconnect. Confirmed this holds by
testing both directions directly: a real request carrying two queued
away-mode exchanges landed exactly two entries in the log with the
right prompts/answers/sources attached, and a normal chat message with
nothing queued left no log file at all.

**It's not just logged anymore -- Smeagol actually considers it.** The
last 5 synced away-session exchanges get handed to Smeagol as
background context on every message from here on, not just recorded
and forgotten. Ask "what did I ask about while I was out" once you're
home and it can actually answer, rather than that history sitting
inertly in a file nobody reads. Deliberately small and bounded (last 5,
not the whole history) -- this is meant to be light, naturally-aging
context, not a full conversation replay that grows without limit.
Confirmed working through the real `/chat` endpoint end to end: synced
an away-mode exchange about an oven timer on message 1, asked an
unrelated follow-up on message 2, and confirmed the oven timer context
actually reached the model as background for that second message.

### Desktop side

```bash
smeagol serve
```

Starts an HTTP server, generates (and persists) a pairing token, and
prints a pairing URL plus a scannable QR code in the terminal
(`pip install qrcode` for the QR art; works without it too, you'd just
type the URL in manually). Leave this running while you use the app.

This was the trickiest part to get right: Smeagol's backends hold
`asyncio.Lock` objects created once and reused across every request.
The naive approach -- a fresh `asyncio.run()` per incoming request --
would mean multiple threads each spinning up their own event loop while
sharing those same lock objects, which **isn't just theoretically
wrong, it actually deadlocks** (confirmed by intentionally triggering
it before writing the real fix). The server instead runs one
persistent event loop in a single dedicated thread for its whole
lifetime; every request submits its work to that one loop and waits
for the result. I tested this for real: fired 6 concurrent requests at
a running instance of the actual server and confirmed they all
completed correctly, together, without hanging -- not just that the
logic looked right on paper.

### Android app

Source is in `android/` -- open that folder in Android Studio, let it
sync, run it on your phone.

This is a full app, not just a thin client: it works whether or not a
desktop is anywhere nearby.

- **At home** (paired, desktop reachable): every message goes to the
  desktop's `/chat` endpoint and gets the full orchestrator -- all your
  local models, consult, everything, exactly as `smeagol chat smeagol`
  gives you.
- **Away from home** (or never paired at all): the app calls Claude or
  Gemini directly from your phone, using API keys you enter in Settings
  (tap the hologram). It tries whichever you set as preferred, falls
  back to the other if that fails, and speaks in the same persona voice
  cached from the last time it *could* reach the desktop -- so the
  character doesn't change depending on where you are.

Deliberately **not** implemented as a second copy of the router/persona/
consult logic in Kotlin -- that stays in exactly one place
(`smeagol_core`), and the phone either borrows it over the network or
falls back to a much simpler direct call. Every message tries the
desktop first with a short connect timeout (fast on the home LAN,
fails quick everywhere else) before falling back, so there's no
noticeable delay at home and no long hang away from it.

The chat screen and hologram are both usable immediately on first
launch, paired or not -- pairing and API keys are things you configure
from Settings, not gates blocking the app until you do. Tap "Pair with
Desktop" any time to scan a new QR code (also how you switch to a
different desktop later).

**Permissions:** `INTERNET` (talking to the desktop and to Claude/
Gemini), `CAMERA` (QR pairing scan), `ACCESS_NETWORK_STATE` (skip
straight to standalone mode when there's no network at all instead of
waiting out a connect timeout). Deliberately **no** storage permission
-- modern Android restricts/deprecates broad storage permissions and
Google Play flags apps that request them without real justification.
Chat history auto-saves to the app's own private storage instead (no
permission needed on any Android version), and "Export Chat" uses the
system file picker (Storage Access Framework), which also needs no
manifest permission at all. Same real functionality, done the way
Android actually wants it done now.

**Settings now also has:** dropdowns to pick the exact Claude and
Gemini model used in standalone mode (`claude-sonnet-5`/`claude-opus-4-8`/
`claude-haiku-4-5-20251001`, `gemini-2.5-flash`/`gemini-3.1-pro`/
`gemini-3.5-flash`), alongside the API key fields and the Claude/Gemini
preference toggle.

**Dark theme:** the app is always dark, not just following system
DayNight -- a white light-mode screen around the dark hologram widget
would look broken rather than themed, so `Theme.Smeagol` forces the
always-dark Material Components variant with a palette matching the
hologram's cyan aesthetic (`colors.xml`).

Same hologram widget as the desktop, and literally the same file
(`gui/assets/hologram.html`, copied into
`android/app/src/main/assets/`) -- it detects whether it's running in
pywebview or an Android WebView and calls the matching bridge. If you
edit the desktop version later, copy it over to keep the phone in sync:

```bash
cp gui/assets/hologram.html android/app/src/main/assets/hologram.html
```

**Important honesty note about this specific piece:** I have no
Android SDK, emulator, or Kotlin compiler available in this
environment -- not even reachable over the network to install one. I
could not compile or run this app even once. Everything else in this
project that I've handed you has been actually executed and tested;
this is the one exception, and it's a real difference in confidence
level, not a formality. What I *did* do: verified current Android
Gradle Plugin / Kotlin version compatibility via a live search rather
than trusting my training data (AGP has moved to a 9.x line with
built-in Kotlin support since I last knew about it), validated every
XML file is well-formed (and caught a real bug this way -- an XML
comment with a stray `--` in the middle of a sentence, which is
invalid XML and would have broken the build), cross-checked every
`R.id.*` reference in the Kotlin against what's actually declared in
the layouts (a very common source of Android build failures when they
drift), and checked all three Kotlin files are at least structurally
sound (balanced braces/parens). The Claude and Gemini API request
formats in `SmeagolClient.kt` match the same shapes already tested on
the desktop side (`anthropic_backend.py`, `gemini_backend.py`). But
actual compilation, and whatever Android Studio's dependency resolution
turns up, is genuinely unverified. If it doesn't build cleanly on the
first try, that's expected enough that it's worth just telling me the
exact error -- most likely candidates are a dependency version Android
Studio wants bumped, or a Gradle/AGP version mismatch, both of which
Android Studio's upgrade assistant handles automatically when prompted.

## Adding more models

Add a block to `config/models.yaml`: `local_gguf` for another local
file, `openai_compatible` with `base_url: http://localhost:11434/v1` for
anything running through Ollama, or `anthropic`/`openai_compatible` for
an API model. No code changes needed.

## Roadmap (not yet built)

- **Voice**: whisper.cpp (STT) + Piper (local TTS) in front of `Router`.
- **System control**: a separate sandboxed tool layer (bubblewrap or a
  container) exposing specific permissioned actions as callable tools --
  kept separate from the router so a model never touches your system
  directly, only through an explicit, audited tool call.
- **Harness/plugin mode**: wrapping this orchestrator as an MCP server
  so tools like Claude Code can route through Smeagol.

## Putting this on GitHub (and connecting it to Claude Code / Termux)

```bash
cd ~/Downloads/smeagol
git init
git add .
git commit -m "initial"
```
(the existing `.gitignore` already keeps `.env`, `data/`, downloaded
`.gguf` files, and `venv/` out of what gets committed)

Create a new empty repo on GitHub (no README/license -- you already
have files), then:
```bash
git remote add origin <your-new-repo-url>
git push -u origin main
```

`.github/workflows/ci.yml` runs automatically from there -- checks out
on a real Ubuntu machine, installs dependencies (using the CPU
prebuilt wheel, so it never hits the compile-from-source issue you ran
into locally), imports every module, confirms the registry actually
builds from `config/models.yaml`, and runs any tests in `tests/` if
you add them. Check the **Actions** tab after pushing to see it run.

**A word on `claude --resume <session-id>` specifically**: that's
Claude Code session state, which lives wherever it was originally
created -- if that session already exists in Termux, resuming it there
is completely normal and works exactly as expected. What doesn't work
is expecting *this chat* to somehow attach to that session or push
code on your behalf -- I have no access to your GitHub account, your
phone, or that session's state from here. The actual connection
between everything is the GitHub repo itself: once it's pushed,
`claude --resume 7959bf23-a3e3-4ac3-9bd9-951d61ce4902` in Termux gives
you a real, full-access Claude Code session that can `git pull` this
exact repo, make changes, commit, and push -- genuinely different from
this chat, which can only hand you files to move over yourself.

### Setting Termux up for this, if it isn't already

Worth catching before it wastes your time: a plain
`npm install -g @anthropic-ai/claude-code` frequently fails on real
Termux with an "Unsupported platform: android arm64" error. Claude
Code's native binary expects genuine glibc Linux; Android's Termux
runs on bionic libc instead, which isn't the same thing. The reliable
path people are actually using successfully is a real Ubuntu
environment inside Termux via `proot-distro`, not fighting the raw
Termux environment directly:

```bash
pkg update && pkg upgrade -y
pkg install proot-distro git -y
proot-distro install ubuntu
proot-distro login ubuntu
```

Then, inside that Ubuntu shell (a genuine Linux environment from here on):
```bash
apt update && apt install -y nodejs npm git
npm install -g @anthropic-ai/claude-code
git clone <your-repo-url>
cd smeagol
claude --resume 7959bf23-a3e3-4ac3-9bd9-951d61ce4902
```

Also worth knowing: Termux needs to be installed from F-Droid, not the
Google Play Store -- the Play Store version is outdated and abandoned,
and will cause exactly the kind of confusing failures this setup is
already prone to.

If that session ID doesn't resolve (expired, or turns out to live in a
different environment than this one), `claude` on its own starts a
fresh session in the same directory -- either way you end up with a
real Claude Code instance with actual shell/git access on your phone.

If you've already got Claude Code running in Termux some other way (a
community compatibility wrapper, a different setup already working for
you), you likely already solved this -- just `git clone` the repo into
wherever that environment's filesystem actually lives.

### The Android project

Lives in `android/`, tracked in this same repo -- the same
`git add . && git push` above pushes both the desktop project and the
Android app together, in one commit, one push. Nothing separate to
set up for it.

What keeps them from stepping on each other in CI: `.github/workflows/`
has two independent workflow files, `ci.yml` (desktop) and
`android-build.yml` (Android), each path-filtered to its own half of
the repo (`paths-ignore: android/**` on the desktop side,
`paths: android/**` on the Android side) -- a change to one doesn't
trigger a rebuild of the other, even though they're one push. Both
workflow files have to live at the repo root specifically
(`.github/workflows/`, not `android/.github/workflows/`) since that's
the only place GitHub Actions actually looks; a workflow file tucked
inside a subfolder is silently invisible to it, which I confirmed by
actually building this exact layout locally, running `git add .` from
the repo root, and checking that both workflow files land where
GitHub Actions can see them and android's 17 source files get tracked
alongside the desktop's, all under one `.git`, before ever handing it
to you.

See `android/README.md` for Android-specific details (downloading the
built APK, opening it in Android Studio, the Gradle wrapper caveat).

## Scope note

This is a local orchestration app that runs on top of Manjaro, not a new
operating system in the kernel/init sense -- building on an existing
distro gives you the whole Linux ecosystem for free.
