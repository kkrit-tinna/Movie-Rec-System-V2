SAMPLE := data/sample/movies_5k.csv
RUN_DATE := $(shell date +%F)
IMAGE := movierec-batch:dev

.PHONY: demo-tfidf image push-image

demo-tfidf:
	python -m movierec.data.ingest --input $(SAMPLE) --dry-run
	python -m movierec.embedders.tfidf --input $(SAMPLE) --run-date $(RUN_DATE)
	python -m movierec.index.neighbours --run-date $(RUN_DATE)

# Native arm64 batch image (Fargate runs ARM64). No provenance/SBOM
# attestations: they turn each push into an index + image + attestation, three
# ECR entries, and ECR's keep-last-5 rule could expire a tagged index's child.
image:
	docker buildx build --provenance=false --sbom=false -t $(IMAGE) --load .

# Usage: make push-image TAG=t4.3-2026-10-07 (must match var.batch_image_tag).
push-image:
	@test -n "$(TAG)" || { echo "TAG is required, e.g. make push-image TAG=t4.3-2026-10-07"; exit 1; }
	$(eval REPO := $(shell terraform -chdir=infra output -raw batch_repository_url))
	aws ecr get-login-password | docker login --username AWS --password-stdin $(firstword $(subst /, ,$(REPO)))
	docker tag $(IMAGE) $(REPO):$(TAG)
	docker push $(REPO):$(TAG)
