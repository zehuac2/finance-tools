# Deploy to Github Pages

A deploy-to-GitHub-Pages workflow builds a site and uploads it to GitHub Pages.
Put the workflow in a file named `deploy-github-pages.yml`. This file must
contain the following content.

```yml
name: Deploy to GitHub Pages

on:
  push:
    branches:
      - "main"
  workflow_dispatch:
```
