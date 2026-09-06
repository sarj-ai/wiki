GCP_PROJECT := sarj-docs
STAMPS := .stamps
SECRETS_TTL := 1440
STANDARDS_RUN := uvx --no-config --isolated --python 3.14 --from sarj-standards-bootstrap==2.0.2 code-standards

.PHONY: help setup setup-hooks standards-sync standards-check standards-upgrade \
	check check-node check-quality check-backend check-frontend check-iac check-repository fix audit doctor \
	branch-protection-check branch-protection-apply branch-protection-rollback \
	pull-local-secrets pull-local-secrets-force pull-tf-secrets pull-tf-secrets-force \
	push-local-secrets push-tf-secrets

help: ## List supported repository commands.
	@python3 scripts/repository_checks.py make-help Makefile

setup-hooks: ## Install the repository Git hooks.
	@cd frontend && npm exec -- lefthook install

standards-sync: ## Regenerate pinned Standards configuration without network access.
	@$(STANDARDS_RUN) update --offline --no-install

standards-check: ## Verify Standards packages, locks, and generated configuration.
	@$(STANDARDS_RUN) doctor
	@$(STANDARDS_RUN) check --trust-repository-code

# Upgrade every Standards package, both locks, and all generated configs in one command.
standards-upgrade: ## Upgrade all managed Standards packages and configuration.
	@$(STANDARDS_RUN) update
	@$(MAKE) standards-check

setup: setup-hooks standards-sync ## Install backend, frontend, and repository tooling.
	@$(MAKE) -C backend setup-dependencies
	@npm ci --prefix frontend

check: check-node check-quality check-backend check-frontend check-iac ## Run the complete local CI mirror.
	@echo "make check: all CI-mirror steps passed"

check-node:
	@node -e 'const required = "26.7.0"; if (process.versions.node.localeCompare(required, undefined, { numeric: true }) < 0) { console.error("node >= " + required + " required (found " + process.versions.node + "). Run \"mise install\" (repo ships .mise.toml) or \"mise exec node@26.7.0 -- make check\"."); process.exit(1); }'

check-quality: check-repository
	@$(MAKE) standards-check
	@uv run --project backend --locked python scripts/check_standards.py --verify-python-baseline .
	@uv run --project backend --locked sarj-ratchet .
	# These hooks remain enforced for changed files by pre-commit and in CI.
	# Their full-repository subprocess fan-out can deadlock on macOS.
	@SKIP=actionlint,check-added-large-files uvx --python 3.14 pre-commit run --all-files --show-diff-on-failure
	@cd backend && uv run --locked python scripts/export_openapi.py --check ../frontend/openapi.json

check-backend: ## Run backend lint, type, architecture, and test checks.
	@cd backend && uv run ruff check .
	@cd backend && uv run ruff format --check .
	@cd backend && uv run mypy app
	@cd backend && uv run basedpyright -p pyrightconfig.json
	@cd backend && uv run ty check app
	@cd backend && uv run vulture
	@cd backend && uv run lint-imports
	@cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70

check-frontend: ## Run frontend generation, lint, type, test, and build checks.
	@cd frontend && npm run generate:api
	@cd frontend && npm run format:check
	@cd frontend && npm run typecheck
	@cd frontend && npm run lint
	@cd frontend && npm run lint:baseline-check
	@cd frontend && npm run knip
	@cd frontend && npm run type-coverage
	@cd frontend && npm test
	@cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build

check-iac: ## Validate Terraform schemas and offline MCP preview wiring without cloud credentials.
	@bash scripts/check-terraform.sh

audit: ## Run network-backed production dependency vulnerability audits.
	@cd backend && uv run pip-audit
	@cd frontend && npm audit --omit=dev --audit-level=moderate

