from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
_CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_PLATFORM_PREFIX = "terraform-providers-${{ runner.os }}-${{ runner.arch }}-"


def test_iac_validation_uses_locked_provider_cache_without_weakening_gates() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-iac.yml").read_text())
    jobs = workflow["jobs"]
    validation = jobs["terraform-validation"]
    cache_directory = validation["env"]["TF_PLUGIN_CACHE_DIR"]
    assert Path(cache_directory).is_absolute()
    assert cache_directory.endswith("/terraform-provider-cache")

    (cache_step,) = [
        step
        for step in validation["steps"]
        if step.get("uses", "").startswith("actions/cache@")
    ]
    assert cache_step["uses"] == _CACHE_ACTION
    prepare_index = next(
        index
        for index, step in enumerate(validation["steps"])
        if step.get("name") == "Prepare Terraform provider cache"
    )
    cache_index = validation["steps"].index(cache_step)
    assert prepare_index < cache_index
    assert cache_step["with"]["path"] == "${{ env.TF_PLUGIN_CACHE_DIR }}"
    assert cache_step["with"]["key"] == (
        _PLATFORM_PREFIX + "${{ hashFiles('terraform/.terraform.lock.hcl') }}"
    )
    assert cache_step["with"]["restore-keys"] == f"{_PLATFORM_PREFIX}\n"

    required = jobs["required"]
    assert required["if"] == "${{ always() }}"
    assert set(required["needs"]) == {"terraform-validation", "trivy-config"}
    required_step = required["steps"][0]
    assert required_step["env"] == {
        "TRIVY_RESULT": "${{ needs.trivy-config.result }}",
        "TERRAFORM_RESULT": "${{ needs.terraform-validation.result }}",
    }
    assert required_step["run"] == (
        'test "$TRIVY_RESULT" = success && test "$TERRAFORM_RESULT" = success'
    )
