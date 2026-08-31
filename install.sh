#!/usr/bin/env bash
#
# open-pharma-plugins — interactive installer & setup.
#
#   curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
#   less install.sh && bash install.sh                                  # inspect, then run
#   bash install.sh [install|update|local|configure|verify|uninstall]  # single interactive action
#   bash install.sh local --restore  # restore release refs after local-checkout testing
#   bash install.sh --verify [caps]   # non-interactive: check system deps of installed (or listed) caps
#
# What it does: installs the plugin via each harness's NATIVE marketplace (it does not
# reinvent install), then writes your config (API keys, endpoints, dirs, tuning, OSS, host
# addresses — the whole grouped list) to a single fixed config file (~/.open-pharma-plugins/config)
# that every harness reads — GUI or terminal — so you set it once.
#
# Env overrides: OPP_REPO (git URL or local checkout), OPP_REF (historical capability tag), NO_COLOR.
# With no OPP_REF, every capability resolves to its own latest immutable tag below.

set -uo pipefail

REPO_URL="${OPP_REPO:-https://github.com/PharmaGenAI/open-pharma-plugins.git}"
REPO_REF="${OPP_REF:-}"
MARKETPLACE="open-pharma-plugins"
CONFIG_DIR="${OPEN_PHARMA_CONFIG_DIR:-$HOME/.open-pharma-plugins}"
CONFIG_FILE="${OPEN_PHARMA_CONFIG:-$CONFIG_DIR/config}"
OPP_DRY=0
LOCAL_REPO_ROOT=''

# ── capability catalog — the ONE place capabilities are declared; every menu iterates this ──
CAP_ITEMS=(hcp-intelligence field-training campaign-studio next-best-engagement territory-alignment competitive-intelligence)
# Latest stable plugin versions, in exactly the same order as CAP_ITEMS. Keep this release index in
# sync with plugin-versions.json; scripts/check_manifests.py and
# tests/tooling/test_install_sh.py enforce it.
CAP_VERSIONS=(1.0.2 1.1.1 1.1.0 1.0.2 1.2.0 1.1.0)
CAP_DESC=("HCP/HCO intelligence — profile doctors and organizations from public sources"
          "field rep training — learning packages, assessments, role-play kits, and scorecards from approved docs"
          "campaign studio — briefs, copy, claim validation, and asset rendering for MLR"
          "next-best-engagement — score HCPs, select actions, allocate to reps"
          "territory alignment — assign HCPs to reps, visit routing, interactive maps"
          "competitive intelligence — trial pipelines, FDA events, news, publications, CI briefs")
# Skill-only capabilities have NO MCP server / pyproject extra / console entry: they install via
# the marketplace like any plugin, but the uvx --check-system self-test doesn't apply to them.
CAP_SKILL_ONLY=""
is_skill_only() { case "$CAP_SKILL_ONLY" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# ── harness catalog — the ONE place supported harnesses are declared. All install a bundled
# skill+MCP plugin through their native marketplace. Menus, status, and detection iterate this list;
# install_for/_detect_mask/do_uninstall provide the corresponding native commands.
ALL_HARNESSES="claude codex copilot"

# ── config-field catalog — the ONE place user-settable config vars are declared; do_configure
# iterates it (like CAP_ITEMS for capabilities). Mirrors src/shared/env.py CONFIG_FIELDS — keep the
# two in sync when adding a var. Each row: KEY|secret(0/1)|group-tag|default|one-line description.
# `default` is the effective value or a concise default hint when the var is unset (empty = no
# default / required / off). Groups are ordered + titled by CONFIG_GROUPS / config_group_title.
# Excludes the config-location bootstrap (OPEN_PHARMA_CONFIG/_DIR) and behavioral toggles
# (OPEN_PHARMA_AUTOLAUNCH/…).
# bash-3.2 safe (no assoc arrays).
CONFIG_SPEC=(
  "OPEN_PHARMA_SEARCH_BACKEND|0|search|auto|text search backend (auto: serper > tavily > exa; or choose one)"
  "SERPER_API_KEY|1|search||Serper web search API key"
  "TAVILY_API_KEY|1|search||Tavily web search API key"
  "EXA_API_KEY|1|search||Exa web search API key"
  "OPENROUTER_API_KEY|1|hcp||OpenRouter API key for optional batch profile synthesis"
  "OPENROUTER_BASE_URL|0|hcp|https://openrouter.ai/api/v1|OpenRouter-compatible API base URL for optional batch profile synthesis"
  "NCBI_API_KEY|0|hcp||NCBI E-utilities API key (optional; raises PubMed rate limit from 3 to 10 req/s)"
  "OPEN_PHARMA_HCP_DATA_DIR|0|hcp||mutable enrichment data directory (default: ~/.open-pharma-plugins/hcp-intelligence)"
  "OPEN_PHARMA_TRAINING_CONTENT_DIR|0|training||content store directory for ingested training documents (default: ~/.open-pharma-plugins/training-content)"
  "OPEN_PHARMA_CAMPAIGN_STORE_DIR|0|campaign||root directory for campaign briefs, claims, and rendered assets (default: ~/.open-pharma-plugins/campaign-studio)"
  "OPEN_PHARMA_NBE_OUTPUT_DIR|0|nbe||directory for exported engagement plans (default: ~/.open-pharma-plugins/next-best-engagement)"
  "OPEN_PHARMA_TA_DATA_DIR|0|territory||directory containing hcps.csv, reps.csv, current_alignment.csv, constraints.csv (default: built-in fixtures)"
  "OPEN_PHARMA_TA_SCENARIOS_DIR|0|territory||directory for saved alignment scenarios (default: ~/.open-pharma-plugins/territory-alignment/scenarios)"
  "OPEN_PHARMA_CI_DATA_DIR|0|ci||watchlist, report, and cache directory (default: ~/.open-pharma-plugins/competitive-intelligence)"
  "OPENFDA_API_KEY|0|ci||openFDA API key (optional; raises daily quota from 1,000/IP to 120,000/key; 240 req/min either way)"
  "CI_CACHE_TTL_HOURS|0|ci|24|hours before cached API responses expire (default: 24)"
)
CONFIG_GROUPS=(search hcp training campaign nbe territory ci)
config_group_title() {
  case "$1" in
    search)   printf 'Web Search' ;;
    hcp)      printf 'HCP Intelligence' ;;
    training) printf 'Field Training' ;;
    campaign) printf 'Campaign Studio' ;;
    nbe)      printf 'Next Best Engagement' ;;
    territory) printf 'Territory Alignment' ;;
    ci)       printf 'Competitive Intelligence' ;;

    *)      printf '%s' "$1" ;;
  esac
}

