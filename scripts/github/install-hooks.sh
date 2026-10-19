#!/bin/sh
set -eu

# Frontend-only CI jobs keep their existing installer without requiring UV.
# Lefthook is consumed here from the frontend toolchain, outside its Knip scope.
if [ "${CI:-}" = true ] || [ "${CI:-}" = 1 ]; then
  exec npm --prefix frontend exec -- lefthook install
fi

exec uvx --no-config --isolated --python 3.14 --from sarj-standards-bootstrap code-standards maintain hooks install
