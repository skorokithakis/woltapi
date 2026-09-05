# Releasing woltapi

This follows the release setup in `skorokithakis/catt`, using `main` instead of
`master` and setuptools instead of Poetry.

## How it works

1. A push or pull request runs tests on Python 3.10 through 3.14. It also checks
   formatting and builds the package. Tests use fake responses, not Wolt accounts.
2. On `main`, Release Please opens or updates a release pull request. It changes
   the version in `pyproject.toml` and writes the changelog for you.
3. Review the release PR and wait for its checks to pass, then merge it.
4. Release Please creates the tag and GitHub release, then starts **Publish to
   PyPI** with that tag.
5. The publishing workflow tests and builds the tagged code. Only after those
   checks pass does a separate job upload it to PyPI.

Use commit messages such as `fix: correct menu prices` and `feat: add search
filters`. Release Please uses these messages to choose the next version.
Like catt, breaking changes before version 1.0 bump the minor version rather
than jumping to 1.0. The starting release is `v0.0.1`.

## One-time GitHub setup

Add a repository Actions secret named **`RELEASE_PLEASE_TOKEN`**. You can use the
same personal access token as catt if it also has access to this repository.
GitHub cannot reveal an existing secret's value, so it cannot be copied from
catt automatically.

For a fine-grained token, allow access to `skorokithakis/woltapi` with read/write
permissions for **Contents** (code) and **Pull requests**, matching catt's token.
GitHub adds read access to Metadata automatically. No separate Issues or Actions
permission is needed on this personal token. Do not commit the token.

You can add it through GitHub's **Settings > Secrets and variables > Actions**,
or run:

```bash
gh secret set RELEASE_PLEASE_TOKEN --repo skorokithakis/woltapi
```

This token matters: release PRs made with the built-in `GITHUB_TOKEN` would not
automatically trigger the test workflow. A personal access token allows those
checks to run, as in catt.

Starting the publishing workflow uses the separate, built-in `GITHUB_TOKEN`,
with `actions: write` granted in the workflow file. Explicit `workflow_dispatch`
events can trigger another workflow with this token. Its permissions do not
change the permissions of your personal access token.

## One-time PyPI setup

No PyPI API token is needed. PyPI must be told to trust this GitHub workflow.

In your PyPI account, add a **trusted publisher** for `woltapi`. If you have not
created the project yet, use **Publishing > Add a new pending publisher**.
If it already exists, use the project's **Publishing** page. You must own the
project, or choose an available package name before publishing.

Enter these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `woltapi` |
| GitHub owner | `skorokithakis` |
| Repository | `woltapi` |
| Workflow filename | `publish-pypi.yml` |
| Environment | Leave blank, matching catt |

The job asks PyPI for a short-lived publishing credential using GitHub's
identity. Building and testing run in a different job without that permission.

## Publish the existing 0.0.1 release

After these workflow files are on `main` and PyPI trusts the publisher, run:

```bash
gh workflow run publish-pypi.yml \
  --repo skorokithakis/woltapi \
  --ref main \
  -f tag=v0.0.1
```

This also lets you retry a failed upload for a later release. Do not change or
move a tag after publishing: PyPI will not let you replace an uploaded version.
The workflow deliberately fails on duplicate files instead of silently skipping
them. Check PyPI before retrying an upload that may have partly succeeded.

Automatic publication is not ready until both the GitHub secret and PyPI
publisher are configured. These workflows do not add login or refresh support
to the Wolt client.
