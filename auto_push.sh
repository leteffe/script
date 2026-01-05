#!/usr/bin/env zsh
# auto-commit-push.zsh
# Public-friendly: init repo if needed, set/verify remote, commit, push
# Zsh required.

set -euo pipefail

# ---------- Defaults (can be overridden via args or env) ----------
BRANCH="${BRANCH:-main}"
REMOTE_URL="${REMOTE_URL:-}"                 # e.g. git@github.com:USER/REPO.git
REMOTE_NAME="${REMOTE_NAME:-origin}"

# Staging behavior:
INCLUDE_NEW_FILES=1                          # 1 = git add -A, 0 = git add -u
ALLOW_RISKY_FILES=0                          # 1 = allow typical secret files, 0 = block

# Optional: set identity only for the commit (otherwise user's git config is used)
COMMIT_USER_NAME="${COMMIT_USER_NAME:-}"
COMMIT_USER_EMAIL="${COMMIT_USER_EMAIL:-}"

# Optional: pull --rebase before pushing (only if upstream exists)
PULL_REBASE=0

# Commit message: can be provided via -m, --ask-message, or env COMMIT_MSG
COMMIT_MSG="${COMMIT_MSG:-}"
ASK_MESSAGE=0

# Safety: changing existing remote URL requires --force-remote
FORCE_REMOTE=0

# Repo paths:
typeset -a REPOS
REPOS=()

# ---------- Helpers ----------
log() { print "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
err() { print "[ERR] $*" >&2; }

usage() {
  cat <<'EOF'
Usage:
  ./auto-commit-push.zsh [options]

Options:
  -r, --repo <path>           Repo path (can be passed multiple times). Default: current dir.
  -u, --remote <url>          Remote URL to use for origin (or REMOTE_URL env).
  -b, --branch <name>         Branch name (default: main).
  -m, --message <text>        Commit message text.
      --ask-message           Ask interactively for commit message (TTY only).
      --no-include-new        Only stage tracked changes (git add -u) instead of git add -A.
      --allow-risky-files     Allow committing typical secret-like files (.env, *.pem, id_rsa, etc).
      --pull-rebase           git pull --rebase before push (only if upstream exists).
      --force-remote          If remote exists and differs, overwrite it.
      --name <name>           Set commit user.name for this commit only.
      --email <email>         Set commit user.email for this commit only.
  -h, --help                  Show help.

Examples:
  # Push current folder to a new remote (repo exists on GitHub already):
  ./auto-commit-push.zsh -u git@github.com:YOU/yourrepo.git --ask-message

  # Multiple repos, same remote (not common, but possible):
  ./auto-commit-push.zsh -r ~/proj1 -r ~/proj2 -u git@github.com:YOU/monorepo.git -m "sync"

  # Safer: only tracked files
  ./auto-commit-push.zsh -u git@github.com:YOU/yourrepo.git --no-include-new -m "update"
EOF
}

need_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || { err "Command not found: $cmd"; exit 1; }
}

is_git_repo() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1
}

has_commits() {
  git rev-parse --verify HEAD >/dev/null 2>&1
}

has_upstream() {
  git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1
}

switch_to_branch() {
  local br="$1"
  if git show-ref --verify --quiet "refs/heads/$br"; then
    git switch "$br" >/dev/null 2>&1 || git checkout "$br" >/dev/null 2>&1
  else
    git switch -c "$br" >/dev/null 2>&1 || git checkout -b "$br" >/dev/null 2>&1
  fi
}

secret_guard() {
  # Block common secret files among untracked + modified (before staging)
  # This is best-effort; users should still use .gitignore + secret scanning.
  local allow="$1"
  [[ "$allow" -eq 1 ]] && return 0

  local -a candidates
  candidates=()

  # untracked files
  local untracked
  untracked="$(git ls-files --others --exclude-standard || true)"
  if [[ -n "$untracked" ]]; then
    candidates+=("${(@f)untracked}")
  fi

  # modified-but-not-staged files
  local modified
  modified="$(git diff --name-only || true)"
  if [[ -n "$modified" ]]; then
    candidates+=("${(@f)modified}")
  fi

  # Dedup
  local -A seen
  local -a uniq
  uniq=()
  for f in "${candidates[@]}"; do
    [[ -n "${seen[$f]:-}" ]] && continue
    seen[$f]=1
    uniq+=("$f")
  done

  local -a risky
  risky=()

  for f in "${uniq[@]}"; do
    # patterns
    case "$f" in
      *.env|*.env.*|.env|.env.*) risky+=("$f");;
      *.pem|*.key|*.p12|*.pfx) risky+=("$f");;
      *id_rsa*|*id_dsa*|*id_ed25519*|*known_hosts*|*authorized_keys*) risky+=("$f");;
      *credentials*|*secret*|*secrets*|*apikey*|*api_key*|*token*|*passwd*|*password*) risky+=("$f");;
      *.sqlite|*.db|*.dump|*.sql) risky+=("$f");;
      *config.json|*settings.json) risky+=("$f");;
    esac
  done

  if (( ${#risky[@]} > 0 )); then
    err "Secret-Guard: Potenziell heikle Dateien gefunden. Commit/PUSH abgebrochen."
    for f in "${risky[@]}"; do
      err "  - $f"
    done
    err "Wenn das absichtlich ist: --allow-risky-files"
    return 1
  fi

  return 0
}

commit_with_optional_identity() {
  local msg="$1"
  if [[ -n "$COMMIT_USER_NAME" || -n "$COMMIT_USER_EMAIL" ]]; then
    local -a args
    args=(commit -m "$msg")
    if [[ -n "$COMMIT_USER_NAME" ]]; then
      args=("-c" "user.name=$COMMIT_USER_NAME" "${args[@]}")
    fi
    if [[ -n "$COMMIT_USER_EMAIL" ]]; then
      args=("-c" "user.email=$COMMIT_USER_EMAIL" "${args[@]}")
    fi
    git "${args[@]}"
  else
    git commit -m "$msg"
  fi
}

# ---------- Arg parsing ----------
while (( $# > 0 )); do
  case "$1" in
    -r|--repo)
      REPOS+=("${2:-}"); shift 2;;
    -u|--remote)
      REMOTE_URL="${2:-}"; shift 2;;
    -b|--branch)
      BRANCH="${2:-}"; shift 2;;
    -m|--message)
      COMMIT_MSG="${2:-}"; shift 2;;
    --ask-message)
      ASK_MESSAGE=1; shift;;
    --no-include-new)
      INCLUDE_NEW_FILES=0; shift;;
    --allow-risky-files)
      ALLOW_RISKY_FILES=1; shift;;
    --pull-rebase)
      PULL_REBASE=1; shift;;
    --force-remote)
      FORCE_REMOTE=1; shift;;
    --name)
      COMMIT_USER_NAME="${2:-}"; shift 2;;
    --email)
      COMMIT_USER_EMAIL="${2:-}"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      err "Unknown argument: $1"
      usage
      exit 2;;
  esac
