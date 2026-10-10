#!/bin/sh
# Preserve the shared Alpine runtime setup; QA additionally needs PDF tools.
set -eu

apk upgrade --no-cache
if [ "${1-}" = qa ]; then
  apk add --no-cache poppler-utils=25.12.0-r1
fi
python -m pip uninstall --yes pip
adduser -D -u 1001 app
