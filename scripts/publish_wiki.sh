#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_SOURCE_DIR="${WIKI_SOURCE_DIR:-$REPO_ROOT/docs/wiki}"
WIKI_REMOTE_URL="${WIKI_REMOTE_URL:-}"
WIKI_WORKTREE="${WIKI_WORKTREE:-$(mktemp -d)}"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-Update GitHub Wiki documentation}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-github-actions[bot]}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"

if [[ ! -d "$WIKI_SOURCE_DIR" ]]; then
  echo "Wiki source directory not found: $WIKI_SOURCE_DIR" >&2
  exit 1
fi

if [[ -z "$WIKI_REMOTE_URL" ]]; then
  origin_url="$(git -C "$REPO_ROOT" config --get remote.origin.url || true)"
  if [[ -z "$origin_url" ]]; then
    echo "Set WIKI_REMOTE_URL or configure remote.origin.url." >&2
    exit 1
  fi

  case "$origin_url" in
    git@github.com:*.git)
      WIKI_REMOTE_URL="${origin_url%.git}.wiki.git"
      ;;
    https://github.com/*.git)
      WIKI_REMOTE_URL="${origin_url%.git}.wiki.git"
      ;;
    *)
      echo "Cannot infer GitHub Wiki remote from origin: $origin_url" >&2
      echo "Set WIKI_REMOTE_URL explicitly." >&2
      exit 1
      ;;
  esac
fi

cleanup() {
  if [[ "${KEEP_WIKI_WORKTREE:-0}" != "1" && "$WIKI_WORKTREE" == /tmp/* ]]; then
    rm -rf "$WIKI_WORKTREE"
  fi
}
trap cleanup EXIT

if [[ -d "$WIKI_WORKTREE/.git" ]]; then
  git -C "$WIKI_WORKTREE" fetch origin
  git -C "$WIKI_WORKTREE" checkout main 2>/dev/null || git -C "$WIKI_WORKTREE" checkout master
  git -C "$WIKI_WORKTREE" pull --ff-only
else
  rm -rf "$WIKI_WORKTREE"
  if ! git clone "$WIKI_REMOTE_URL" "$WIKI_WORKTREE"; then
    echo "Could not clone GitHub Wiki remote: $WIKI_REMOTE_URL" >&2
    echo "Make sure the GitHub Wiki is enabled and initialized for this repository." >&2
    exit 1
  fi
fi

git -C "$WIKI_WORKTREE" config user.name "$GIT_AUTHOR_NAME"
git -C "$WIKI_WORKTREE" config user.email "$GIT_AUTHOR_EMAIL"

find "$WIKI_WORKTREE" -maxdepth 1 -type f -name '*.md' -delete
cp "$WIKI_SOURCE_DIR"/*.md "$WIKI_WORKTREE"/

git -C "$WIKI_WORKTREE" add .
if git -C "$WIKI_WORKTREE" diff --cached --quiet; then
  echo "GitHub Wiki is already up to date."
  exit 0
fi

git -C "$WIKI_WORKTREE" commit -m "$COMMIT_MESSAGE"
git -C "$WIKI_WORKTREE" push
echo "Published GitHub Wiki documentation to $WIKI_REMOTE_URL"
