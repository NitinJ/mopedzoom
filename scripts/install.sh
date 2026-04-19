#!/usr/bin/env bash
# install.sh — install the mopedzoomd systemd user unit.
#
# Idempotent: safe to re-run. Substitutes {{PLUGIN_PATH}} in the unit template
# with the plugin checkout path, writes the result to
# ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/mopedzoomd.service, ensures
# ~/.mopedzoom/logs exists, then reloads + enables + starts the unit via
# `systemctl --user` unless $MOPEDZOOM_SKIP_SYSTEMCTL is set.
#
# Usage:
#   scripts/install.sh              # install (or refresh) the unit
#   scripts/install.sh --dry-run    # print what would happen, touch nothing
#   scripts/install.sh -h           # show help
#
# Env:
#   MOPEDZOOM_PLUGIN_PATH   Override the detected plugin checkout path.
#   MOPEDZOOM_SKIP_SYSTEMCTL=1
#                           Skip the systemctl --user invocations (used by
#                           tests, also useful in container/CI environments
#                           that have no running user bus).

set -euo pipefail

DRY_RUN=0

usage() {
    cat <<'USAGE'
Usage: install.sh [--dry-run] [-h|--help]

Install the mopedzoomd systemd user unit.

Options:
  --dry-run   Print the actions that would be taken, but make no filesystem
              or systemctl changes.
  -h, --help  Show this help and exit.

Environment:
  MOPEDZOOM_PLUGIN_PATH     Override the detected plugin checkout path.
  MOPEDZOOM_SKIP_SYSTEMCTL  If set to "1", skip `systemctl --user` calls.
USAGE
}

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# Resolve the plugin checkout path (directory that contains deploy/, scripts/, …).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_PATH="${MOPEDZOOM_PLUGIN_PATH:-$(cd "$SCRIPT_DIR/.." && pwd)}"

UNIT_SRC="$PLUGIN_PATH/deploy/mopedzoomd.service"
if [[ ! -f "$UNIT_SRC" ]]; then
    echo "install.sh: unit template not found at $UNIT_SRC" >&2
    exit 1
fi

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
SYSTEMD_USER_DIR="$XDG_CONFIG_HOME/systemd/user"
UNIT_DST="$SYSTEMD_USER_DIR/mopedzoomd.service"
STATE_DIR="$HOME/.mopedzoom"
LOG_DIR="$STATE_DIR/logs"

say() { printf '%s\n' "$*"; }

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        say "[dry-run] would run: $*"
    else
        "$@"
    fi
}

write_file() {
    # write_file <path> <content>
    local path="$1" content="$2"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        say "[dry-run] would write $path"
        return
    fi
    mkdir -p "$(dirname "$path")"
    local tmp
    tmp="$(mktemp "${path}.XXXXXX")"
    printf '%s' "$content" > "$tmp"
    mv -f "$tmp" "$path"
}

say "mopedzoom install"
say "  plugin path : $PLUGIN_PATH"
say "  unit source : $UNIT_SRC"
say "  unit target : $UNIT_DST"
say "  state dir   : $STATE_DIR"

# 1. Ensure the per-user state + log directories exist.
if [[ "$DRY_RUN" -eq 1 ]]; then
    say "[dry-run] would mkdir -p $LOG_DIR"
    say "[dry-run] would mkdir -p $SYSTEMD_USER_DIR"
else
    mkdir -p "$LOG_DIR"
    mkdir -p "$SYSTEMD_USER_DIR"
fi

# 2. Substitute {{PLUGIN_PATH}} in the unit template and write the result.
#    Done in pure bash to avoid sed-escaping pitfalls with paths that contain
#    slashes (always true here).
TEMPLATE_CONTENT="$(cat "$UNIT_SRC")"
RENDERED="${TEMPLATE_CONTENT//\{\{PLUGIN_PATH\}\}/$PLUGIN_PATH}"
write_file "$UNIT_DST" "$RENDERED"

# 3. Reload + enable + start, unless explicitly skipped (tests, CI).
SKIP="${MOPEDZOOM_SKIP_SYSTEMCTL:-0}"
if [[ "$SKIP" = "1" ]]; then
    say "skipping systemctl (MOPEDZOOM_SKIP_SYSTEMCTL=1)"
elif [[ "$DRY_RUN" -eq 1 ]]; then
    say "[dry-run] would run: systemctl --user daemon-reload"
    say "[dry-run] would run: systemctl --user enable --now mopedzoomd.service"
else
    run systemctl --user daemon-reload
    run systemctl --user enable --now mopedzoomd.service
fi

say "done."
