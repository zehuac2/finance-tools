# Create Release Assets

A release workflow uploads assets to a published release. Put the workflow in a
file named `create-release-assets.yml`. This file must contain the following
content.

```yml
name: Create Release Assets

on:
  release:
    types: [published]

jobs:
  build:
    steps:
      # Add this step after the asset creation and upload step.
      - name: Delete assets from older releases
        # v9 is a placeholder. Use the latest version.
        uses: actions/github-script@v9
        with:
          script: |
            const currentTag = context.payload.release.tag_name;
            const { owner, repo } = context.repo;

            const releases = await github.paginate(github.rest.repos.listReleases, {
              owner,
              repo,
              per_page: 100,
            });

            const sorted = [...releases].sort(
              (a, b) => new Date(b.created_at) - new Date(a.created_at),
            );

            // Keep the last 2 releases. This prevents installer errors when
            // the latest release is too new.
            const keepTags = new Set([
              currentTag,
              ...sorted.map((r) => r.tag_name).filter((t) => t !== currentTag).slice(0, 2),
            ]);

            for (const release of releases) {
              if (keepTags.has(release.tag_name)) continue;

              const assets = await github.paginate(github.rest.repos.listReleaseAssets, {
                owner,
                repo,
                release_id: release.id,
                per_page: 100,
              });

              for (const asset of assets) {
                await github.rest.repos.deleteReleaseAsset({
                  owner,
                  repo,
                  asset_id: asset.id,
                });
              }
            }
```
