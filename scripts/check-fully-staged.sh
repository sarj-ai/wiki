#!/usr/bin/env bash
set -euo pipefail

if (($# == 0)); then
  exit 0
fi

# Hidden index flags prevent Git from proving that the worktree matches the index.
if ! GIT_OPTIONAL_LOCKS=0 git --literal-pathspecs ls-files -v -z -- "$@" |
  while IFS= read -r -d '' entry; do
    case "${entry:0:1}" in
      [a-z] | S)
        printf 'Refusing to autofix a path with hidden index flags: %q\n' "${entry:2}" >&2
        exit 1
        ;;
    esac
  done then
  printf 'Refusing to autofix because index state could not be verified.\n' >&2
  printf 'Clear assume-unchanged/skip-worktree flags yourself and review the file, then retry.\n' >&2
  exit 1
fi

partial=()
for file in "$@"; do
  if ! changes=$(GIT_OPTIONAL_LOCKS=0 git -c diff.autoRefreshIndex=false --literal-pathspecs diff --patch --no-renames --no-ext-diff --no-textconv -- "$file"); then
    printf 'Refusing to autofix because worktree state could not be verified: %q\n' "$file" >&2
    exit 1
  fi
  if [[ -n "$changes" ]]; then
    partial+=("$file")
  fi
done

if (("${#partial[@]}")); then
  printf 'Refusing to autofix files whose worktree differs from the index:\n' >&2
  printf '  %q\n' "${partial[@]}" >&2
  printf 'Stage the whole file or stash its unstaged hunks, then retry.\n' >&2
  exit 1
fi
