#!/usr/bin/env bash
# Run every Argon regression script (test_F*.py) from the repo root; prints failures, exits 1 if any.
cd "$(dirname "$0")/.." || exit 1
pass=0; fail=0
for f in argon_tests/test_F*.py; do
    if out=$(PYTHONPATH="$PWD" timeout 300 ./py "$f" 2>&1); then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); echo "FAIL $(basename "$f" .py)"; echo "$out" | tail -3 | sed 's/^/    /'
    fi
done
echo "argon_tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
