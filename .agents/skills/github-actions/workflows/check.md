# Check

A Check workflow runs a repository's checks on pull requests. Put the workflow
in a file named `check.yml`. This file must start with the following content.

```yml
name: Check

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  workflow_call:
```

Add jobs after this content. Fill in those jobs from the repository's tooling
and documented checks. Examples are format, lint, type check, test, and build.
