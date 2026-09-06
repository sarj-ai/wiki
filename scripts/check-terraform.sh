#!/usr/bin/env bash
set -euo pipefail

required_version=$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path(".mise.toml").read_text())["tools"]["terraform"])')
actual_version=$(terraform version -json | python3 -c 'import json, sys; print(json.load(sys.stdin)["terraform_version"])')
if [[ "$actual_version" != "$required_version" ]]; then
  echo "Terraform $required_version required (found $actual_version); run mise install and mise exec -- make check-iac." >&2
  exit 1
fi

# Keep validation separate from any operator-initialized remote backend. This
# ignored directory contains only provider tooling, never production state.
export TF_DATA_DIR="$PWD/terraform/.terraform/validation"
if [[ -e "$TF_DATA_DIR/terraform.tfstate" ]]; then
  echo "Refusing validation data directory containing backend metadata." >&2
  exit 1
fi

terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false -input=false
terraform -chdir=terraform validate -no-color
# Both providers are mocked in this exact file; do not run the separate real-
# provider recovery tests or an unfiltered Terraform test/plan/apply here.
terraform -chdir=terraform test -filter=tests/mcp_edge_preview.tftest.hcl -no-color
