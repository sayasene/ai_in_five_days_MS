.PHONY: install lint typecheck test eval playground tf-validate deploy-dry-run

install:
	uv sync --extra dev

lint:
	uv run ruff check pursuit_auditor tests

typecheck:
	uv run mypy pursuit_auditor

test:
	uv run pytest tests/unit tests/integration

eval:
	uv run pytest tests/eval

eval-adk:
	uv run adk eval pursuit_auditor tests/eval/evalsets/pursuit_auditor_golden.evalset.json \
		--config_file_path tests/eval/eval_config.json --print_detailed_results

playground:
	uv run adk web .

tf-validate:
	cd deployment/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate

deploy-dry-run:
	@echo "Previewing Cloud Run deployment via agents-cli (or Terraform):"
	@echo "  agents-cli deploy --deployment-target cloud_run --region us-central1 --dry-run"
	@echo "  cd deployment/terraform && terraform plan -var-file=environments/dev.tfvars"
