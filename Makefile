.PHONY: bootstrap install-dev test lint pv-list displays run-psu run-prototype run-all phoebus run-ui clean

bootstrap:
	./scripts/bootstrap.sh

install-dev:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests

pv-list:
	bdx-pv-list --config-dir config

displays:
	bdx-generate-displays --config-dir config --output-dir phoebus/displays

run-psu:
	bdx-psu-ioc --config config/psu.json

run-prototype:
	bdx-prototype-ioc --config-dir config

run-all:
	./scripts/run_all_simulated.sh

phoebus:
	./scripts/launch_phoebus.sh

run-ui:
	./scripts/run_prototype_with_phoebus.sh

clean:
	rm -rf build dist .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
