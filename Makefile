.PHONY: test dev lint
test:
	python3 -m unittest discover -s tests -v
dev:
	python3 -m piratebox --dev
lint:
	python3 -m pyflakes piratebox tests 2>/dev/null || python3 -m py_compile piratebox/*.py tests/*.py
