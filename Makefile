# Shortcuts for the commands in README → Run it. Nothing here spends money, applies infrastructure or deletes data.
.PHONY: help test lint check site site-stop image infra-check plan jobs history export

help:            ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t 16

test:            ## unit tests
	uv run python -m pytest -q

lint:            ## pyflakes-level lint, as in CI
	uvx ruff check pipeline tests --select F

check: test lint infra-check  ## everything CI checks except the image
	cd frontend && npx ng build

site:            ## the site on :4200 (bash scripts/stop.sh to stop)
	bash scripts/start.sh

site-stop:       ## stop the site
	bash scripts/stop.sh

image:           ## build the job image as CI and production do (arm64 on this Mac)
	docker build --build-arg GIT_SHA=$$(git rev-parse --short=12 HEAD) -t basednews-job .
	docker run --rm basednews-job list

infra-check:     ## terraform fmt + validate both stacks, and checkov
	terraform fmt -check -recursive infra
	terraform -chdir=infra/bootstrap init -backend=false -input=false >/dev/null && terraform -chdir=infra/bootstrap validate
	terraform -chdir=infra/main init -backend=false -input=false >/dev/null && terraform -chdir=infra/main validate
	uvx checkov -d infra --config-file infra/.checkov.yaml

plan:            ## read-only terraform plan against the account (needs init with the backend, DEPLOY.md step 3)
	AWS_PROFILE=basednews terraform -chdir=infra/main plan

jobs:            ## the job runner's steps and jobs
	uv run python -m pipeline.job list

history:         ## what ran, what failed, why
	uv run python -m pipeline.job history

export:          ## the current fit → frontend/src/data (free)
	uv run python -m pipeline.job export
