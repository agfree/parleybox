.PHONY: test dev lint
test:
	python3 -m unittest discover -s tests -v
dev:
	python3 -m parleybox --dev
lint:
	python3 -m pyflakes parleybox tests 2>/dev/null || python3 -m py_compile parleybox/*.py tests/*.py
