LATEXMK ?= latexmk
LATEXFLAGS := -pdf -interaction=nonstopmode -halt-on-error -file-line-error
BUILD_DIR := build
ROOT_DIR := ..

QUICKSTART_SOURCE := quickstart_guide.tex
DEVELOPER_SOURCE := developer_manual.tex
QUICKSTART_PDF := $(ROOT_DIR)/BDX_Slow_Control_Quickstart.pdf
DEVELOPER_PDF := $(ROOT_DIR)/BDX_Slow_Control_Developer_Manual.pdf

.PHONY: all quickstart developer clean

all: quickstart developer

quickstart: $(QUICKSTART_PDF)

developer: $(DEVELOPER_PDF)

$(QUICKSTART_PDF): $(QUICKSTART_SOURCE) common/preamble.tex
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) $(LATEXFLAGS) -outdir=$(BUILD_DIR) $(QUICKSTART_SOURCE)
	cp $(BUILD_DIR)/quickstart_guide.pdf $@

DEVELOPER_PARTS := $(wildcard developer/*.tex)

$(DEVELOPER_PDF): $(DEVELOPER_SOURCE) common/preamble.tex $(DEVELOPER_PARTS)
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) $(LATEXFLAGS) -outdir=$(BUILD_DIR) $(DEVELOPER_SOURCE)
	cp $(BUILD_DIR)/developer_manual.pdf $@

clean:
	$(LATEXMK) -C -outdir=$(BUILD_DIR) $(QUICKSTART_SOURCE) $(DEVELOPER_SOURCE) || true
	rm -rf $(BUILD_DIR)
