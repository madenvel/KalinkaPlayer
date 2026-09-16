#!/usr/bin/env bash
#
# The stand-in systemctl used while the image is assembled. What it lets
# through decides whether the appliance comes up playing, and what it swallows
# decides whether the build survives a package that restarts its own service.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$TESTS_DIR")"
# shellcheck source=helpers.sh
. "$TESTS_DIR/helpers.sh"

needs_disposable_system

SHIM="$PKG_DIR/build-aids/systemctl"
LOG="$(mktemp)"
trap 'rm -f "$LOG" /usr/bin/systemctl.real' EXIT

cat > /usr/bin/systemctl.real <<SCRIPT
#!/bin/sh
echo "\$*" >> "$LOG"
SCRIPT
chmod 755 /usr/bin/systemctl.real

shim() { : > "$LOG"; bash "$SHIM" "$@"; SHIM_STATUS=$?; DELEGATED="$(cat "$LOG")"; }

echo "  -- verbs that have to take effect"
shim enable kalinka.service
assert_eq "enable is delegated against this root" "$DELEGATED" "--root=/ enable kalinka.service"

shim enable --now kalinka-restart.path
assert_eq "--now is dropped, since --root cannot start anything" \
  "$DELEGATED" "--root=/ enable kalinka-restart.path"

shim disable --now ssh.socket ssh.service
assert_eq "every named unit survives the rewrite" \
  "$DELEGATED" "--root=/ disable ssh.socket ssh.service"

shim preset-all
assert_eq "preset-all is delegated" "$DELEGATED" "--root=/ preset-all"

echo "  -- verbs with nothing behind them"
for verb in daemon-reload restart start stop status is-active reload; do
  shim "$verb" kalinka.service
  assert_eq "$verb succeeds" "$SHIM_STATUS" 0
  assert_eq "$verb is swallowed" "$DELEGATED" ""
done

echo "  -- an option is never mistaken for the verb"
shim --no-pager --full status kalinka.service
assert_eq "leading options do not become the verb" "$DELEGATED" ""
assert_eq "and the call still succeeds" "$SHIM_STATUS" 0

exit "$FAILURES"
