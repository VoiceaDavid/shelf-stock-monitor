# How we work in this repository

## Branches
- `main` always works. Nobody pushes to it directly; changes arrive only through pull requests.
- One branch per backlog item, created from the latest `main`:

| Kind | Name | Example |
|---|---|---|
| User story | `feature/<story-id>-<short-name>` | `feature/US-03-shopify-sync` |
| Technical task | `chore/<task-id>-<short-name>` | `chore/T-01-docker-setup` |
| Bug fix | `fix/<short-name>` | `fix/duplicate-events` |
| Documentation | `docs/<task-id>-<short-name>` | `docs/T-17-test-plan` |

## Commits
Small commits with a clear message in the imperative, with the backlog id:
`US-03: send shelf count to Shopify with inventorySetQuantities`

## Pull requests
1. Push your branch and open a PR into `main` (the template opens automatically).
2. Another team member reviews it. Fix the comments, then they approve.
3. CI must be green. Then the author merges with **Squash and merge** and deletes the branch.
4. Move the Trello card to **Done** only when the PR is merged and the feature works in the integrated app.

## Definition of Done
A backlog item is Done when:
- its acceptance criteria are met and shown working (simulator or real shelf),
- the code is merged into `main` through a reviewed PR,
- CI is green, and new logic has tests,
- no secrets are in the repository.
