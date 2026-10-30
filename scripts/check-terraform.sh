#!/usr/bin/env bash
set -euo pipefail

required_version=$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path(".mise.toml").read_text())["tools"]["terraform"])')
actual_version=$(terraform version -json | python3 -c 'import json, sys; print(json.load(sys.stdin)["terraform_version"])')
if [[ "$actual_version" != "$required_version" ]]; then
  echo "Terraform $required_version required (found $actual_version); run mise install and mise exec -- uv run scripts/wiki.py check iac." >&2
  exit 1
fi

# Never inherit an operator backend or put provider-cache symlinks in the source
# tree. Explicit TF_PLUGIN_CACHE_DIR settings can still reuse downloaded tools.
terraform_data_dir=$(mktemp -d /tmp/wiki-terraform-validation.XXXXXX)
trap 'rm -rf -- "$terraform_data_dir"' EXIT
export TF_DATA_DIR="$terraform_data_dir"

terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false -input=false
terraform -chdir=terraform validate -no-color
# Both providers are mocked in the exact filtered files below; do not run the
# separate real-provider recovery tests or an unfiltered plan/apply here.
terraform -chdir=terraform test -filter=tests/mcp_edge_preview.tftest.hcl -no-color
terraform -chdir=terraform test -filter=tests/mcp_production_plumbing.tftest.hcl -no-color
terraform -chdir=terraform test -filter=tests/document_transform_qa_job.tftest.hcl -no-color
terraform -chdir=terraform test -filter=tests/github_identity.tftest.hcl -no-color
