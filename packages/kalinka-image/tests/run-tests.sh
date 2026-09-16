#!/usr/bin/env bash
#
# Run the image tests. Files that edit /etc skip themselves unless
# KALINKA_IMAGE_TEST_DISPOSABLE=1 says this system is throwaway — `make
# image-test` sets that inside a container.
set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
failed=0 skipped=0 passed=0

for test_file in "$TESTS_DIR"/test_*.sh; do
  echo "== ${test_file##*/}"
  bash "$test_file"
  case $? in
    0)  passed=$((passed + 1)) ;;
    77) skipped=$((skipped + 1)) ;;
    *)  failed=$((failed + 1)) ;;
  esac
done

echo
echo "$passed passed, $failed failed, $skipped skipped"
[ "$failed" -eq 0 ]
