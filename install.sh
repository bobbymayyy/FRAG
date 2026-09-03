#!/usr/bin/env bash
#
# FRAG installer for Claude Code.
#
# This wraps two commands that Claude Code already provides:
#
#     claude plugin marketplace add <repo>
#     claude plugin install frag@frag
#
# The script exists for the things those two commands don't do: check that
# the prerequisites are actually present before failing halfway, stay
# idempotent when re-run, and let you pin to a tag. If you'd rather run the
# two commands yourself, that is a completely reasonable choice and this
# script is not required.
#
# Usage:
#   ./install.sh                              # install from the default GitHub repo
#   ./install.sh --ref v1.2.0                 # pin to a tag or branch
#   ./install.sh --source gitea               # install from the Gitea mirror instead
#   ./install.sh --url https://host/o/r.git   # explicit repo URL
#   ./install.sh --scope project              # share via the project's settings.json
#   ./install.sh --uninstall
#
set -euo pipefail

# --- defaults; override with flags or environment ------------------------
GITHUB_REPO="${FRAG_GITHUB_REPO:-YOUR-GH-OWNER/FRAG}"
GITEA_URL="${FRAG_GITEA_MARKETPLACE_URL:-}"
MARKETPLACE_NAME="frag"
PLUGIN_NAME="frag"

SOURCE="github"
REF=""
URL=""
SCOPE="user"
ASSUME_YES=0
DO_UNINSTALL=0

MIN_PY_MAJOR=3
MIN_PY_MINOR=11

# --- output --------------------------------------------------------------
if [ -t 2 ]; then
  BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
  YEL=$(printf '\033[33m'); RST=$(printf '\033[0m')
else
  BOLD=""; DIM=""; RED=""; YEL=""; RST=""
fi

info() { printf '%s==>%s %s\n' "$BOLD" "$RST" "$*" >&2; }
warn() { printf '%swarning:%s %s\n' "$YEL" "$RST" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }
hint() { printf '   %s%s%s\n' "$DIM" "$*" "$RST" >&2; }

usage() {
  sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# --- args ----------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --ref)        REF="${2:?--ref needs a value}"; shift 2 ;;
    --source)     SOURCE="${2:?--source needs github or gitea}"; shift 2 ;;
    --url)        URL="${2:?--url needs a value}"; shift 2 ;;
    --scope)      SCOPE="${2:?--scope needs user, project, or local}"; shift 2 ;;
    --uninstall)  DO_UNINSTALL=1; shift ;;
    -y|--yes)     ASSUME_YES=1; shift ;;
    -h|--help)    usage ;;
    *)            die "unknown option: $1 (try --help)" ;;
  esac
done

case "$SCOPE" in
  user|project|local) ;;
  *) die "--scope must be one of: user, project, local" ;;
esac

# --- preflight -----------------------------------------------------------
# Fail here with a clear message rather than partway through an install.

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required but not on PATH.${2:+ $2}"
}

info "checking prerequisites"

need_cmd git "FRAG clones repositories with the git CLI."

if ! command -v claude >/dev/null 2>&1; then
  die "the 'claude' CLI is not on PATH.
   Install Claude Code first: https://code.claude.com/docs/en/quickstart"
fi

# FRAG's pyproject sets requires-python >=3.11. The plugin builds its venv
# with whatever python3 is on PATH, so an older interpreter fails at install
# time with a much less obvious message than this one.
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required but not on PATH."
fi
PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=${PY_VER%%.*}
PY_MINOR=${PY_VER##*.}
if [ "$PY_MAJOR" -lt "$MIN_PY_MAJOR" ] || \
   { [ "$PY_MAJOR" -eq "$MIN_PY_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_PY_MINOR" ]; }; then
  die "python3 is $PY_VER, but FRAG needs >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR}."
fi

# venv is a separate package on some distros (notably Debian/Ubuntu), and its
# absence is a confusing failure deep inside the plugin's first run.
if ! python3 -c 'import venv' >/dev/null 2>&1; then
  die "python3 is present but the venv module is missing.
   On Debian/Ubuntu: sudo apt install python3-venv"
fi

hint "git, claude, python3 $PY_VER, venv: ok"

# --- uninstall -----------------------------------------------------------
if [ "$DO_UNINSTALL" -eq 1 ]; then
  info "uninstalling $PLUGIN_NAME"
  claude plugin uninstall "${PLUGIN_NAME}@${MARKETPLACE_NAME}" --scope "$SCOPE" -y || true
  claude plugin marketplace remove "$MARKETPLACE_NAME" || true
  info "done. Repo clones and indexes in the plugin data directory were removed with it."
  hint "Pass --keep-data to 'claude plugin uninstall' directly if you want to keep them."
  exit 0
fi

# --- resolve the source --------------------------------------------------
# GitHub shorthand pins with @ref; a full git URL pins with #ref. Getting
# these backwards silently tracks the default branch instead of your tag,
# so they're built separately here rather than with one concatenation.
if [ -n "$URL" ]; then
  SOURCE_SPEC="$URL"
  if [ -n "$REF" ]; then SOURCE_SPEC="${SOURCE_SPEC}#${REF}"; fi
elif [ "$SOURCE" = "gitea" ]; then
  if [ -z "$GITEA_URL" ]; then
    die "--source gitea needs FRAG_GITEA_MARKETPLACE_URL set, or use --url."
  fi
  SOURCE_SPEC="$GITEA_URL"
  if [ -n "$REF" ]; then SOURCE_SPEC="${SOURCE_SPEC}#${REF}"; fi
elif [ "$SOURCE" = "github" ]; then
  SOURCE_SPEC="$GITHUB_REPO"
  if [ -n "$REF" ]; then SOURCE_SPEC="${SOURCE_SPEC}@${REF}"; fi
else
  die "--source must be github or gitea"
fi

info "marketplace source: $SOURCE_SPEC"

# --- add / update the marketplace ---------------------------------------
# Re-running the script must be safe, so an already-registered marketplace
# is updated rather than treated as an error.
if claude plugin marketplace list 2>/dev/null | grep -qw "$MARKETPLACE_NAME"; then
  info "marketplace '$MARKETPLACE_NAME' already registered; updating"
  claude plugin marketplace update "$MARKETPLACE_NAME" \
    || warn "update failed; continuing with the registered copy"
else
  info "adding marketplace"
  claude plugin marketplace add "$SOURCE_SPEC"
fi

# --- install -------------------------------------------------------------
info "installing $PLUGIN_NAME (scope: $SCOPE)"

INSTALL_ARGS=(plugin install "${PLUGIN_NAME}@${MARKETPLACE_NAME}" --scope "$SCOPE")
if [ "$ASSUME_YES" -eq 1 ]; then INSTALL_ARGS+=(-y); fi

# Deliberately NOT passing tokens via --config. They'd land in shell history
# and in the process list. Claude Code prompts for the sensitive ones at
# enable time and stores them in the OS keychain instead.
claude "${INSTALL_ARGS[@]}"

# --- done ----------------------------------------------------------------
cat >&2 <<EOF

${BOLD}FRAG installed.${RST}

Next:
  1. Claude Code will prompt for your GitHub and Gitea tokens the first time
     the plugin is enabled. The two token fields are stored in your OS
     keychain, not in settings.json.
  2. Start a session and try:
       "Login is throwing intermittent 500s in github/CERBERUS-2.0"
  3. The first search against a repo clones and indexes it, so it takes
     longer than later ones.

Useful:
  claude plugin details frag     what it contributes, and its token cost
  claude plugin update frag      pull the newest commit from ${SOURCE}
  ./install.sh --uninstall       remove it
EOF
