GCP_PROJECT := sarj-docs
STAMPS := .stamps
SECRETS_TTL := 1440

.PHONY: pull-local-secrets pull-local-secrets-force pull-tf-secrets \
	pull-tf-secrets-force push-local-secrets push-tf-secrets

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
