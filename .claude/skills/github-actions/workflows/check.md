# Check

A Check workflow runs a repository's checks on pull requests and on `main`. Put
the workflow in a file named `check.yml`. This file must start with the
following content.

```yml
name: Check

on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  workflow_call:
```

Add jobs after this content. Fill in those jobs from the repository's tooling
and documented checks. Examples are format, lint, type check, test, and build.

Do not add a build job when the repository also runs a build step on every push
to `main`. That build step already covers `main`. Add a build job only when the
repository has no other build step.
