#!/bin/sh
# Lint, not a test (`luc test` runs the tests): the module checks clean with -W and every
# source is laid out as luce-base fmt lays it out.
set -eu
cd "$(dirname "$0")/.."
base=${LUCE_BASE:-luce-base}
for module in $(sed -n 's/.*str\[\] public = \[\(.*\)\].*/\1/p' package.prisma | tr -d '",'); do
    warnings=$("$base" check "src/$module" -W)
    [ -z "$warnings" ] || { echo "$base check -W src/$module:"; echo "$warnings"; exit 1; }
done
for file in $(find src tests tools -name '*.lucb'); do
    "$base" fmt "$file" --check > /dev/null || { echo "$file is not formatted ($base fmt $file --write)"; exit 1; }
done
echo "clean"