# Harness CLIs often live in per-user dirs a non-login/GUI-spawned shell drops from PATH: nvm's
# node bin (claude), etc. Add the ones that exist so detection
# and the install/uninstall commands resolve regardless of how this script was launched.
_add_path() { [ -d "$1" ] && case ":$PATH:" in *":$1:"*) ;; *) PATH="$PATH:$1" ;; esac; }
_add_path "$HOME/.local/bin"
_add_path "$HOME/bin"
_add_path "/opt/homebrew/bin"      # macOS Apple-silicon Homebrew (brew-installed claude/uv/node)
_add_path "/usr/local/bin"         # macOS Intel Homebrew / common CLI install prefix
if [ -d "$HOME/.nvm/versions/node" ]; then
  for _d in "$HOME"/.nvm/versions/node/*/bin; do _add_path "$_d"; done
fi
export PATH

# Non-interactive forms (--verify / --help / local --restore) run headless and need no terminal;
# every other invocation is an interactive TUI that reads keys from the tty (fd 3).
NONINTERACTIVE=0
case "${1:-}:${2:-}" in
  --verify:*|-h:*|--help:*|local:--restore) NONINTERACTIVE=1; OPP_NO_TUI=1 ;;
esac
# ── an interactive terminal, even under `curl | bash` (stdin is the pipe → read from /dev/tty) ──
if [ "$NONINTERACTIVE" = 1 ]; then
  exec 3</dev/null                                   # headless: no prompts, no terminal required
elif ! { exec 3</dev/tty; } 2>/dev/null; then
  printf 'This installer is interactive — run it in a terminal (or use a documented headless command):\n' >&2
  printf '  curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh && bash install.sh\n' >&2
  exit 1
fi

# ── palette ──
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C0=$'\033[0m'; CB=$'\033[1m'; CD=$'\033[2m'
  CR=$'\033[31m'; CG=$'\033[32m'; CY=$'\033[33m'; CC=$'\033[36m'; CQ=$'\033[1;36m'
else
  C0='' CB='' CD='' CR='' CG='' CY='' CC='' CQ=''
fi

# 24-bit color? enables the cyan→blue gradient logo; falls back to flat cyan on 16-color terminals.
OPP_TRUECOLOR=0
[ -n "$CC" ] && case "${COLORTERM:-}" in *truecolor*|*24bit*) OPP_TRUECOLOR=1 ;; esac

# Menus hide the cursor (\033[?25l); restore it + reset attributes on Ctrl-C / kill so the
# terminal isn't left with an invisible cursor. Also drops spin's temp file if killed mid-spin.
_OPP_SPIN_TMP=''
# _TTY_SAVED holds the tty's cooked-mode settings while a single-key menu has it in cbreak mode (see
# tty_cbreak below). On Ctrl-C / kill, restore the cursor, reset attributes, put the tty BACK to cooked
# so the shell isn't left with no echo / invisible cursor, and drop any spinner temp file.
_TTY_SAVED=''
_restore_tty() { [ -t 1 ] && printf '\033[?25h\033[0m'; [ -n "$_TTY_SAVED" ] && stty "$_TTY_SAVED" <&3 2>/dev/null; _TTY_SAVED=''; [ -n "$_OPP_SPIN_TMP" ] && rm -f "$_OPP_SPIN_TMP"; }
# EXIT covers abnormal exits too (e.g. `set -u` on an unbound var mid-menu) — without it a crash while
# a menu holds the tty in no-echo cbreak mode would leave the shell with no echo + hidden cursor.
trap '_restore_tty' EXIT
trap '_restore_tty; exit 130' INT
trap '_restore_tty; exit 143' TERM

have()  { command -v "$1" >/dev/null 2>&1; }
# Friendly harness name → its CLI binary.
harness_bin() {
  printf '%s' "$1"
}
require_harness() {
  local h=$1 bin; bin=$(harness_bin "$h")
  have "$bin" && return 0
  warn "$h is not available — '$bin' is not installed or not on PATH"
  pause
  return 1
}
hr()    { printf '\n%b▸ %s%b\n' "$CC$CB" "$1" "$C0"; }
ok()    { printf '  %b✓%b %s\n' "$CG" "$C0" "$1"; }
warn()  { printf '  %b!%b %s\n' "$CY" "$C0" "$1"; }
err()   { printf '  %b✗%b %s\n' "$CR" "$C0" "$1"; }
mark()  { [ "$1" = ok ] && printf '%b✓%b' "$CG" "$C0" || printf '%b·%b' "$CD" "$C0"; }

# Animate a braille spinner while running <cmd> in the background; capture its stdout into <outvar>.
# Falls back to a plain synchronous run when stdout isn't a TTY (pipes/CI). Returns <cmd>'s exit code.
# Bounded by OPP_SPIN_TIMEOUT seconds (default 15; 0 = unbounded): a wedged harness `list` CLI (network,
# or an auth prompt that gets EOF under curl|bash) would otherwise spin forever. On timeout the child is
# killed and <outvar> left empty — every caller treats empty detection output as "unknown / nothing
# installed", so a stuck detection degrades to an unfiltered menu instead of a frozen installer. Job
# control is off under curl|bash (one process group), so we kill only the child pid, never the group.
spin() {  # spin <message> <outvar> -- cmd...
  local msg=$1 outvar=$2; shift 2; [ "${1:-}" = "--" ] && shift
  if [ ! -t 1 ] || [ -n "${OPP_NO_TUI:-}" ]; then
    local _o; _o=$("$@"); local _rc=$?; printf -v "$outvar" '%s' "$_o"; return $_rc
  fi
  local tmp; tmp=$(mktemp "${TMPDIR:-/tmp}/opp.XXXXXX"); _OPP_SPIN_TMP=$tmp
  ( "$@" >"$tmp" 2>/dev/null ) &
  local pid=$! i=0 ticks=0 timedout=0
  local maxticks=$(( ${OPP_SPIN_TIMEOUT:-15} * 10 ))       # loop sleeps 0.1s → 10 ticks/second
  local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
  printf '\033[?25l'
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r  %b%s%b %s' "$CC" "${frames[i]}" "$C0" "$msg"
    i=$(( (i + 1) % 10 )); sleep 0.1; ticks=$((ticks + 1))
    if [ "$maxticks" -gt 0 ] && [ "$ticks" -ge "$maxticks" ]; then
      timedout=1; kill "$pid" 2>/dev/null; sleep 0.2; kill -9 "$pid" 2>/dev/null; break
    fi
  done
  wait "$pid" 2>/dev/null; local rc=$?
  printf '\r\033[2K\033[?25h'
  if [ "$timedout" = 1 ]; then
    printf -v "$outvar" '%s' ''; rm -f "$tmp"; _OPP_SPIN_TMP=''
    warn "timed out after ${OPP_SPIN_TIMEOUT:-15}s: ${msg%%...*} — continuing"
    return 124
  fi
  printf -v "$outvar" '%s' "$(cat "$tmp")"; rm -f "$tmp"; _OPP_SPIN_TMP=''
  return $rc
}

# repeat <string> <count> → <string> concatenated <count> times (bash-3.2 safe, no seq/brace-var).
repeat() { local _i _s=''; for ((_i = 0; _i < ${2:-0}; _i++)); do _s="$_s$1"; done; printf '%s' "$_s"; }

# Real terminal width via the tty (fd 3) — works even under `curl | bash`; falls back to tput, then
# 80. Do not pretend a narrow terminal is wider than it is: interactive menus move the cursor by
# logical row count, so an auto-wrapped row would leave duplicates behind on every arrow keypress.
term_cols() {
  local c; c=$(stty size <&3 2>/dev/null | awk '{print $2}')
  [ -z "$c" ] && c=$(tput cols 2>/dev/null)
  case "$c" in ''|*[!0-9]*) c=80 ;; esac
  [ "$c" -lt 20 ] && c=20
  printf '%s' "$c"
}

# ── rounded-box panels — for STATIC screens (the status summary + result recaps). Interactive menus
# are NOT boxed: they redraw line-by-line with cursor moves, which a fixed frame would fight. Boxes
# assume the same UTF-8 terminal the braille spinner / ✓ / ██ banner already require.
#
# _vwidth <str> → visible column count of a (possibly colored) narrow-glyph string: strip CSI SGR
# escapes (\e[…m), then count characters (== columns here — no wide/CJK glyphs in boxed content;
# ${#s} is char-count under the assumed UTF-8 locale). Pure bash, no sed/awk (works under curl|bash).
_vwidth() {
  local s=$1 pre rest
  while [ "${s#*$'\033['}" != "$s" ]; do
    pre=${s%%$'\033['*}; rest=${s#*$'\033['}; rest=${rest#*m}; s="$pre$rest"
  done
  printf '%s' "${#s}"
}
# _fit <plain-ascii> <n> → truncate to n columns with a trailing ellipsis (keeps result boxes aligned
# when a path/version is long). ASCII-only input, so byte length == column width.
_fit() { local s=$1 n=$2; [ "$n" -lt 1 ] && n=1; [ "${#s}" -le "$n" ] && { printf '%s' "$s"; return; }; printf '%s…' "${s:0:$((n - 1))}"; }

# box_open <title> / box_row <colored-content> / box_close — a closed rounded frame. box_open sets
# _BOX_W (inner content columns); every row pads to it so the right border lines up. Colorless under
# NO_COLOR / non-TTY (the frame glyphs still print as plain text).
_BOX_W=0
box_open() {  # box_open <title>
  local title=$1 cols dashes
  cols=$(term_cols); _BOX_W=$(( cols - 6 ))
  [ "$_BOX_W" -gt 70 ] && _BOX_W=70; [ "$_BOX_W" -lt 14 ] && _BOX_W=14
  title=$(_fit "$title" "$(( _BOX_W - 2 ))")
  dashes=$(( _BOX_W - ${#title} - 1 )); [ "$dashes" -lt 0 ] && dashes=0
  printf '  %b╭─ %b%s%b %s╮%b\n' "$CC" "$CB$CC" "$title" "$C0$CC" "$(repeat '─' "$dashes")" "$C0"
}
box_row() {  # box_row <colored-content>
  local c=$1 pad; pad=$(( _BOX_W - $(_vwidth "$c") )); [ "$pad" -lt 0 ] && pad=0
  printf '  %b│%b %s%*s %b│%b\n' "$CC" "$C0" "$c" "$pad" '' "$CC" "$C0"
}
box_close() { printf '  %b╰%s╯%b\n' "$CC" "$(repeat '─' "$(( _BOX_W + 2 ))")" "$C0"; }

# A width-safe divider between capability command outputs. Keep it visually aligned with result
# boxes so several verbose --check-system reports remain easy to scan.
cap_divider() {
  local title=$1 cols width dashes
  cols=$(term_cols); width=$(( cols - 2 ))
  [ "$width" -gt 74 ] && width=74; [ "$width" -lt 18 ] && width=18
  title=$(_fit "$title" "$(( width - 7 ))")
  dashes=$(( width - ${#title} - 4 ))
  printf '\n  %b── %b%s%b %b%s%b\n' "$CC" "$CB" "$title" "$C0" "$CD" "$(repeat '─' "$dashes")" "$C0"
}

# Centered splash. The logo block is centered as a unit; the rule is derived from the logo width,
# not hardcoded. Both subtitles center on their own width. Everything recomputes from term_cols(),
# so the banner recenters with the window.
banner() {
  local -a _logo=(
' ██████  ██████  ███████ ███    ██'
'██    ██ ██   ██ ██      ████   ██'
'██    ██ ██████  █████   ██ ██  ██'
'██    ██ ██      ██      ██  ██ ██'
' ██████  ██      ███████ ██   ████'
'██████  ██   ██  █████  ██████  ███    ███  █████'
'██   ██ ██   ██ ██   ██ ██   ██ ████  ████ ██   ██'
'██████  ███████ ███████ ██████  ██ ████ ██ ███████'
'██      ██   ██ ██   ██ ██   ██ ██  ██  ██ ██   ██'
'██      ██   ██ ██   ██ ██   ██ ██      ██ ██   ██'
'██████  ██      ██    ██  ██████  ██ ███    ██ ███████'
'██   ██ ██      ██    ██ ██       ██ ████   ██ ██'
'██████  ██      ██    ██ ██   ███ ██ ██ ██  ██ ███████'
'██      ██      ██    ██ ██    ██ ██ ██  ██ ██      ██'
'██      ███████  ██████   ██████  ██ ██   ████ ███████'
  )
  local tag1='Agent Skills + MCP tools for vision-language models'
  local tag2='· installer & setup ·'
  local n=${#_logo[@]} i r g b w lw=0 cols pad tp1 tp2 sp short='Open Pharma Plugins' short_tag
  for ((i = 0; i < n; i++)); do w=${#_logo[i]}; [ "$w" -gt "$lw" ] && lw=$w; done
  cols=$(term_cols)
  if [ "$cols" -lt "$lw" ]; then
    short=$(_fit "$short" "$(( cols - 2 ))")
    short_tag=$(_fit "$tag1" "$(( cols - 2 ))")
    pad=$(( (cols - ${#short}) / 2 )); [ "$pad" -lt 0 ] && pad=0
    tp1=$(( (cols - ${#short_tag}) / 2 )); [ "$tp1" -lt 0 ] && tp1=0
    printf '\n%*s%b%s%b\n' "$pad" '' "$CB$CC" "$short" "$C0"
    printf '%*s%b%s%b\n' "$tp1" '' "$CD" "$short_tag" "$C0"
    return
  fi
  pad=$(( (cols - lw)       / 2 )); [ "$pad" -lt 0 ] && pad=0
  tp1=$(( (cols - ${#tag1}) / 2 )); [ "$tp1" -lt 0 ] && tp1=0
  tp2=$(( (cols - ${#tag2}) / 2 )); [ "$tp2" -lt 0 ] && tp2=0
  sp=$(printf '%*s' "$pad" '')
  printf '\n'
  if [ "$OPP_TRUECOLOR" = 1 ]; then          # vertical cyan(0,229,255) → blue(74,110,255) gradient
    for ((i = 0; i < n; i++)); do
      r=$(( 74 * i / (n - 1) )); g=$(( 229 - 119 * i / (n - 1) )); b=255
      printf '%s\033[1;38;2;%d;%d;%dm%s\033[0m\n' "$sp" "$r" "$g" "$b" "${_logo[i]}"
    done
  elif [ -n "$CC" ]; then                    # 16-color: flat bold cyan
    for ((i = 0; i < n; i++)); do printf '%s%b%b%s%b\n' "$sp" "$CB" "$CC" "${_logo[i]}" "$C0"; done
  else                                        # NO_COLOR / non-TTY: plain text
    for ((i = 0; i < n; i++)); do printf '%s%s\n' "$sp" "${_logo[i]}"; done
  fi
  printf '%s%b%s%b\n' "$sp" "$CD" "$(repeat '─' "$lw")" "$C0"
  printf '%*s%b%s%b\n' "$tp1" '' "$CB$CC" "$tag1" "$C0"
  printf '%*s%b%s%b\n' "$tp2" '' "$CD" "$tag2" "$C0"
}

# Clear to a fresh screen (only when stdout is a real terminal) and reprint the banner. Called on
# entry to each level so drilling down refreshes instead of scrolling. No-op clear under pipes/CI.
screen() { [ -t 1 ] && { clear 2>/dev/null || true; }; banner; }

# ── prompts (all read from fd 3 = the real terminal) ──
# ask/ask_secret return 1 if Esc is pressed as the first key, so callers can offer "Esc to cancel".
ask() {  # ask <var> <prompt> [default]
  local __v=$1 __p=$2 __d=${3:-} __a __c
  if [ -n "$__d" ]; then printf '%b%s%b %b[%s]%b ' "$CQ" "$__p" "$C0" "$CD" "$__d" "$C0"
  else printf '%b%s%b ' "$CQ" "$__p" "$C0"; fi
  IFS= read -rsn1 __c <&3 || { printf '\n'; return 1; }
  case "$__c" in
    $'\033') printf '\n'; return 1 ;;                       # Esc → cancel
    ''|$'\n'|$'\r') printf '\n'; __a=$__d ;;                # Enter → default
    *) printf '%s' "$__c"; IFS= read -r __a <&3 || __a=''; __a="$__c$__a" ;;
  esac
  printf -v "$__v" '%s' "$__a"
}
ask_secret() {  # ask_secret <var> <prompt>
  local __v=$1 __p=$2 __a __c
  printf '%b%s%b ' "$CQ" "$__p" "$C0"
  IFS= read -rsn1 __c <&3 || { printf '\n'; return 1; }
  case "$__c" in
    $'\033') printf '\n'; return 1 ;;                       # Esc → cancel
    ''|$'\n'|$'\r') __a='' ;;                               # Enter → empty (keep current)
    *) IFS= read -rs __a <&3 || __a=''; __a="$__c$__a" ;;   # -s: never echo a secret
  esac
  printf '\n'
  printf -v "$__v" '%s' "$__a"
}
confirm() {  # confirm <prompt> [y|n default] -> 0/1
  local __p=$1 __d=${2:-n} __a __h
  [ "$__d" = y ] && __h='[Y/n]' || __h='[y/N]'
  printf '%b%s%b %s ' "$CQ" "$__p" "$C0" "$__h"
  IFS= read -r __a <&3 || __a=''
  [ -z "$__a" ] && __a=$__d
  case "$(printf '%s' "$__a" | tr '[:upper:]' '[:lower:]')" in y|yes) return 0 ;; *) return 1 ;; esac
}

run_cmd() {  # print, then run (unless OPP_DRY=1 or the binary is absent)
  printf '  %b$ %s%b\n' "$CD" "$*" "$C0"
  [ "$OPP_DRY" = 1 ] && return 0
  if ! have "$1"; then
    warn "'$1' not on PATH — run the command above where '$1' is installed"
    return 127
  fi
  local rc
  "$@"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "ok"
    return 0
  fi
  warn "command failed (exit $rc) — you can run it manually"
  return "$rc"
}

default_cache_dir() {
  case "$(uname -s)" in
    Darwin)                printf '%s' "$HOME/Library/Caches/open-pharma-plugins" ;;
    MINGW*|MSYS*|CYGWIN*)  printf '%s' "${LOCALAPPDATA:-$HOME/AppData/Local}/open-pharma-plugins/cache" ;;
    *)                     printf '%s' "${XDG_CACHE_HOME:-$HOME/.cache}/open-pharma-plugins" ;;
  esac
}

# Update-or-append one KEY=VALUE in the config file (bash 3.2 safe — no assoc arrays), 0600.
set_kv() {  # set_kv KEY VALUE
  local key=$1 val=$2 tmp
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  if [ ! -f "$CONFIG_FILE" ]; then
    printf '# open-pharma-plugins config — KEY=VALUE per line, read when the var is not in the environment.\n\n' > "$CONFIG_FILE"
  fi
  tmp=$(mktemp "${TMPDIR:-/tmp}/opp.XXXXXX")
  grep -v -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$CONFIG_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
}

# get_kv KEY → the value KEY is set to in the config file (empty if unset / no file). Strips a
# leading `export ` and surrounding quotes, matching shared.env's dotenv parse.
get_kv() {  # get_kv KEY
  [ -f "$CONFIG_FILE" ] || return 0
  local line val
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$CONFIG_FILE" 2>/dev/null | tail -1)
  [ -z "$line" ] && return 0
  val=${line#*=}
  case "$val" in
    \"*\") val=${val#\"}; val=${val%\"} ;;
    \'*\') val=${val#\'}; val=${val%\'} ;;
  esac
  printf '%s' "$val"
}

# del_kv KEY → remove any line setting KEY from the config file (preserves 0600). Clearing must
# delete the line, not write KEY= — an empty value would shadow a real default (see shared.env).
del_kv() {  # del_kv KEY
  [ -f "$CONFIG_FILE" ] || return 0
  local tmp; tmp=$(mktemp "${TMPDIR:-/tmp}/opp.XXXXXX")
  grep -v -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$CONFIG_FILE" > "$tmp" 2>/dev/null || true
  mv "$tmp" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
}

# cfg_raw KEY → the value that would win at runtime (environment first, then config file); empty if
# unset. Mirrors get_env precedence so the editor shows what actually takes effect.
cfg_raw() {  # cfg_raw KEY
  local v; eval "v=\${$1-}"
  [ -n "$v" ] && { printf '%s' "$v"; return; }
  get_kv "$1"
}

# cfg_display KEY SECRET DEFAULT → a PLAIN (no color — safe inside menu_pick items, which pad by raw
# length) current-value cell: masked for secrets, `(env)`-tagged when the environment overrides the
# file, `default: X` when unset but a default exists, `not set` when unset with no default, truncated.
cfg_display() {  # cfg_display KEY SECRET DEFAULT
  local key=$1 secret=$2 default=$3 env_val file_val val
  eval "env_val=\${$key-}"
  file_val=$(get_kv "$key")
  if [ -n "$env_val" ]; then val=$env_val
  elif [ -n "$file_val" ]; then val=$file_val
  elif [ -n "$default" ]; then printf 'default: %s' "$(_fit "$default" 21)"; return
  else printf 'not set'; return; fi
  if [ "$secret" = 1 ]; then
    if [ ${#val} -gt 9 ]; then val="${val:0:3}...${val: -2}"; else val='set'; fi
  fi
  val=$(_fit "$val" 30)
  [ -n "$env_val" ] && printf '%s (env)' "$val" || printf '%s' "$val"
}

status() {
  box_open "status"
  local pad=$(( _BOX_W - 22 ))                          # columns left for the trailing value
  local uvs=no; have uvx && uvs=ok
  box_row "$(mark $uvs) uv / uvx            ${CD}$(_fit "$(uvx --version 2>/dev/null || echo 'not found — needed to launch servers')" "$pad")${C0}"
  local cst=no; [ -f "$CONFIG_FILE" ] && cst=ok
  box_row "$(mark $cst) config file         ${CD}$(_fit "$CONFIG_FILE" "$pad")${C0}"
  local hs=''; for h in $ALL_HARNESSES; do have "$(harness_bin "$h")" && hs="$hs $h"; done
  box_row "$(mark $([ -n "$hs" ] && echo ok || echo no)) harnesses           ${CD}$(_fit "${hs:- none detected}" "$pad")${C0}"
  box_close
}

ensure_uv() {
  have uvx && return 0
  warn "uv / uvx not found — the MCP servers launch via 'uvx'."
  err "Install uv using a reviewed method from https://docs.astral.sh/uv/getting-started/installation/"
  return 1
}

# ── the ONE place the uvx launch spec is built — verify / manual all reuse it ──
# cap_ref <cap> → explicit OPP_REF when supplied, otherwise that capability's immutable latest tag.
# cap_spec <cap> → the package the harness itself also launches with. A local OPP_REPO is a
# PEP-508 file URL (no meaningless git ref); remote repositories use cap_ref.
is_local_repo() {
  case "$1" in file://*) return 0 ;; esac
  [ -d "$1" ]
}

cap_version() {
  local cap=$1 i
  for ((i = 0; i < ${#CAP_ITEMS[@]}; i++)); do
    [ "${CAP_ITEMS[$i]}" = "$cap" ] && { printf '%s' "${CAP_VERSIONS[$i]}"; return 0; }
  done
  printf 'unknown capability: %s\n' "$cap" >&2
  return 1
}

cap_ref() {
  local cap=$1 version
  [ -n "$REPO_REF" ] && { printf '%s' "$REPO_REF"; return 0; }
  version=$(cap_version "$cap") || return 1
  printf 'open-pharma-plugins-%s-v%s' "$cap" "$version"
}

marketplace_source() {
  local repo=$REPO_URL
  { is_local_repo "$repo" || [ -z "$REPO_REF" ]; } && { printf '%s' "$repo"; return 0; }
  case "$repo" in
    http://*|https://*|git@*) printf '%s#%s' "$repo" "$REPO_REF" ;;
    *) printf '%s@%s' "$repo" "$REPO_REF" ;;
  esac
}

# local_checkout_root → repository root containing this script. `install.sh local` cannot run via
# curl|bash because the checkout is both the marketplace and the Python package source.
local_checkout_root() {
  local script_path=${BASH_SOURCE[0]:-} root
  [ -n "$script_path" ] && [ -f "$script_path" ] || return 1
  root=$(cd "$(dirname "$script_path")" 2>/dev/null && pwd -P) || return 1
  [ -f "$root/pyproject.toml" ] && [ -d "$root/src/capabilities" ] || return 1
  printf '%s' "$root"
}

# Parse local/remote marketplace state before replacing a configured source. Removing a marketplace
# can remove its plugins, so local mode preselects installed capabilities and reinstalls them below.
marketplace_root_from_list() {
  local name=$1
  awk -v wanted="$name" '$1 == wanted { sub(/^[^[:space:]]+[[:space:]]+/, ""); print; exit }'
}

claude_marketplace_root_from_list() {
  local name=$1
  awk -v wanted="$name" '
    $2 == wanted { found=1; next }
    found && /Source: Directory \(/ {
      sub(/^.*Source: Directory \(/, ""); sub(/\)[[:space:]]*$/, ""); print; exit
    }
    found && /Source:/ { print "remote"; exit }
  '
}

codex_marketplace_source_from_json() {
  local name=$1
  awk -v wanted="$name" '
    $0 ~ "\"name\"[[:space:]]*:[[:space:]]*\"" wanted "\"" { found=1; next }
    found && /"sourceType"[[:space:]]*:/ {
      if ($0 ~ /"git"/) { print "remote"; exit }
      if ($0 ~ /"local"/) local_source=1
      next
    }
    found && local_source && /"source"[[:space:]]*:/ {
      sub(/^.*"source"[[:space:]]*:[[:space:]]*"/, ""); sub(/".*$/, ""); print; exit
    }
  '
}

copilot_marketplace_root_from_list() {
  local name=$1
  awk -v wanted="$name" '
    index($0, wanted " (") {
      if (match($0, /\((Local|Directory|Path): /)) {
        sub(/^.*\((Local|Directory|Path): /, ""); sub(/\)[[:space:]]*$/, ""); print; exit
      }
      print "remote"; exit
    }
  '
}

# configured_marketplace_source <harness> → local root, "remote", or empty when unknown/missing.
# Used only as a safety check before a stable update: supported harnesses can retain a local
# marketplace configured by `install.sh local`, in which case their native update verb keeps using
# working-tree code rather than the published tags shown in this installer's release index.
configured_marketplace_source() {
  local h=$1 bin out; bin=$(harness_bin "$h")
  have "$bin" || return 0
  case "$h" in
    claude)
      out=$("$bin" plugin marketplace list 2>/dev/null) || true
      printf '%s\n' "$out" | claude_marketplace_root_from_list "$MARKETPLACE" ;;
    codex)
      out=$("$bin" plugin marketplace list --json 2>/dev/null) || true
      printf '%s\n' "$out" | codex_marketplace_source_from_json "$MARKETPLACE" ;;
    copilot)
      out=$("$bin" plugin marketplace list 2>/dev/null) || true
      printf '%s\n' "$out" | copilot_marketplace_root_from_list "$MARKETPLACE" ;;
  esac
}

# Run the stdlib-only source rewriter with Python when available, or isolated uv as a fallback.
rewrite_plugin_sources() {
  local root=$1 mode=$2 py=python3
  shift 2
  if ! have python3; then py=uv; fi
  if [ "$py" = python3 ]; then
    python3 "$root/scripts/rewrite_plugin_sources.py" --repo "$root" "$mode" "$@"
  else
    uv run --no-project --isolated --offline python \
      "$root/scripts/rewrite_plugin_sources.py" --repo "$root" "$mode" "$@"
  fi
}

# Switch selected catalog entries to checkout-relative paths, point MCP package specs at
# file://<repo>, and force uvx to refresh so a reconnect observes the current checkout.
localize_plugin_sources() {
  local plugin cap root
  local -a caps=()
  root=${LOCAL_REPO_ROOT:-${REPO_URL#file://}}
  root=$(cd "$root" 2>/dev/null && pwd -P) || return 1
  for plugin in "$@"; do
    cap=${plugin#open-pharma-plugins-}
    caps+=("$cap")
  done
  [ ${#caps[@]} -gt 0 ] || return 0
  rewrite_plugin_sources "$root" --refresh "${caps[@]}"
}

cap_spec() {
  local cap=$1 repo=$REPO_URL path ref
  case "$repo" in
    file://*) printf 'open-pharma-plugins[%s] @ %s' "$cap" "$repo" ;;
    *)
      if [ -d "$repo" ]; then
        path=$(cd "$repo" 2>/dev/null && pwd -P) || return 1
        path=${path//%/%25}; path=${path// /%20}; path=${path//#/%23}
        printf 'open-pharma-plugins[%s] @ file://%s' "$cap" "$path"
      else
        case "$repo" in
          /*|./*|../*) printf 'OPP_REPO is not a directory: %s\n' "$repo" >&2; return 1 ;;
          git+*) ref=$(cap_ref "$cap") || return 1; printf 'open-pharma-plugins[%s] @ %s@%s' "$cap" "$repo" "$ref" ;;
          *) ref=$(cap_ref "$cap") || return 1; printf 'open-pharma-plugins[%s] @ git+%s@%s' "$cap" "$repo" "$ref" ;;
        esac
      fi
      ;;
  esac
}

# uvx_cap <cap> [uvx-flags...] -- [entry-args...] — print, then run the capability's uvx entry.
# Honors OPP_DRY (print only). Returns uvx's real exit code (0 in dry mode). bash-3.2 safe: never
# expands an empty array under `set -u`.
uvx_cap() {
  local cap=$1; shift
  local -a flags=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do flags+=("$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  local disp=""; [ ${#flags[@]} -gt 0 ] && disp="${flags[*]} "
  local edisp=""; [ $# -gt 0 ] && edisp=" $*"
  printf '  %b$ uvx %s--from "%s" open-pharma-plugins-%s%s%b\n' "$CD" "$disp" "$(cap_spec "$cap")" "$cap" "$edisp" "$C0"
  [ "$OPP_DRY" = 1 ] && return 0
  if [ ${#flags[@]} -gt 0 ]; then
    uvx "${flags[@]}" --from "$(cap_spec "$cap")" "open-pharma-plugins-${cap}" "$@"
  else
    uvx --from "$(cap_spec "$cap")" "open-pharma-plugins-${cap}" "$@"
  fi
}

install_for() {  # install_for <harness> <plugin...>
  local h=$1; shift
  local bin; bin=$(harness_bin "$h")
  local failed=0 prompt="Run the $h install commands now (otherwise just print them)?"
  is_local_repo "$REPO_URL" && prompt="Install the selected plugins from this checkout into $h now?"
  OPP_DRY=0; confirm "$prompt" y || OPP_DRY=1
  if is_local_repo "$REPO_URL" && [ "$OPP_DRY" = 0 ]; then
    localize_plugin_sources "$@" || { err "could not prepare the local plugin manifests"; return 1; }
  fi
  case "$h" in
    claude)
      # Claude rejects a duplicate add; update is the normal path for an existing marketplace.
      if is_local_repo "$REPO_URL"; then
        local claude_root claude_list desired_claude_root
        claude_list=$("$bin" plugin marketplace list 2>/dev/null) || true
        claude_root=$(printf '%s\n' "$claude_list" | claude_marketplace_root_from_list "$MARKETPLACE")
        desired_claude_root=$(cd "${REPO_URL#file://}" 2>/dev/null && pwd -P) || return
        if [ -n "$claude_root" ] && [ "$claude_root" != "$desired_claude_root" ]; then
          warn "switching $MARKETPLACE from $claude_root to this checkout"
          run_cmd "$bin" plugin marketplace remove "$MARKETPLACE" || return
        fi
        run_cmd "$bin" plugin marketplace add "$desired_claude_root" || return
      else
        run_cmd "$bin" plugin marketplace add "$(marketplace_source)" ||
          run_cmd "$bin" plugin marketplace update "$MARKETPLACE" || return
      fi
      for p in "$@"; do run_cmd "$bin" plugin install "${p}@${MARKETPLACE}" || failed=1; done ;;
    codex)
      # add is idempotent, but on an ALREADY-added marketplace it does NOT refresh the git snapshot,
      # so newly-published capabilities would be missing and their `plugin add` would fail. `upgrade`
      # re-pulls the snapshot (no-op right after a fresh add). We refresh instead of remove→add because
      # removing a marketplace also drops every plugin already installed from it, including ones not
      # reselected this run. Stop on a failed prerequisite so the UI cannot report a false success.
      if is_local_repo "$REPO_URL"; then
        local current_root desired_root list_out
        list_out=$("$bin" plugin marketplace list 2>/dev/null) || true
        current_root=$(printf '%s\n' "$list_out" | marketplace_root_from_list "$MARKETPLACE")
        desired_root=$(cd "${REPO_URL#file://}" 2>/dev/null && pwd -P) || return
        if [ -n "$current_root" ] && [ "$current_root" != "$desired_root" ]; then
          warn "switching $MARKETPLACE from $current_root to this checkout"
          run_cmd "$bin" plugin marketplace remove "$MARKETPLACE" || return
        fi
        run_cmd "$bin" plugin marketplace add "$desired_root" || return
      elif [ -n "$REPO_REF" ]; then
        run_cmd "$bin" plugin marketplace add "$REPO_URL" --ref "$REPO_REF" || return
      else
        run_cmd "$bin" plugin marketplace add "$REPO_URL" || return
      fi
      # Local marketplaces read the checkout directly and cannot be upgraded as Git snapshots.
      is_local_repo "$REPO_URL" || run_cmd "$bin" plugin marketplace upgrade "$MARKETPLACE" || return
      for p in "$@"; do run_cmd "$bin" plugin add "${p}@${MARKETPLACE}" || failed=1; done ;;
    copilot)
      if is_local_repo "$REPO_URL"; then
        local copilot_list copilot_root desired_copilot_root
        copilot_list=$("$bin" plugin marketplace list 2>/dev/null) || true
        copilot_root=$(printf '%s\n' "$copilot_list" | copilot_marketplace_root_from_list "$MARKETPLACE")
        desired_copilot_root=$(cd "${REPO_URL#file://}" 2>/dev/null && pwd -P) || return
        if [ "$copilot_root" != "$desired_copilot_root" ]; then
          if [ -n "$copilot_root" ]; then
            warn "switching $MARKETPLACE from $copilot_root to this checkout"
            run_cmd "$bin" plugin marketplace remove "$MARKETPLACE" || return
          fi
          run_cmd "$bin" plugin marketplace add "$desired_copilot_root" || return
        fi
      else
        run_cmd "$bin" plugin marketplace add "$(marketplace_source)" ||
          run_cmd "$bin" plugin marketplace update "$MARKETPLACE" || return
      fi
      for p in "$@"; do run_cmd "$bin" plugin install "${p}@${MARKETPLACE}" || failed=1; done ;;
    *)
      err "unsupported harness '$h' — supported harnesses: $ALL_HARNESSES"
      return 1 ;;
  esac
  [ "$failed" -eq 0 ]
}

# update_for <harness> <plugin...> — refresh the release catalog and update already-installed
# capabilities to the stable refs embedded above. Claude and Copilot use their native update verbs.
# Codex has no plugin-update verb, but `plugin add` is idempotent and refreshes the installed cache
# after `marketplace upgrade`.
update_for() {
  local h=$1; shift
  local bin; bin=$(harness_bin "$h")
  local p failed=0 source prompt="Update the selected plugins in $h now (otherwise just print the commands)?"
  [ -n "$REPO_REF" ] && {
    err "update uses the current stable catalog; use OPP_REF=<tag> bash install.sh install for rollback"
    return 1
  }
  is_local_repo "$REPO_URL" && {
    err "update targets published stable releases; use 'bash install.sh local' for a checkout"
    return 1
  }
  case "$h" in
    claude|codex|copilot)
      source=$(configured_marketplace_source "$h")
      if [ -n "$source" ] && [ "$source" != remote ] && is_local_repo "$source"; then
        err "$h currently uses the local marketplace at $source"
        warn "to return to stable releases, uninstall those local plugins in $h, then run Install plugin"
        return 1
      fi ;;
  esac
  OPP_DRY=0; confirm "$prompt" y || OPP_DRY=1
  case "$h" in
    claude)
      run_cmd "$bin" plugin marketplace update "$MARKETPLACE" || return
      for p in "$@"; do run_cmd "$bin" plugin update "${p}@${MARKETPLACE}" || failed=1; done ;;
    codex)
      run_cmd "$bin" plugin marketplace upgrade "$MARKETPLACE" || return
      for p in "$@"; do run_cmd "$bin" plugin add "${p}@${MARKETPLACE}" || failed=1; done ;;
    copilot)
      run_cmd "$bin" plugin marketplace update "$MARKETPLACE" || return
      for p in "$@"; do run_cmd "$bin" plugin update "$p" || failed=1; done ;;
    *)
      err "unsupported harness '$h' — supported harnesses: $ALL_HARNESSES"
      return 1 ;;
  esac
  [ "$failed" -eq 0 ]
}

# Updating the installed files and activating them in an already-running harness are separate
# steps. Native plugin managers replace the complete plugin bundle (skill + MCP where present).
# Tell the user how to refresh the in-memory inventory without pretending the MCP pre-build below
# can verify a live session.
post_update_hint() {
  case "$1" in
    claude)    warn "already-open Claude Code session: run /reload-plugins (or restart it)" ;;
    codex)     warn "already-open Codex task: start a new task (or restart Codex) to load the updated plugin" ;;
    copilot)   warn "already-open GitHub Copilot session: run /restart or restart Copilot to load the updated plugin" ;;
  esac
}

# _detect_mask <harness> → echoes a 0/1 mask (installed?), one char per MP_ITEMS entry.
# ONE `list` call per harness (the node CLIs are slow to spawn, so we avoid one call per capability),
# matched against the captured output. Verified per harness on this repo:
#   - claude `plugin list` shows installed plugins as "<name>@<marketplace>"
#   - codex `plugin list` lists EVERY marketplace plugin with a status column → installed iff the row isn't "not installed"
#   - copilot `plugin list` lists installed plugins by name (and may include their marketplace)
# Output is captured before matching so codex's non-zero exit (101) can't trip pipefail; any
# uncertainty (CLI absent/offline/no match) yields 0, so nothing is ever falsely greyed.
_detect_mask() {
  local h=$1 bin out i id; bin=$(harness_bin "$h")
  have "$bin" || { printf '%0*d' "${#MP_ITEMS[@]}" 0; return; }
  case "$h" in
    claude|codex|copilot) out=$("$bin" plugin list 2>/dev/null) || true ;;
    *) printf '%0*d' "${#MP_ITEMS[@]}" 0; return ;;
  esac
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    id="open-pharma-plugins-${MP_ITEMS[$i]}"
    case "$h" in
      codex)    if printf '%s\n' "$out" | grep -F -- "${id}@${MARKETPLACE}" | grep -qv 'not installed'; then printf 1; else printf 0; fi ;;
      *)        printf '%s' "$out" | grep -qF -- "${id}@${MARKETPLACE}" && printf 1 || printf 0 ;;
    esac
  done
}

# ── selection widgets (shared by every list: main menu, harness pick, capability pick) ──
# All read keys from /dev/tty (fd 3) so they work under `curl | bash`, and fall back to a numbered
# prompt when stdout isn't a terminal or OPP_NO_TUI is set.

# How long to wait for an escape sequence's trailing bytes (arrow keys). bash 4+ only: a lone Esc is
# instant there (`read -t 0` in _read_key gates it), so this just bounds arrow decoding (0.1s). bash 3.2
# (macOS) has no `read -t 0` and no sub-second `read -t`, so in cbreak mode _read_key lets the TERMINAL
# time the read (VMIN/VTIME) instead of this value — bare Esc resolves in ~0.1s there too, not a second.
if [ "${BASH_VERSINFO:-0}" -ge 4 ]; then _ESC_T=0.1; else _ESC_T=1; fi

# _read_key → KEY = up | down | enter | space | all | back | cancel | other
_read_key() {
  local k s
  IFS= read -rsn1 -u 3 k || { KEY=enter; return; }
  [ -z "$k" ] && { KEY=enter; return; }                      # Enter (newline eaten by read -n1)
  case "$k" in
    $'\n'|$'\r') KEY=enter ;;
    ' ') KEY=space ;;
    a|A) KEY=all ;;
    j|J) KEY=down ;;
    k|K) KEY=up ;;
    q|Q) KEY=cancel ;;
    $'\033')
      # Arrow keys send their trailing bytes right after the Esc; a lone Esc sends none. Decode them
      # without blocking on a bare Esc — the tricky part on macOS's bash 3.2:
      #   • bash 4+ — `read -t 0` tests for buffered bytes WITHOUT consuming (bare Esc → instant), then
      #     a fractional-timeout read grabs the arrow's "[A".
      #   • bash 3.2 in cbreak mode — no `read -t 0` and no sub-second `read -t`, so let the TERMINAL
      #     time it: VMIN 0 / VTIME 0.1s + `dd` for the 2 trailing bytes. (`read -n` would reset our
      #     termios; `dd` doesn't, and its fixed count leaves an autorepeat burst buffered for the next
      #     key.) Bare Esc → ~0.1s instead of a full second; arrows stay instant. Empty ⇒ bare Esc.
      s=''
      if [ "${BASH_VERSINFO:-0}" -ge 4 ]; then
        read -t 0 -u 3 2>/dev/null && IFS= read -rsn2 -t "$_ESC_T" -u 3 s
      elif [ -n "$_TTY_SAVED" ]; then
        stty min 0 time 1 <&3 2>/dev/null
        s=$(dd bs=1 count=2 <&3 2>/dev/null)
        stty min 1 time 0 <&3 2>/dev/null
      else
        IFS= read -rsn2 -t "$_ESC_T" -u 3 s || s=''
      fi
      case "$s" in '[A'|'[D') KEY=up ;; '[B'|'[C') KEY=down ;; '') KEY=back ;; *) KEY=other ;; esac ;;
    *) KEY=other ;;
  esac
}

_tty_ui() { [ -t 1 ] && [ -z "${OPP_NO_TUI:-}" ]; }

# Put the tty (fd 3) into cbreak mode — non-canonical, no echo, deliver each byte immediately — so the
# arrow-key menus below react to a keystroke at once instead of waiting for Enter. This is essential
# under `curl … | bash`: stdin is the pipe (not a tty), and bash's own `read -n1` raw-mode handling
# doesn't reliably reach fd 3 there, leaving the tty line-buffered (arrows dead, only Enter flushes).
# tty_cooked restores the saved settings. Both no-op unless we're driving an interactive tty; text
# prompts (ask/ask_secret/confirm) deliberately stay in cooked mode — they read whole lines.
tty_cbreak() {
  _tty_ui || return 0
  _TTY_SAVED=$(stty -g <&3 2>/dev/null) || _TTY_SAVED=''
  [ -n "$_TTY_SAVED" ] && stty -icanon -echo min 1 time 0 <&3 2>/dev/null
}
tty_cooked() { [ -n "$_TTY_SAVED" ] && stty "$_TTY_SAVED" <&3 2>/dev/null; _TTY_SAVED=''; }

# Hold a read-only / report screen on-screen until a key is pressed (interactive only). Without it,
# such screens flash away the instant they return, since the menu reclears on the next loop.
pause() { _tty_ui && { printf '\n  %bpress any key to return%b' "$CD" "$C0"; tty_cbreak; _read_key; tty_cooked; }; return 0; }

# menu_pick <title> <item...> → PICK_I (index, -1 = cancelled) and PICK (value)
menu_pick() {
  local title=$1; shift
  local items=("$@") n=$# cur=0 i ans w=0 cols item_w
  local -a shown=()
  if _tty_ui; then
    printf '\n  %b%s%b  %b(↑/↓ · enter · esc/q back)%b\n\n' "$CB" "$title" "$C0" "$CD" "$C0"
    printf '\033[?25l'; tty_cbreak; trap 'printf "\033[?25h"; tty_cooked' RETURN
    while :; do
      # Reserve six visible columns for indentation, pointer and highlighted-row padding. Recompute
      # on every redraw so resizing an open menu also stays one physical line per logical row.
      cols=$(term_cols); item_w=$(( cols - 7 )); [ "$item_w" -lt 1 ] && item_w=1
      shown=(); w=0
      for ((i = 0; i < n; i++)); do
        shown[i]=$(_fit "${items[$i]}" "$item_w")
        [ ${#shown[$i]} -gt "$w" ] && w=${#shown[$i]}
      done
      for ((i = 0; i < n; i++)); do
        if [ "$i" != "$cur" ]; then printf '\033[2K    %s\n' "${shown[$i]}"
        elif [ "$OPP_TRUECOLOR" = 1 ]; then    # dark text on a bright cyan bar
          printf '\033[2K  \033[1;38;2;12;18;32;48;2;0;210;255m ❯ %-*s \033[0m\n' "$w" "${shown[$i]}"
        elif [ -n "$CC" ]; then                # 16-color: reverse-video bar
          printf '\033[2K  %b%b\033[7m ❯ %-*s \033[0m\n' "$CB" "$CC" "$w" "${shown[$i]}"
        else printf '\033[2K  ❯ %s\n' "${shown[$i]}"; fi
      done
      _read_key
      case "$KEY" in
        up)          cur=$(( (cur - 1 + n) % n )) ;;
        down)        cur=$(( (cur + 1) % n )) ;;
        enter)       break ;;
        back|cancel) cur=-1; break ;;
      esac
      printf '\033[%dA' "$n"
    done
    tty_cooked; printf '\033[?25h'; trap - RETURN
  else
    printf '\n  %s\n' "$title"
    for ((i = 0; i < n; i++)); do printf '    %d) %s\n' $((i + 1)) "${items[$i]}"; done
    ask ans "  ›" "1"
    case "$ans" in
      ''|*[!0-9]*) cur=-1 ;;
      *) if [ "$ans" -ge 1 ] && [ "$ans" -le "$n" ]; then cur=$((ans - 1)); else cur=-1; fi ;;
    esac
  fi
  PICK_I=$cur
  [ "$cur" -ge 0 ] && PICK="${items[$cur]}" || PICK=''
}

# _multi_rows <cur> — render MP_ITEMS/MP_DESC/MP_SEL/MP_DIS (cur=-1 → num mode: no pointer / no clear)
_multi_rows() {
  local cur=$1 i box ptr num body clr='' cols desc_w name_w desc name
  [ "$cur" != -1 ] && clr='\033[2K'
  cols=$(term_cols); desc_w=$(( cols - 26 ))
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    num=$((i + 1)); [ "$i" = "$cur" ] && ptr="${CB}${CC}❯${C0}" || ptr=' '
    if [ "$cols" -lt 27 ]; then
      # At very small widths omit the description and spend the remaining columns on the name.
      name_w=$(( cols - 12 )); [ "$name_w" -lt 1 ] && name_w=1
      name=$(_fit "${MP_ITEMS[$i]}" "$name_w")
      if [ "${MP_DIS[$i]}" = 1 ]; then
        body=$(printf '%b[-] %d) %s%b' "$CD" "$num" "$name" "$C0")
      else
        [ "${MP_SEL[$i]}" = 1 ] && box="${CG}[✓]${C0}" || box='[ ]'
        body=$(printf '%s %d) %s' "$box" "$num" "$name")
      fi
    else
      local num_w=${#num}; local fixed=$(( 7 + num_w + 1 ))  # "[✓] N) " + space before desc
      local avail=$(( cols - fixed - 4 ))  # 4 = prefix "  ❯ "
      local nw=13; [ "$avail" -lt 14 ] && nw=$avail  # cap name at available space
      name=$(_fit "${MP_ITEMS[$i]}" "$nw")
      desc_w=$(( cols - fixed - nw - 4 ))
      [ "$desc_w" -lt 1 ] && desc_w=1
      desc=$(_fit "${MP_DESC[$i]}" "$desc_w")
      if [ "${MP_DIS[$i]}" = 1 ]; then
        body=$(printf '%b[-] %d) %-*s %s%b' "$CD" "$num" "$nw" "$name" "$desc" "$C0")
      else
        [ "${MP_SEL[$i]}" = 1 ] && box="${CG}[✓]${C0}" || box='[ ]'
        body=$(printf '%s %d) %-*s %b%s%b' "$box" "$num" "$nw" "$name" "$CD" "$desc" "$C0")
      fi
    fi
    printf '%b  %s %s\n' "$clr" "$ptr" "$body"
  done
}

# multi_pick <title> — toggles MP_SEL[] over MP_ITEMS[] (MP_DIS[]=1 rows locked).
# Sets MP_STATUS = ok (enter) | back (Esc) | cancel (q).
multi_pick() {
  local title=$1 n=${#MP_ITEMS[@]} cur=0 i ans tok
  MP_STATUS=ok
  if _tty_ui; then
    printf '\n  %b%s%b\n  %b↑/↓ move · space toggle · a all · enter confirm · esc back · q quit%b\n\n' "$CB" "$title" "$C0" "$CD" "$C0"
    printf '\033[?25l'; tty_cbreak; trap 'printf "\033[?25h"; tty_cooked' RETURN
    while :; do
      _multi_rows "$cur"
      _read_key
      case "$KEY" in
        up)     cur=$(( (cur - 1 + n) % n )) ;;
        down)   cur=$(( (cur + 1) % n )) ;;
        space)  [ "${MP_DIS[$cur]}" = 1 ] || MP_SEL[$cur]=$(( 1 - ${MP_SEL[$cur]} )) ;;
        all)    for ((i = 0; i < n; i++)); do [ "${MP_DIS[$i]}" = 1 ] || MP_SEL[$i]=1; done ;;
        enter)  break ;;
        back)   MP_STATUS=back; break ;;
        cancel) MP_STATUS=cancel; break ;;
      esac
      printf '\033[%dA' "$n"
    done
    tty_cooked; printf '\033[?25h'; trap - RETURN
  else
    while :; do
      printf '\n  %b%s%b  %b([✓] on · [ ] off · [-] locked)%b\n\n' "$CB" "$title" "$C0" "$CD" "$C0"
      _multi_rows -1
      printf '\n  %bToggle by number (e.g. "1 3"), Enter to confirm%b\n' "$CD" "$C0"
      ask ans "  ›" ""
      [ -z "$ans" ] && break
      for tok in $ans; do
        case "$tok" in
          [1-9]) i=$((tok - 1))
                 if [ "$i" -ge "$n" ]; then warn "ignored: $tok"
                 elif [ "${MP_DIS[$i]}" = 1 ]; then warn "${MP_ITEMS[$i]} locked — skipped"
                 else MP_SEL[$i]=$(( 1 - ${MP_SEL[$i]} )); fi ;;
          *) warn "ignored: $tok" ;;
        esac
      done
    done
  fi
}

# load_caps <presel> [descmode] — populate MP_ITEMS/MP_DESC/MP_SEL/MP_DIS from the catalog.
#   presel:   "hcp-intelligence" preselects only hcp-intelligence; anything else leaves everything unselected.
#   descmode: "entry" → plugin id; "local" → local checkout; else stable version + blurb.
load_caps() {
  local presel=${1:-} descmode=${2:-} i
  MP_ITEMS=("${CAP_ITEMS[@]}"); MP_DESC=(); MP_SEL=(); MP_DIS=()
  for ((i = 0; i < ${#CAP_ITEMS[@]}; i++)); do
    case "$descmode" in
      entry) MP_DESC[i]="→ open-pharma-plugins-${CAP_ITEMS[$i]}" ;;
      local) MP_DESC[i]="local checkout · ${CAP_DESC[$i]}" ;;
      *)     MP_DESC[i]="v${CAP_VERSIONS[$i]} · ${CAP_DESC[$i]}" ;;
    esac
    MP_DIS[i]=0
    { [ "$presel" = hcp-intelligence ] && [ "${CAP_ITEMS[$i]}" = hcp-intelligence ]; } && MP_SEL[i]=1 || MP_SEL[i]=0
  done
}

# choose_caps <harness> → SELECTED_PLUGINS (installed caps rendered [-] and pre-excluded)
choose_caps() {
  local h=$1 i mask=""
  load_caps hcp-intelligence
  spin "checking installed plugins in ${h}..." mask -- _detect_mask "$h"
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    if [ "${mask:i:1}" = 1 ]; then MP_DIS[$i]=1; MP_SEL[$i]=0; MP_DESC[$i]="already installed"; fi
  done
  multi_pick "Select capabilities for $h"
  SELECTED_PLUGINS=""
  [ "$MP_STATUS" != ok ] && return 0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    [ "${MP_SEL[$i]}" = 1 ] && SELECTED_PLUGINS="$SELECTED_PLUGINS open-pharma-plugins-${MP_ITEMS[$i]}"
  done
}

# Source switching may remove an existing marketplace and its plugins. Keep installed capabilities
# selected so local mode reinstalls them instead of silently dropping them.
choose_caps_local() {
  local h=$1 i mask=""
  load_caps hcp-intelligence local
  spin "checking installed plugins in $h..." mask -- _detect_mask "$h"
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    if [ "${mask:i:1}" = 1 ]; then
      MP_SEL[i]=1
      MP_DESC[i]="installed; reinstall from local checkout"
    fi
  done
  multi_pick "Select capabilities for local $h install"
  SELECTED_PLUGINS=""
  [ "$MP_STATUS" != ok ] && return 0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    [ "${MP_SEL[$i]}" = 1 ] && SELECTED_PLUGINS="$SELECTED_PLUGINS open-pharma-plugins-${MP_ITEMS[$i]}"
  done
}

# Update operates only on capabilities already registered in this harness. Select all installed
# entries by default (the common "bring this harness current" action); users can deselect any of
# them. We show the target release, not a guessed current version — several harnesses do not expose
# installed versions in a stable machine-readable form.
choose_caps_update() {
  local h=$1 i mask="" any=0
  load_caps
  spin "checking installed plugins in $h..." mask -- _detect_mask "$h"
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    if [ "${mask:i:1}" = 1 ]; then
      any=1; MP_SEL[i]=1
      MP_DESC[i]="target v${CAP_VERSIONS[$i]} · ${CAP_DESC[$i]}"
    else
      MP_DIS[i]=1; MP_DESC[i]="not installed"
    fi
  done
  [ "$any" = 0 ] && { SELECTED_PLUGINS=''; MP_STATUS=ok; return 0; }
  multi_pick "Update installed capabilities in $h"
  SELECTED_PLUGINS=""
  [ "$MP_STATUS" != ok ] && return 0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    [ "${MP_SEL[$i]}" = 1 ] && SELECTED_PLUGINS="$SELECTED_PLUGINS open-pharma-plugins-${MP_ITEMS[$i]}"
  done
}

# detect_installed → prints space-separated cap names installed in ANY detected harness (union).
# One `list` per detected harness (see _detect_mask). Prints nothing if none installed / no harness.
# Used to auto-target verify at "whatever you actually installed, however you installed it".
detect_installed() {
  local h mask i out=""
  MP_ITEMS=("${CAP_ITEMS[@]}")                        # _detect_mask keys off MP_ITEMS
  local -a acc=()
  for ((i = 0; i < ${#CAP_ITEMS[@]}; i++)); do acc[i]=0; done
  for h in $ALL_HARNESSES; do
    have "$(harness_bin "$h")" || continue
    mask=$(_detect_mask "$h")
    for ((i = 0; i < ${#CAP_ITEMS[@]}; i++)); do [ "${mask:i:1}" = 1 ] && acc[i]=1; done
  done
  for ((i = 0; i < ${#CAP_ITEMS[@]}; i++)); do [ "${acc[i]}" = 1 ] && out="$out ${CAP_ITEMS[$i]}"; done
  printf '%s' "${out# }"
}

do_install() {
  local harness
  while :; do
    if is_local_repo "$REPO_URL"; then
      screen; hr "Install from local checkout"
    else
      screen; hr "Install plugin"
    fi
    menu_pick "Target harness" $ALL_HARNESSES
    [ "$PICK_I" -lt 0 ] && return 0                 # esc/q at top level → back to menu
    harness=$PICK
    require_harness "$harness" || continue
    ensure_uv || { pause; return 0; }
    screen; hr "Install → $harness"
    if is_local_repo "$REPO_URL"; then
      choose_caps_local "$harness"
    else
      choose_caps "$harness"
    fi
    case "$MP_STATUS" in
      back)   continue ;;                           # esc → back to harness pick
      cancel) return 0 ;;                           # q → back to menu
    esac
    [ -n "$SELECTED_PLUGINS" ] && break
    warn "nothing selected."
    pause
    return 0
  done
  screen; hr "Install → $harness"
  if ! install_for "$harness" $SELECTED_PLUGINS; then
    err "installation incomplete — one or more commands failed"
    pause
    return 1
  fi
  # First install of these caps → self-test once now. --check-system pre-builds each uvx env (so the
  # harness's first tool call isn't a cold multi-minute build) and reports missing system tools right
  # away. Skipped in dry-run (nothing was installed) and for skill-only caps (no MCP env to build).
  local p cap checked=0 check_failed=0
  if [ "$OPP_DRY" = 0 ] && have uvx; then
    for p in $SELECTED_PLUGINS; do
      cap=${p#open-pharma-plugins-}
      is_skill_only "$cap" && continue
      [ "$checked" = 0 ] && { hr "System check — pre-build uvx env & check system tools"; checked=1; }
      cap_divider "open-pharma-plugins-$cap"
      uvx_cap "$cap" -- --check-system || check_failed=1
    done
    if [ "$check_failed" = 1 ]; then
      err "installation commands completed, but one or more MCP environments failed to start"
      pause
      return 1
    fi
    printf '\n'; box_open "Installed → $harness"
    for p in $SELECTED_PLUGINS; do box_row "${CG}✓${C0} $p"; done
    box_close
  fi
  printf '\n'
  pause   # hold the install result on screen — the menu reclears the moment we return
}

do_local_install() {
  local root
  root=$(local_checkout_root) || {
    err "local install must run from a cloned open-pharma-plugins checkout"
    printf '  git clone https://github.com/PharmaGenAI/open-pharma-plugins.git\n'
    printf '  cd open-pharma-plugins && bash install.sh local\n'
    return 1
  }
  LOCAL_REPO_ROOT=$root
  REPO_URL=$root
  REPO_REF=''
  do_install
}

do_local_restore() {
  local root
  root=$(local_checkout_root) || {
    err "local restore must run from a cloned open-pharma-plugins checkout"
    return 1
  }
  rewrite_plugin_sources "$root" --restore all || {
    err "could not restore published plugin refs"
    return 1
  }
  ok "restored published plugin refs in $root"
}

do_update() {
  local harness bin p cap checked=0 check_failed=0
  while :; do
    screen; hr "Update installed plugins"
    menu_pick "Target harness" $ALL_HARNESSES
    [ "$PICK_I" -lt 0 ] && return 0
    harness=$PICK; bin=$(harness_bin "$harness")
    require_harness "$harness" || continue
    screen; hr "Update → $harness"
    choose_caps_update "$harness"
    case "$MP_STATUS" in
      back)   continue ;;
      cancel) return 0 ;;
    esac
    [ -n "$SELECTED_PLUGINS" ] && break
    warn "nothing installed or selected in $harness."
    pause
    return 0
  done

  screen; hr "Update → $harness"
  if ! update_for "$harness" $SELECTED_PLUGINS; then
    err "update incomplete — one or more commands failed"
    pause
    return 1
  fi

  # Validate the exact target package refs after the harness update. A missing uvx does not undo a
  # successful skill/plugin update; it only means the MCP pre-build check cannot run here.
  if [ "$OPP_DRY" = 0 ]; then
    if have uvx; then
      for p in $SELECTED_PLUGINS; do
        cap=${p#open-pharma-plugins-}
        is_skill_only "$cap" && continue
        [ "$checked" = 0 ] && { hr "System check — pre-build updated MCP envs"; checked=1; }
        cap_divider "open-pharma-plugins-$cap"
        uvx_cap "$cap" -- --check-system || check_failed=1
      done
    else
      warn "uvx not found — updated plugins, but skipped the MCP environment check"
    fi
    printf '\n'; box_open "Updated → $harness"
    for p in $SELECTED_PLUGINS; do
      cap=${p#open-pharma-plugins-}
      box_row "${CG}✓${C0} $p ${CD}→ v$(cap_version "$cap")${C0}"
    done
    box_close
    post_update_hint "$harness"
  fi
  [ "$check_failed" = 1 ] && err "plugins updated, but one or more MCP environments failed to start"
  pause
  [ "$check_failed" = 0 ]
}

edit_one() {  # edit_one KEY SECRET DEFAULT DESC — prompt for one setting; write, clear, or keep it
  local key=$1 secret=$2 default=$3 desc=$4 cur newv env_val
  cur=$(get_kv "$key")
  eval "env_val=\${$key-}"
  printf '\n  %b%s%b — %s\n' "$CB" "$key" "$C0" "$desc"
  [ -z "$env_val$cur" ] && [ -n "$default" ] && printf '  %bcurrently unset — default: %s%b\n' "$CD" "$default" "$C0"
  [ -n "$env_val" ] && printf '  %b! set in the environment — that overrides the config file until you unset it.%b\n' "$CY" "$C0"
  if [ "$secret" = 1 ]; then
    ask_secret newv "$key (blank = keep · '-' = clear):" || return 0
    case "$newv" in
      '')  printf '  %b(unchanged)%b\n' "$CD" "$C0" ;;
      '-') del_kv "$key"; ok "cleared $key" ;;
      *)   set_kv "$key" "$newv"; ok "saved $key" ;;
    esac
  else
    ask newv "$key (blank = keep · '-' = clear)" "$cur" || return 0
    if [ "$newv" = '-' ]; then del_kv "$key"; ok "cleared $key"
    elif [ -n "$newv" ] && [ "$newv" != "$cur" ]; then set_kv "$key" "$newv"; ok "saved $key"
    else printf '  %b(unchanged)%b\n' "$CD" "$C0"; fi
  fi
}

edit_group() {  # edit_group <group-tag> — list the group's settings and edit whichever is picked
  local g=$1 title; title=$(config_group_title "$g")
  while :; do
    screen; hr "Configure → $title"
    printf '  %bSelect a setting to edit · Esc/back returns to the group list.%b\n' "$CD" "$C0"
    local -a keys=() secs=() defs=() descs=() labels=()
    local spec k s grp def d
    for spec in "${CONFIG_SPEC[@]}"; do
      IFS='|' read -r k s grp def d <<<"$spec"
      [ "$grp" = "$g" ] || continue
      keys+=("$k"); secs+=("$s"); defs+=("$def"); descs+=("$d")
      labels+=("$(printf '%-26s %s' "$k" "$(cfg_display "$k" "$s" "$def")")")
    done
    menu_pick "$title" "${labels[@]}" "← back"
    local i=$PICK_I
    { [ "$i" -lt 0 ] || [ "$i" -ge "${#keys[@]}" ]; } && return 0     # Esc/q or "← back"
    edit_one "${keys[$i]}" "${secs[$i]}" "${defs[$i]}" "${descs[$i]}"
  done
}

do_configure() {  # do_configure [nested] — nested: invoked from another action, so skip the trailing pause
  local nested=${1:-}
  while :; do
    screen; hr "Configure — the whole config, grouped"
    printf '  Config file (fixed location): %b%s%b\n' "$CB" "$CONFIG_FILE" "$C0"
    printf '  %bRead by every harness when the var is not already in the environment — set once, works everywhere.%b\n' "$CD" "$C0"
    printf '  %bPick a group to browse & edit its settings; blank keeps a value, "-" clears it.%b\n' "$CD" "$C0"
    local -a gtags=() glabels=()
    local g spec k s grp def d set tot title
    for g in "${CONFIG_GROUPS[@]}"; do
      set=0; tot=0
      for spec in "${CONFIG_SPEC[@]}"; do
        IFS='|' read -r k s grp def d <<<"$spec"
        [ "$grp" = "$g" ] || continue
        tot=$((tot + 1)); [ -n "$(cfg_raw "$k")" ] && set=$((set + 1))
      done
      title=$(config_group_title "$g")
      gtags+=("$g"); glabels+=("$(printf '%-38s (%d/%d set)' "$title" "$set" "$tot")")
    done
    menu_pick "Edit which settings?" "${glabels[@]}" "Done"
    local i=$PICK_I
    { [ "$i" -lt 0 ] || [ "$i" -ge "${#gtags[@]}" ]; } && break         # Esc/q or "Done"
    edit_group "${gtags[$i]}"
  done
  [ -f "$CONFIG_FILE" ] && { printf '\n'; ok "Config saved → $CONFIG_FILE (chmod 600)"; }
  [ -n "$nested" ] || pause
}

do_verify() {
  screen; hr "Verify"
  [ -f "$CONFIG_FILE" ] && ok "config file present: $CONFIG_FILE" || warn "no config file yet — run Configure"
  local i any=0 installed passed=0 failed=0 skipped=0
  spin "detecting installed capabilities..." installed -- detect_installed
  load_caps "" entry
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do            # preselect whatever is already installed
    case " $installed " in *" ${MP_ITEMS[$i]} "*) MP_SEL[$i]=1 ;; esac
  done
  multi_pick "Self-test which capabilities (each fetched via uvx, checks system tools + config)"
  [ "$MP_STATUS" != ok ] && return 0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do [ "${MP_SEL[$i]}" = 1 ] && any=1; done
  [ "$any" = 0 ] && { printf '  (nothing selected)\n'; pause; return 0; }
  ensure_uv || { pause; return 0; }
  OPP_DRY=0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    [ "${MP_SEL[$i]}" = 1 ] || continue
    cap_divider "open-pharma-plugins-${MP_ITEMS[$i]}"
    if is_skill_only "${MP_ITEMS[$i]}"; then
      printf '  %bskill-only — no MCP server to check; verify its runtime requirements from SKILL.md.%b\n' "$CD" "$C0"
      skipped=$((skipped + 1))
      continue
    fi
    if uvx_cap "${MP_ITEMS[$i]}" -- --check-system; then
      passed=$((passed + 1))
    else
      failed=$((failed + 1))
    fi
  done
  printf '\n'; box_open "Verify summary"
  [ "$passed" -gt 0 ] && box_row "${CG}✓${C0} passed       $passed"
  [ "$failed" -gt 0 ] && box_row "${CR}✗${C0} failed       $failed"
  [ "$skipped" -gt 0 ] && box_row "${CD}· skipped      $skipped ${CD}(skill-only)${C0}"
  box_close
  # Verify is a report — hold it on screen until a key is pressed (the menu reclears on return).
  pause
  [ "$failed" = 0 ]
}

# run_caps_noninteractive [caps...] — headless self-test: no prompts, no TTY (CI / curl|bash). No caps
# → all capabilities detected as installed (union across harnesses). Accepts space- or comma-separated
# names. Skill-only caps and unknown names are skipped; exits non-zero if any check fails.
run_caps_noninteractive() {
  local caps="$*" cap rc=0
  have uvx || { err "uv / uvx not found — install it first: https://docs.astral.sh/uv/"; return 1; }
  if [ -z "$caps" ]; then
    caps=$(detect_installed)
    [ -z "$caps" ] && { err "no installed capabilities detected — pass an explicit list, e.g. --verify hcp-intelligence,territory-alignment"; return 1; }
    printf 'Detected installed: %s\n' "$caps"
  fi
  caps=${caps//,/ }
  OPP_DRY=0
  for cap in $caps; do
    case " ${CAP_ITEMS[*]} " in *" $cap "*) ;; *) warn "unknown capability: $cap (skipped)"; continue ;; esac
    if is_skill_only "$cap"; then printf -- '- %s: skill-only, no MCP env to check\n' "$cap"; continue; fi
    printf '\n== check %s ==\n' "$cap"; uvx_cap "$cap" -- --check-system || rc=1
  done
  return $rc
}

do_uninstall() {
  screen; hr "Uninstall"
  menu_pick "Remove from which harness" $ALL_HARNESSES
  [ "$PICK_I" -lt 0 ] && return 0
  local h=$PICK bin i mask=""
  bin=$(harness_bin "$h")
  require_harness "$h" || return 0
  screen; hr "Uninstall → $h"

  # detect installed; NOT-installed rows are locked ([-] not installed) so you only pick real ones
  load_caps
  spin "checking installed plugins in ${h}..." mask -- _detect_mask "$h"
  local any=0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    if [ "${mask:i:1}" = 1 ]; then any=1
    else MP_DIS[$i]=1; MP_DESC[$i]="not installed"; fi
  done
  [ "$any" = 0 ] && { warn "nothing installed in ${h}."; pause; return 0; }

  multi_pick "Uninstall which capabilities from $h (space=select · a=all)"
  [ "$MP_STATUS" != ok ] && return 0
  local picks="" remains=0
  for ((i = 0; i < ${#MP_ITEMS[@]}; i++)); do
    if [ "${MP_SEL[$i]}" = 1 ]; then picks="$picks ${MP_ITEMS[$i]}"
    elif [ "${mask:i:1}" = 1 ]; then remains=1; fi   # installed but not selected → stays
  done
  [ -z "$picks" ] && { warn "nothing selected."; pause; return 0; }

  screen; hr "Uninstall → $h"
  OPP_DRY=0; confirm "Run the uninstall commands now (otherwise just print)?" y || OPP_DRY=1
  local p plugin_rc removed="" failed=0
  for p in $picks; do
    plugin_rc=0
    case "$h" in
      claude)   run_cmd "$bin" plugin  uninstall "open-pharma-plugins-${p}@${MARKETPLACE}" || plugin_rc=1 ;;
      codex)    run_cmd "$bin" plugin  remove    "open-pharma-plugins-${p}@${MARKETPLACE}" || plugin_rc=1 ;;
      copilot)  run_cmd "$bin" plugin  uninstall "open-pharma-plugins-${p}" || plugin_rc=1 ;;
      *) err "unsupported harness '$h' — supported harnesses: $ALL_HARNESSES"; plugin_rc=1 ;;
    esac
    if [ "$plugin_rc" = 0 ]; then removed="$removed $p"; else failed=1; fi
  done
  # Claude and Copilot track marketplaces separately; drop them when nothing remains.
  if { [ "$h" = claude ] || [ "$h" = copilot ]; } && [ "$remains" = 0 ] && [ "$failed" = 0 ]; then
    run_cmd "$bin" plugin marketplace remove "$MARKETPLACE" || failed=1
  fi

  if [ "$OPP_DRY" = 0 ] && [ -n "$removed" ]; then
    printf '\n'; box_open "Removed → $h"
    for p in $removed; do box_row "${CD}✗ open-pharma-plugins-${p}${C0}"; done
    box_close
  fi

  if [ "$failed" = 1 ]; then
    err "uninstall incomplete — one or more commands failed"
    pause
    return 1
  fi

  printf '\n'
  if [ -f "$CONFIG_FILE" ] && confirm "Also delete the config file ($CONFIG_FILE)?" n; then
    rm -f "$CONFIG_FILE" && ok "removed config file"
  fi
  local dc; dc=$(default_cache_dir)
  if [ -d "$dc" ] && confirm "Delete the cache dir ($dc)?" n; then rm -rf "$dc" && ok "removed cache dir"; fi
  pause   # hold the uninstall result on screen before the menu reclears
}

menu() {
  while :; do
    screen
    status
    menu_pick "What would you like to do?" \
      "Install plugin" "Update installed plugins" "Configure (API key + all settings)" "Verify" "Uninstall" "Quit"
    case "$PICK_I" in
      0) do_install ;;
      1) do_update ;;
      2) do_configure ;;
      3) do_verify ;;
      4) do_uninstall ;;
      *) printf '\n  bye 👋\n\n'; exit 0 ;;   # "Quit" or cancelled (q/Esc)
    esac
    # Each action pauses on its own result screen; only cancel/back paths return straight here.
  done
}

case "${1:-}" in
  install)   do_install ;;
  update)    do_update ;;
  local)
    shift
    case "${1:-}" in
      '') do_local_install ;;
      --restore)
        shift
        [ "$#" -eq 0 ] || { err "usage: install.sh local [--restore]"; exit 2; }
        do_local_restore
        ;;
      *) err "usage: install.sh local [--restore]"; exit 2 ;;
    esac
    ;;
  configure) do_configure ;;
  verify)    do_verify ;;
  uninstall) do_uninstall ;;
  --verify)  shift; run_caps_noninteractive "$@" ;;
  -h|--help) banner; printf '\n  Usage: install.sh [install|update|local|configure|verify|uninstall]   (no arg = interactive menu)\n         install.sh update            # update installed plugins to current stable tags\n         install.sh local             # install plugins from this checkout\n         install.sh local --restore   # restore published refs after local testing\n         install.sh --verify [caps]   # non-interactive: check installed (or listed) caps\n\n  Default: each plugin uses its latest immutable stable tag.\n  Rollback: OPP_REF=open-pharma-plugins-<cap>-v<version> (select that cap only).\n\n' ;;
  *)         menu ;;
esac
