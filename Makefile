SAMPLE := data/sample/movies_5k.csv
RUN_DATE := $(shell date +%F)

.PHONY: demo-tfidf

demo-tfidf:
	python -m movierec.data.ingest --input $(SAMPLE) --dry-run
	python -m movierec.embedders.tfidf --input $(SAMPLE) --run-date $(RUN_DATE)
	python -m movierec.index.neighbours --run-date $(RUN_DATE)
