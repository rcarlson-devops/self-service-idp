#!/usr/bin/env bash
set -uo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && cd "../.." && pwd)"
EVIDENCE_DIR="${REPO_DIR}/docs/evidence"
SCRIPTS=( "$SCRIPT_DIR"/A*.sh )
PASSED=0
FAILED=0

if [[ "${#SCRIPTS[@]}" -eq 0 ]]; then
    echo "No files matching naming convention found." >&2
    exit 1
fi

# Create dated evidence file
iso=$(date -I)
filename="${EVIDENCE_DIR}/abuse-${iso}.md"

if ! touch "$filename"; then
    echo "Couldn't create ${filename}" >&2
    exit 1
fi

timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! cat << EOF > "$filename"; then
# Abuse test results

Run at: $timestamp

EOF
    echo "Couldn't write to file ${filename}" >&2
    exit 1
fi

# Execute abuse test scripts
# Abuse tests print test results and reasons, never secret values
# The evidence file is committed to a public repo
# This test runner keeps only the first 20 lines of abuse test script output
for script in "${SCRIPTS[@]}"; do
    test_output=$("$script" 2>&1)
    exit_code=$?

    capped_test=$(printf '%s\n' "$test_output" | head -n 20)

    if [[ "$exit_code" -eq 0 ]]; then
        PASSED=$((PASSED + 1))
        echo "- $(basename "$script"): PASS" >> "$filename"
        echo "$(basename "$script"): PASS"
    else
        FAILED=$((FAILED + 1))
        {
            echo "- $(basename "$script"): FAIL (exit ${exit_code})";
            echo '````';
            printf "%s\n" "$capped_test";
            echo '````';
        } >> "$filename"
        echo "$(basename "$script"): FAIL (exit ${exit_code})"
        printf '%s\n' "$capped_test"
    fi
done

echo "- PASSED: ${PASSED}, FAILED: ${FAILED}" >> "$filename"
echo "PASSED: ${PASSED}, FAILED: ${FAILED}"

# If any abuse scripts failed, exit 1.
# Otherwise exit 0.
if [[ "$FAILED" -ge 1 ]]; then
    exit 1
else
    exit 0
fi
