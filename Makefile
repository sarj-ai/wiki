GCP_PROJECT := sarj-docs
STAMPS := .stamps
SECRETS_TTL := 1440

.PHONY: setup setup-hooks check fix standards-sync standards-check standards-upgrade \
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
	@uv add --project backend --group dev --exact --upgrade-package sarj-lint-configs sarj-lint-configs
	@npm install --prefix frontend --save-dev --save-exact \
		@sarj/eslint-plugin@latest @sarj/tsconfig@latest
	@$(MAKE) standards-sync
	@$(MAKE) standards-check

# Dependencies first so `npm exec -- lefthook` resolves locally on a pristine clone.
setup:
	@$(MAKE) -C backend setup-dependencies
	@npm ci --prefix frontend
	@$(MAKE) setup-hooks
	@$(MAKE) standards-sync

# EXACT mirror of the blocking CI gates; each line is tagged with its CI job/step.
# Keep this list in lockstep with .github/workflows/ci-{backend,frontend,quality}.yml.
check:
	@$(MAKE) standards-check                # ci-quality pre-commit: Verify canonical Standards configs
	@uv run --project backend --locked python scripts/check_standards.py --verify-python-baseline .  # ci-quality pre-commit: Verify exact Python Standards baseline
	@cd backend && uv run ruff check .      # ci-backend lint-and-typecheck: Ruff check
	@cd backend && uv run ruff format --check .  # ci-backend lint-and-typecheck: Ruff format check
	@cd backend && uv run mypy app          # ci-backend lint-and-typecheck: Mypy strict
	@cd backend && uv run basedpyright -p pyrightconfig.json  # ci-backend lint-and-typecheck: Basedpyright strict
	@cd backend && uv run ty check app      # ci-backend lint-and-typecheck: ty check (app)
	@cd backend && uv run vulture           # ci-backend lint-and-typecheck: Vulture dead-code check
	@cd backend && uv run lint-imports      # ci-backend lint-and-typecheck: Import contracts
	@cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70  # ci-backend test: Run pytest
	@npm run --prefix frontend format:check  # ci-frontend lint-and-typecheck: Prettier check
	@npm run --prefix frontend typecheck    # ci-frontend lint-and-typecheck: TypeScript check (next typegen + tsc)
	@npm run --prefix frontend lint         # ci-frontend lint-and-typecheck: ESLint (next typegen + eslint --max-warnings 0)
	@npm run --prefix frontend knip         # ci-frontend lint-and-typecheck: Knip
	@npm run --prefix frontend type-coverage  # ci-frontend lint-and-typecheck: Type coverage
	@npm test --prefix frontend             # ci-frontend test: Run vitest
	@uvx --python 3.14 pre-commit run --all-files --show-diff-on-failure  # ci-quality pre-commit: Run pre-commit
	@echo "● All CI-mirrored gates green"

# Auto-fix everything that has an autofixer; rerun `make check` afterwards.
fix:
	@cd backend && uv run ruff check --fix . && uv run ruff format .
	@npm run --prefix frontend format
	@cd frontend && npm exec -- next typegen && npm exec -- eslint --fix .
	@echo "● Auto-fixes applied — run 'make check' to verify"

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
