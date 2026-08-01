GCP_PROJECT := sarj-docs
STAMPS := .stamps
SECRETS_TTL := 1440

.PHONY: setup setup-hooks standards-sync standards-check standards-upgrade \
	check check-node check-quality check-backend check-frontend fix \
	pull-local-secrets pull-local-secrets-force pull-tf-secrets pull-tf-secrets-force \
	push-local-secrets push-tf-secrets

setup-hooks:
	@cd frontend && npm exec -- lefthook install

standards-sync:
	@uv run --project backend --locked sarj-lint-configs sync \
		--python-dest backend --typescript-dest frontend --dest . --force

standards-check:
	@uv run --project backend --locked sarj-lint-configs sync \
		--python-dest backend --typescript-dest frontend --dest . --check

# Upgrade every Standards package, both locks, and all generated configs in one command.
standards-upgrade:
	@uv add --project backend --group dev --bounds exact --upgrade-package sarj-lint-configs sarj-lint-configs
	@npm install --prefix frontend --save-dev --save-exact \
		@sarj/eslint-plugin@latest @sarj/tsconfig@latest
	@$(MAKE) standards-sync
	@$(MAKE) standards-check

setup: setup-hooks standards-sync
	@$(MAKE) -C backend setup-dependencies
	@npm ci --prefix frontend

# ---------------------------------------------------------------------------
# `make check` is the exact local mirror of CI. Each step is tagged with the
# workflow step it mirrors; when editing a step here or in .github/workflows/,
# update its twin. CI-only steps with no faithful local equivalent:
#   ci-backend.yml  "Image build + scan" (docker build + Trivy — needs Docker)
#   ci-quality.yml  "Reject lint baseline growth" (needs the PR base SHA)
#   ci-quality.yml  "gitleaks (full history)" (pre-commit runs gitleaks on the
#                   working tree; full-history scan stays CI-only)
# ---------------------------------------------------------------------------
check: check-node check-quality check-backend check-frontend
	@echo "make check: all CI-mirror steps passed"

check-node:
	@node -e 'const v = Number(process.versions.node.split(".")[0]); if (v < 24) { console.error("node >= 24 required (found " + process.versions.node + "). Run \"mise install\" (repo ships .mise.toml) or \"mise exec node@24 -- make check\"."); process.exit(1); }'

check-quality:
	@$(MAKE) standards-check
	@# ci-quality.yml "Verify exact Python Standards baseline"
	@uv run --project backend --locked python scripts/check_standards.py --verify-python-baseline .
	@# ci-quality.yml "Suppression ratchet"
	@uv run --project backend --locked sarj-ratchet .
	@# ci-quality.yml "Run pre-commit"
	@uvx --python 3.14 pre-commit run --all-files --show-diff-on-failure
	@# ci-quality.yml api-contract: "Export OpenAPI spec" + "Fail on drift".
	@# CI prettier-formats the exported spec via npm ci's postinstall
	@# (generate:api); mirror that formatting before diffing.
	@cd backend && uv run --locked python scripts/export_openapi.py ../frontend/openapi.json
	@cd frontend && npm exec -- prettier --log-level warn --write openapi.json
	@git diff --exit-code -- frontend/openapi.json

check-backend:
	@# ci-backend.yml "Ruff check" (+ "Standards Ruff strict" — same config)
	@cd backend && uv run ruff check .
	@# ci-backend.yml "Ruff format check"
	@cd backend && uv run ruff format --check .
	@# ci-backend.yml "Mypy strict"
	@cd backend && uv run mypy app
	@# ci-backend.yml "Basedpyright strict" (both steps run the same command)
	@cd backend && uv run basedpyright -p pyrightconfig.json
	@# ci-backend.yml "ty check (app)"
	@cd backend && uv run ty check app
	@# ci-backend.yml "Vulture dead-code check"
	@cd backend && uv run vulture
	@# ci-backend.yml "Import contracts"
	@cd backend && uv run lint-imports
	@# ci-backend.yml "pip-audit"
	@cd backend && uv run pip-audit
	@# ci-backend.yml "Run pytest"
	@cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70

check-frontend:
	@# ci-frontend.yml "Prettier check"
	@cd frontend && npm run format:check
	@# ci-frontend.yml "TypeScript check"
	@cd frontend && npm run typecheck
	@# ci-frontend.yml "ESLint"
	@cd frontend && npm run lint
	@# ci-frontend.yml "Verify exact ESLint baseline"
	@cd frontend && npm run lint:baseline-check
	@# ci-frontend.yml "Knip"
	@cd frontend && npm run knip
	@# ci-frontend.yml "Type coverage"
	@cd frontend && npm run type-coverage
	@# ci-frontend.yml "npm audit"
	@cd frontend && npm audit --omit=dev --audit-level=moderate
	@# ci-frontend.yml "Run vitest"
	@cd frontend && npm test
	@# ci-frontend.yml "Build" (Next.js build smoke test)
	@cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build

# Apply every autofix the checkers know how to make; `make check` verifies.
fix:
	@cd backend && uv run ruff check --fix .
	@cd backend && uv run ruff format .
	@cd frontend && npm run format
	@cd frontend && node_modules/.bin/eslint --fix --suppressions-location eslint-suppressions.json . || true
	@echo "make fix: autofixes applied — run 'make check' to verify"

$(STAMPS):
	@mkdir -p $(STAMPS)

pull-local-secrets: | $(STAMPS)
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

pull-tf-secrets: | $(STAMPS)
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

push-local-secrets:
	@gcloud secrets describe env-backend-local --project=$(GCP_PROJECT) >/dev/null 2>&1 || \
		gcloud secrets create env-backend-local --project=$(GCP_PROJECT) --replication-policy=automatic
	gcloud secrets versions add env-backend-local --project=$(GCP_PROJECT) --data-file=backend/.env
	@echo "● backend/.env published as env-backend-local"

push-tf-secrets:
	@gcloud secrets describe terraform-tfvars --project=$(GCP_PROJECT) >/dev/null 2>&1 || \
		gcloud secrets create terraform-tfvars --project=$(GCP_PROJECT) --replication-policy=automatic
	gcloud secrets versions add terraform-tfvars --project=$(GCP_PROJECT) --data-file=terraform/terraform.tfvars
	@echo "● terraform/terraform.tfvars published as terraform-tfvars"
