# Release and promotion policy

Releases use SemVer tags (`vMAJOR.MINOR.PATCH`). A tag builds exactly one collector image and all later deployment stages reference the immutable digest, never a mutable tag.

The release workflow performs: container build/push, HIGH/CRITICAL Trivy gate, SPDX SBOM generation, keyless Cosign signing through GitHub OIDC, GitHub build provenance attestation, and release-note creation.

Azure deployments use GitHub OIDC (`id-token: write`) and protected GitHub Environments. Configure `staging` and `prod` environments so production requires an explicit reviewer. Do not store an Azure client secret; the three Azure values are identifiers used for workload-identity federation.

`main` governance is defined in `.github/rulesets/main.json`. The connector used to develop this repository does not expose administrative ruleset mutation, so an administrator applies the checked-in policy once with:

```bash
scripts/apply_github_ruleset.sh magic-alt/grafana-dashboard
```

After application, the repository policy and its proposed future changes remain reviewable in Git.