done

# Default repo: current directory
if (( ${#REPOS[@]} == 0 )); then
  REPOS+=("$PWD")
fi

need_cmd git

# Ask for commit message if requested and running in a TTY
if [[ "$ASK_MESSAGE" -eq 1 && -t 0 ]]; then
  print -n "Commit-Text eingeben (leer = Auto-Timestamp): "
  read -r COMMIT_MSG_INPUT || true
  if [[ -n "${COMMIT_MSG_INPUT:-}" ]]; then
    COMMIT_MSG="$COMMIT_MSG_INPUT"
  fi
fi

# Fallback commit message
if [[ -z "${COMMIT_MSG:-}" ]]; then
  COMMIT_MSG="Auto commit: $(date '+%Y-%m-%d %H:%M:%S')"
else
  # Optional: add timestamp for traceability (feel free to remove)
  COMMIT_MSG="${COMMIT_MSG} ($(date '+%Y-%m-%d %H:%M:%S'))"
fi

# ---------- Main loop ----------
for REPO in "${REPOS[@]}"; do
  if [[ -z "$REPO" || ! -d "$REPO" ]]; then
    log "Repo-Pfad fehlt/ungültig: $REPO (überspringe)"
    continue
  fi

  cd "$REPO"

  # Init if not a git repo
  if ! is_git_repo; then
    log "$REPO: Kein Git-Repo gefunden -> initialisiere."
    # Try modern init -b, fallback
    if git init -b "$BRANCH" >/dev/null 2>&1; then
      :
    else
      git init >/dev/null
      switch_to_branch "$BRANCH"
    fi
  else
    # Switch to branch without renaming existing branches
    switch_to_branch "$BRANCH"
  fi

  # Remote handling
  local_has_remote=0
  if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    local_has_remote=1
  fi

  if [[ -z "$REMOTE_URL" ]]; then
    if [[ "$local_has_remote" -eq 0 ]]; then
      err "$REPO: Kein Remote gesetzt und keine --remote URL angegeben. Überspringe."
      continue
    fi
  else
    if [[ "$local_has_remote" -eq 0 ]]; then
      git remote add "$REMOTE_NAME" "$REMOTE_URL"
      log "$REPO: Remote '$REMOTE_NAME' hinzugefügt -> $REMOTE_URL"
    else
      CURRENT_URL="$(git remote get-url "$REMOTE_NAME")"
      if [[ "$CURRENT_URL" != "$REMOTE_URL" ]]; then
        if [[ "$FORCE_REMOTE" -eq 1 ]]; then
          git remote set-url "$REMOTE_NAME" "$REMOTE_URL"
          log "$REPO: Remote '$REMOTE_NAME' geändert -> $REMOTE_URL (war: $CURRENT_URL)"
        else
          err "$REPO: Remote '$REMOTE_NAME' ist anders:"
          err "  aktuell: $CURRENT_URL"
          err "  gewünscht: $REMOTE_URL"
          err "  Nutze --force-remote um zu überschreiben. Überspringe."
          continue
        fi
      fi
    fi
  fi

  # Optional pull --rebase if upstream exists
  if [[ "$PULL_REBASE" -eq 1 ]]; then
    if has_upstream; then
      log "$REPO: pull --rebase"
      git pull --rebase || { err "$REPO: pull --rebase fehlgeschlagen"; continue; }
    fi
  fi

  # Secret guard (best-effort)
  if ! secret_guard "$ALLOW_RISKY_FILES"; then
    continue
  fi

  # Stage changes
  if [[ "$INCLUDE_NEW_FILES" -eq 1 ]]; then
    git add -A
  else
    git add -u
  fi

  # Commit if staged changes exist
  if git diff --cached --quiet; then
    log "$REPO: Keine Änderungen zum Committen."
  else
    commit_with_optional_identity "$COMMIT_MSG"
    log "$REPO: Commit erstellt."
  fi

  # Push (set upstream if missing)
  if has_upstream; then
    if git push "$REMOTE_NAME" "$BRANCH"; then
      log "$REPO: Gepusht."
    else
      err "$REPO: Push fehlgeschlagen."
      continue
    fi
  else
    if git push -u "$REMOTE_NAME" "$BRANCH"; then
      log "$REPO: Upstream gesetzt & gepusht."
    else
      err "$REPO: Push fehlgeschlagen."
      continue
    fi
  fi
done