doctor: ## Diagnose repository Standards drift in a clean tree copy.
	@tmp=$$(mktemp -d) && tmp=$$(cd "$$tmp" && pwd -P) && trap 'rm -rf "$$tmp"' EXIT && \
		git ls-files --cached --others --exclude-standard -z | \
		tar --null -T - -cf - | tar -xf - -C "$$tmp" && \
		$(STANDARDS_RUN) --root "$$tmp" doctor

fix: ## Apply safe backend and frontend formatter/linter fixes.
	@cd backend && uv run ruff check --fix .
	@cd backend && uv run ruff format .
	@cd frontend && npm run format
	@cd frontend && node_modules/.bin/eslint --fix --suppressions-location eslint-suppressions.json . || true
	@echo "make fix: autofixes applied — run 'make check' to verify"

check-repository: ## Verify Markdown, README commands, and environment-example parity.
	@uv run --project backend --locked python scripts/repository_checks.py all
	@uv run --project backend --locked python -m pytest -q scripts/github/test_release_gate.py scripts/github/test_frontend_rollback.py

branch-protection-check: ## Compare live main-branch protection with the canonical policy.
	@python3 scripts/github/branch_protection.py check

branch-protection-apply: ## Refuse unconfirmed apply; use the CLI with a reviewed fingerprint and target.
	@python3 scripts/github/branch_protection.py apply

branch-protection-rollback: ## Disabled; restore a reviewed policy instead of deleting protection.
	@python3 scripts/github/branch_protection.py rollback

$(STAMPS):
	@mkdir -p $(STAMPS)

pull-local-secrets: | $(STAMPS) ## Refresh local backend secrets when the cached copy is stale.
	@if [ -f $(STAMPS)/secrets ] && \
	    [ -n "$$(find $(STAMPS)/secrets -mmin -$(SECRETS_TTL) 2>/dev/null)" ]; then \
		echo "● Secrets fresh (pulled today) — skipping. Run 'make pull-local-secrets-force' to refresh."; \
	else \
		$(MAKE) pull-local-secrets-force; \
	fi

pull-local-secrets-force: | $(STAMPS)
	gcloud secrets versions access latest --secret="env-backend-local" --project=$(GCP_PROJECT) > backend/.env
	@touch $(STAMPS)/secrets
	@echo "● Secrets refreshed"

pull-tf-secrets: | $(STAMPS) ## Refresh Terraform secrets when the cached copy is stale.
	@if [ -f $(STAMPS)/tf-secrets ] && \
	    [ -n "$$(find $(STAMPS)/tf-secrets -mmin -$(SECRETS_TTL) 2>/dev/null)" ]; then \
		echo "● Terraform secrets fresh (pulled today) — skipping. Run 'make pull-tf-secrets-force' to refresh."; \
	else \
		$(MAKE) pull-tf-secrets-force; \
	fi

pull-tf-secrets-force: | $(STAMPS)
	gcloud secrets versions access latest --secret="terraform-tfvars" --project=$(GCP_PROJECT) > terraform/terraform.tfvars
	@touch $(STAMPS)/tf-secrets
	@echo "● Terraform secrets refreshed"

push-local-secrets: ## Publish backend/.env to Google Secret Manager.
	@gcloud secrets describe env-backend-local --project=$(GCP_PROJECT) >/dev/null 2>&1 || \
		gcloud secrets create env-backend-local --project=$(GCP_PROJECT) --replication-policy=automatic
	gcloud secrets versions add env-backend-local --project=$(GCP_PROJECT) --data-file=backend/.env
	@echo "● backend/.env published as env-backend-local"

push-tf-secrets: ## Publish terraform/terraform.tfvars to Google Secret Manager.
	@gcloud secrets describe terraform-tfvars --project=$(GCP_PROJECT) >/dev/null 2>&1 || \
		gcloud secrets create terraform-tfvars --project=$(GCP_PROJECT) --replication-policy=automatic
	gcloud secrets versions add terraform-tfvars --project=$(GCP_PROJECT) --data-file=terraform/terraform.tfvars
	@echo "● terraform/terraform.tfvars published as terraform-tfvars"
