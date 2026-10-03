.PHONY: setup test lint demo demo-sim demo-sim-view demo-sim-record bench

setup:
	$(MAKE) -C runtime setup

test:
	$(MAKE) -C runtime test

lint:
	$(MAKE) -C runtime lint

demo demo-sim demo-sim-view demo-sim-record bench:
	$(MAKE) -C runtime $@
