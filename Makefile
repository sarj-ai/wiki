STANDARDS_RUN := uvx --no-config --isolated --python 3.14 --from sarj-standards-bootstrap code-standards

.PHONY: help setup setup-hooks standards-sync standards-check standards-upgrade
.PHONY: check check-node check-quality check-backend check-frontend check-iac check-repository fix audit
.PHONY: branch-protection-check branch-protection-apply branch-protection-rollback

help: ## List supported repository commands.
	@uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/repository_checks.py make-help Makefile

setup-hooks: ## Install the repository Git hooks.
	@sh scripts/github/install-hooks.sh

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
	@uv sync --project backend --locked
	@npm ci --prefix frontend

check: check-node check-quality check-backend check-frontend check-iac ## Run the complete local CI mirror.
	@echo "make check: all CI-mirror steps passed"

check-node:
	@uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/repository_checks.py node

check-quality: check-repository
	@$(MAKE) standards-check
	@uv run --project backend --locked python scripts/check_standards.py --verify-python-baseline .
	@uv run --project backend --locked sarj-ratchet .
	# These hooks remain enforced for changed files by pre-commit and in CI.
	# Their full-repository subprocess fan-out can deadlock on macOS.
	@SKIP=actionlint,check-added-large-files uvx --python 3.14 pre-commit run --all-files --show-diff-on-failure
	@uv --directory backend run --locked python scripts/export_openapi.py --check ../frontend/openapi.json

check-backend: ## Run backend lint, type, architecture, and test checks.
	@uv --directory backend run --locked deptry .
	@uv --directory backend run ruff check .
	@uv --directory backend run ruff format --check .
	@uv --directory backend run mypy app
	@uv --directory backend run basedpyright -p pyrightconfig.json
	@uv --directory backend run ty check app
	@uv --directory backend run vulture
	@uv --directory backend run lint-imports
	@uv --directory backend run pytest --cov=app --cov-report=term-missing --cov-fail-under=70

check-frontend: ## Run frontend generation, lint, type, test, and build checks.
	@npm --prefix frontend run generate:api
	@npm --prefix frontend run format:check
	@npm --prefix frontend run typecheck
	@npm --prefix frontend run lint:baseline-check
	@npm --prefix frontend run knip
	@npm --prefix frontend run type-coverage
	@npm --prefix frontend test
	@NEXT_PUBLIC_API_URL=http://localhost:8000 npm --prefix frontend run build

check-iac: ## Validate Terraform schemas and offline MCP preview wiring without cloud credentials.
	@bash scripts/check-terraform.sh

audit: ## Run network-backed production dependency vulnerability audits.
	@uv --directory backend run pip-audit
	@npm --prefix frontend audit --omit=dev --audit-level=moderate

fix: ## Apply safe backend and frontend formatter/linter fixes.
	@uv --directory backend run ruff check --fix .
	@uv --directory backend run ruff format .
	@npm --prefix frontend run format
	-@uv --directory frontend run --no-project node_modules/.bin/eslint --fix --suppressions-location eslint-suppressions.json .
	@echo "make fix: autofixes applied — run 'make check' to verify"

check-repository: ## Verify Markdown, README commands, and environment-example parity.
	@uv run --project backend --locked python scripts/repository_checks.py all
	@uv run --project backend --locked python -m pytest -q scripts/github/test_release_gate.py scripts/github/test_frontend_rollback.py scripts/github/test_backend_recovery.py scripts/github/test_iac_ci.py scripts/github/test_deploy_cache_contract.py scripts/github/test_cli.py scripts/github/test_shell_entrypoints.py

branch-protection-check: ## Compare live main-branch protection with the canonical policy.
	@uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/github/branch_protection.py check

branch-protection-apply: ## Refuse unconfirmed apply; use the CLI with a reviewed fingerprint and target.
	@uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/github/branch_protection.py apply

branch-protection-rollback: ## Disabled; restore a reviewed policy instead of deleting protection.
	@uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/github/branch_protection.py rollback
