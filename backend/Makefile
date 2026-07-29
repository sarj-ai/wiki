.PHONY: setup setup-dependencies

setup:
	@$(MAKE) -C .. setup-hooks
	@$(MAKE) setup-dependencies

setup-dependencies:
	@uv sync --frozen
