# PSC-CPM

This repository contains the implementation of Pruned State-Compressed Coverage
Pattern Mining (PSC-CPM) accompanying “Efficient k-Coverage Pattern Mining for
Representative Monitoring in Transportation Networks,” accepted at BDA 2026.

## Repository contents

- `PSC_CPM.py`: proposed PSC-CPM implementation.
- `CPPG_K.py`: length-bounded CPPG implementation used as the principal comparison.
- `sample_transactions.txt`: fully synthetic dataset for checking execution.

## Requirements

Tested with Python 3.12.11. No third-party Python packages are required for the
mining algorithms.

## Input format

Use one transaction per line, with items separated by tabs (tabs are
whitespace). Duplicate transactions are allowed. A blank line represents an
empty transaction, and blank transactions remain part of the transaction
denominator.

For example:

```text
R001	R006	R012
R004	R018
R001	R006	R012

R003	R021
```

The included sample is a substantially larger synthetic dataset, not this tiny
format example.

## How to run PSC-CPM

```bash
python PSC_CPM.py \
  --input sample_transactions.txt \
  --min-rf 0.10 \
  --min-cs 0.40 \
  --max-or 0.30 \
  --k 5
```

## How to run CPPG-k

```bash
python CPPG_K.py \
  --input sample_transactions.txt \
  --min-rf 0.10 \
  --min-cs 0.40 \
  --max-or 0.30 \
  --k 5
```

## Parameters

- `--input`: path to the tab-separated transaction file.
- `--min-rf`: minimum relative frequency required for an item.
- `--min-cs`: minimum fraction of transactions covered by a reported pattern.
- `--max-or`: maximum allowed overlap ratio for each pattern extension.
- `--k`: maximum number of items in a reported pattern.

## Sample verification

For the included synthetic dataset and the example configuration, PSC-CPM and
CPPG-k are expected to return the same bounded pattern set.

## Paper configurations

Fukushima:

```text
minRF = 0.04
minCS = 0.40
maxOR = 0.30
k = 5
```

SUMO:

```text
minRF = 0.15
minCS = 0.50
maxOR = 0.30
k = 5
```

## Data availability

The Fukushima traffic dataset used in the paper is not distributed with this
repository because the authors do not have permission to redistribute it. The
included `sample_transactions.txt` file is a fully synthetic dataset created
solely for software verification and contains no records derived from the
Fukushima or SUMO datasets.

No software license has yet been selected. A license decision is required
before public publication.
