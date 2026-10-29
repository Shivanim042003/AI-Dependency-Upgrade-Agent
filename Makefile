.PHONY: install test test-all image toy eval-dev report serve
install:        ; pip install -e ".[dev]"
test:           ; python -m pytest -m "not integration"
test-all:       ; python -m pytest
image:          ; docker build -t pmigrate-runner docker/
toy:            ; pmigrate init-toy
eval-dev:       ; pmigrate eval dev-1 --split dev --variants noop bump-pydantic agent agent+docs agent+docs+plan --attempts 3 --max-total-cost 25
report:         ; pmigrate report --experiment dev-1
serve:          ; pmigrate serve --mode replay
