# GitHub repository scripts

Applied manually by an operator with `admin:repo` scope on the
repository. Part of Sub-PR B's hardening work but lives outside
Terraform because branch protection is GitHub-API-only.

## Apply branch protection to `main`

```sh
gh api \
  repos/a-alhashemi/sarj-docs/branches/main/protection \
  --method PUT \
  --input scripts/github/branch-protection.json
```

The payload requires:

- A pull request with at least one approving review before merging
- Passing status checks on `ci-backend`, `ci-frontend`, and `codeql`
- Conversation resolution on every thread before merging
- Signed commits
- No force pushes
- No branch deletion

Run this command on **cutover night**, after the final merge of
`feat/navigator-pivot` into `main`. Running it earlier would block
the pivot merge itself, since the feature branch isn't technically
passing the approving-review requirement at that moment (it is by
definition the one massive PR being merged).

## Verify

```sh
gh api repos/a-alhashemi/sarj-docs/branches/main/protection
```

Should echo the same JSON back with GitHub's own computed fields
added (URLs, etc.).

## Rollback

```sh
gh api repos/a-alhashemi/sarj-docs/branches/main/protection \
  --method DELETE
```

Requires `admin:repo` on the repo.
