GCP_PROJECT := sarj-docs
STAMPS := .stamps
SECRETS_TTL := 1440
STANDARDS_RUN := uvx --isolated --python 3.14 --from sarj-standards==5.8.1 sarj-standards --root .

.PHONY: setup setup-hooks standards-sync standards-check standards-upgrade \
	check check-node check-quality check-backend check-frontend fix audit doctor \
	pull-local-secrets pull-local-secrets-force pull-tf-secrets pull-tf-secrets-force \
	push-local-secrets push-tf-secrets

setup-hooks:
	@cd frontend && npm exec -- lefthook install

standards-sync:
	@$(STANDARDS_RUN) update --offline --no-install

standards-check:
	@$(STANDARDS_RUN) doctor
	@$(STANDARDS_RUN) check --trust-repository-code

# Upgrade every Standards package, both locks, and all generated configs in one command.
standards-upgrade:
	@$(STANDARDS_RUN) update
	@$(MAKE) standards-check

setup: setup-hooks standards-sync
	@$(MAKE) -C backend setup-dependencies
	@npm ci --prefix frontend

# ---------------------------------------------------------------------------
# `make check` is the exact local mirror of CI. Each step is tagged with the
# workflow step it mirrors; when editing a step here or in .github/workflows/,
# update its twin. CI-only steps with no faithful local equivalent:
#   ci-backend.yml  "Image build + scan" (docker build + Trivy — needs Docker)
#   ci-backend.yml  "pip-audit"  \ network-bound advisory lookups; run them
#   ci-frontend.yml "npm audit"  / locally via `make audit`, not on every push
#   ci-iac.yml      "Trivy IaC scan" (terraform/ config scan — needs Trivy)
#   ci-quality.yml  "Reject lint baseline growth" (needs the PR base SHA)
#   ci-quality.yml  "gitleaks (full history)" (pre-commit runs gitleaks on the
#                   working tree; full-history scan stays CI-only)
# ---------------------------------------------------------------------------
check: check-node check-quality check-backend check-frontend
	@echo "make check: all CI-mirror steps passed"

check-node:
	@node -e 'const required = "26.5.1"; if (process.versions.node.localeCompare(required, undefined, { numeric: true }) < 0) { console.error("node >= " + required + " required (found " + process.versions.node + "). Run \"mise install\" (repo ships .mise.toml) or \"mise exec node@26.5.1 -- make check\"."); process.exit(1); }'

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
	@cd frontend && npm exec --call 'prettier --log-level warn --write openapi.json'
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
	@# ci-backend.yml "Run pytest"
	@cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70

check-frontend:
	@cd frontend && npm run generate:api
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
	@# ci-frontend.yml "Run vitest"
	@cd frontend && npm test
	@# ci-frontend.yml "Build" (Next.js build smoke test)
	@cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build

# Network-bound advisory scans, carved out of `make check` so pre-push stays
# offline-safe and fast. CI runs the same commands on every PR.
audit:
	@# ci-backend.yml "pip-audit"
	@cd backend && uv run pip-audit
	@# ci-frontend.yml "npm audit"
	@cd frontend && npm audit --omit=dev --audit-level=moderate

# `sarj-lint-configs doctor` walks the whole tree looking for version pins and
# does not know about Next.js build output (.next/, .open-next/), where stale
# vendored copies of config text produce phantom drift. Run it over a clean
# copy containing only git-tracked and unignored files instead.
doctor:
	@tmp=$$(mktemp -d) && tmp=$$(cd "$$tmp" && pwd -P) && trap 'rm -rf "$$tmp"' EXIT && \
		git ls-files --cached --others --exclude-standard -z | \
		tar --null -T - -cf - | tar -xf - -C "$$tmp" && \
		uv run --project backend --locked sarj-lint-configs doctor --dest "$$tmp"

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
