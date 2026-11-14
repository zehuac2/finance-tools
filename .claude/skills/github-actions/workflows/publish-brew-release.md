# Publish Brew Release

A publish-Homebrew-release workflow publishes a Homebrew formula after a release
build finishes. Put the workflow in a file named `publish-brew-release.yml`.
This file must contain the following content.

```yml
name: Publish Homebrew Release

on:
  workflow_run:
    # "Create Release Assets" is a placeholder. Use the name of the workflow
    # that builds and uploads the release assets. See create-release-assets.md.
    workflows: ["Create Release Assets"]
    types: [completed]
  workflow_dispatch:

jobs:
  publish:
    # Skip the job when the upstream workflow run fails.
    if:
      github.event_name == 'workflow_dispatch' ||
      github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-latest

    steps:
      - name: Resolve release tag
        env:
          HEAD_BRANCH: ${{ github.event.workflow_run.head_branch }}
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            tag=$(gh release view --json tagName -q .tagName)
          else
            # head_branch holds the tag name for a workflow_run trigger.
            # Do not use "gh release view" here. It returns the latest
            # release, not the release that triggered this run.
            tag="$HEAD_BRANCH"
          fi

      # Render the formula from a template file here. Do not hand-edit the
      # formula.

      - name: Checkout tap
        uses: actions/checkout@v7
        with:
          # <owner>/<tap-repo> is a placeholder. Use the tap repository.
          repository: <owner>/<tap-repo>
          token: ${{ secrets.HOMEBREW_TAP_TOKEN }}

      - name: Commit and push formula
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add Formula/<name>.rb

          if git diff --cached --quiet; then
            echo "No changes to formula; skipping commit."
            exit 0
          fi

          git commit -m "<commit message>"
          git push
```

A `workflow_run` event fires for every run of the upstream workflow, not only
the run for the newest release. Use `head_branch` to target the correct release.

Store the tap token in a secret named `HOMEBREW_TAP_TOKEN`. Grant the token
`contents: write` access on the tap repository.

Skip the commit when the formula has no changes.
