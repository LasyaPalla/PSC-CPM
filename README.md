# PSC-CPM

This repository contains the implementation of **Pruned State-Compressed Coverage Pattern Mining (PSC-CPM)** accompanying the paper:

**“Efficient k-Coverage Pattern Mining for Representative Monitoring in Transportation Networks”**, accepted at **BDA 2026**.

## Repository Contents

- `PSC_CPM.py` — proposed PSC-CPM implementation.
- `CPPG_K.py` — length-bounded CPPG implementation used as the principal comparison.
- `sample_transactions.txt` — fully synthetic dataset for checking execution.

## Requirements

Tested with **Python 3.12.11**.

The command-line mining implementations require only the Python standard
library.

`psutil` is optional and is used only for convenience process-memory getters.
`pandas` is optional and is required only by `getPatternsAsDataFrame()` in
`PSC_CPM.py`.

The peak-RSS values reported in the paper were measured externally using the
Linux high-water value from `/usr/bin/time -v`, not through `psutil`.

## Input Format

Use one transaction per line, with items separated by **tab characters**.

Duplicate transactions are allowed. A blank line represents an empty
transaction, and empty transactions remain part of the transaction denominator.

Example:

```text
R001	R006	R012
R004	R018
R001	R006	R012

R003	R021
```

The included `sample_transactions.txt` file is a larger synthetic dataset provided for software verification.

## How to Run PSC-CPM

```bash
python PSC_CPM.py \
  --input sample_transactions.txt \
  --min-rf 0.10 \
  --min-cs 0.40 \
  --max-or 0.30 \
  --k 5
```

## How to Run CPPG-k

```bash
python CPPG_K.py \
  --input sample_transactions.txt \
  --min-rf 0.10 \
  --min-cs 0.40 \
  --max-or 0.30 \
  --k 5
```

## Parameters

* `--input` — path to the transaction file.
* `--min-rf` — minimum relative frequency required for an item.
* `--min-cs` — minimum fraction of transactions covered by a reported pattern.
* `--max-or` — maximum allowed overlap ratio for each pattern extension.
* `--k` — maximum number of items in a reported pattern.

## Sample Verification

For `sample_transactions.txt` with `minRF=0.10`, `minCS=0.40`,
`maxOR=0.30`, and `k=5`, both implementations return **762 coverage
patterns**. Their pattern keys, coverage counts, coverage supports, and overlap
ratios are identical.

The sample contains 750 transactions, 75 distinct synthetic items, 16 empty
transactions, duplicate transaction states, and varying transaction lengths.

The sample is intended for correctness and execution verification, not for
reproducing the runtime results reported in the paper.

## Paper Configurations

### Fukushima

```text
minRF = 0.04
minCS = 0.40
maxOR = 0.30
k = 5
```

### SUMO

```text
minRF = 0.15
minCS = 0.50
maxOR = 0.30
k = 5
```

## Data Availability

The Fukushima traffic dataset used in the paper is not distributed with this repository because the authors do not have permission to redistribute it.

The included `sample_transactions.txt` file is a fully synthetic dataset created solely for software verification and contains no records derived from the Fukushima or SUMO datasets.

## License

This repository is released under the **MIT License**.
