# Lock merged PR

A lock-merged-PR workflow locks the pull request conversation after a merge. Put
the workflow in a file named `lock-merged-pr.yml`. This file must contain the
following content.

```yml
name: Lock merged PR

on:
  pull_request_target:
    types: [closed]

jobs:
  lock:
    if: github.event.pull_request.merged == true
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      pull-requests: write
      issues: write

    steps:
      - name: Lock conversation
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          endpoint="repos/${{ github.repository }}/issues/${{ github.event.pull_request.number }}"
          if ! gh api -X PUT "${endpoint}/lock" -f lock_reason=resolved; then
            locked="$(gh api "$endpoint" --jq .locked)"
            if [ "$locked" = "true" ]; then
              echo "Conversation is already locked."
              exit 0
            fi
            exit 1
          fi
```
