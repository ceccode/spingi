RUNTIME_TARGETS = setup test lint schemas bench \
	demo demo-sim demo-sim-go2 demo-sim-view demo-sim-record demo-yard demo-track

.PHONY: $(RUNTIME_TARGETS)

# Every target is the runtime's: `make demo-yard` here is `make demo-yard` in runtime/.
$(RUNTIME_TARGETS):
	$(MAKE) -C runtime $@
