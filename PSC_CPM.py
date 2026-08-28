#!/usr/bin/env python3
"""Pruned State-Compressed Coverage Pattern Mining (PSC-CPM).

This module implements the bounded coverage-pattern mining method described in
"Efficient k-Coverage Pattern Mining for Representative Monitoring in
Transportation Networks."

The implementation preserves the deterministic CPPG item order and prefix
non-overlap semantics while replacing recursive projected databases with
multiplicity-preserving vertical integer bitsets.

The main pruning mechanisms are:

1. minimum-relative-frequency filtering,
2. prefix-overlap pruning,
3. an optimistic coverage upper bound, and
4. the maximum pattern-length bound k.

Duplicate transaction states may be grouped while preserving their original
multiplicity. Empty transactions are retained in the denominator by default,
matching the experimental configuration used in the paper.

Pruning effectiveness is threshold- and data-dependent.

Example
-------
    from PSC_CPM import PSCCPM

    miner = PSCCPM(
        iFile="transactions.txt",
        minRF=0.04,
        minCS=0.40,
        maxOR=0.30,
        maxPatternLength=5,
    )
    miner.mine()
    patterns = miner.getPatterns()
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

try:
    import psutil
except ImportError:
    psutil = None


Item = str
Transaction = Tuple[Item, ...]
Pattern = Tuple[Item, ...]


@dataclass(frozen=True)
class PatternRecord:
    coverage_count: int
    coverage_support: float
    overlap_count: int
    overlap_ratio: float


class MiningAborted(RuntimeError):
    """Internal exception for controlled runtime/candidate termination."""


class PSCCPM:
    EPSILON = 1e-12

    def __init__(
        self,
        iFile,
        minRF=0.15,
        minCS=0.60,
        maxOR=0.40,
        sep="\t",
        maxPatternLength=5,
        minGlobalSupport=0.0,
        maxGlobalSupport=1.0,
        deduplicate=True,
        preserveMultiplicity=True,
        dropEmptyTransactions=False,
        requirePrefixNonOverlap=True,
        *,
        maxRuntimeSeconds=None,
        maxCandidateExtensions=None,
        progressEvery=100_000,
        checkpointFile=None,
        checkpointEverySeconds=300.0,
    ):
        self.iFile = iFile
        self.minRF = float(minRF)
        self.minCS = float(minCS)
        self.maxOR = float(maxOR)
        self.sep = "\t" if sep == r"\t" else sep

        self.maxPatternLength = int(maxPatternLength)
        self.minGlobalSupport = float(minGlobalSupport)
        self.maxGlobalSupport = float(maxGlobalSupport)

        self.deduplicate = bool(deduplicate)
        self.preserveMultiplicity = bool(preserveMultiplicity)
        self.dropEmptyTransactions = bool(dropEmptyTransactions)
        self.requirePrefixNonOverlap = bool(requirePrefixNonOverlap)

        self.maxRuntimeSeconds = maxRuntimeSeconds
        self.maxCandidateExtensions = maxCandidateExtensions
        self.progressEvery = max(0, int(progressEvery))
        self.checkpointFile = checkpointFile
        self.checkpointEverySeconds = float(checkpointEverySeconds)

        self._validate_parameters()
        self._reset_run_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def mine(self):
        self._reset_run_state()
        self.startTime = time.perf_counter()
        self._lastCheckpointTime = self.startTime
        self.status = "running"

        try:
            self._preprocess()
            if self.fList:
                self._dfs(
                    prefix=tuple(),
                    startIndex=0,
                    coverageBits=0,
                    coverageCount=0,
                )
            self.status = "complete"
        except MiningAborted as exc:
            self.status = "aborted"
            self.abortReason = str(exc)
        finally:
            self._synchronise_public_patterns()
            self.endTime = time.perf_counter()
            self._capture_memory()

            # Always leave a recoverable file when checkpointing is enabled.
            if self.checkpointFile:
                self.save(self.checkpointFile)

    def startMine(self):
        self.mine()

    def getPatterns(self):
        return dict(self.finalPatterns)

    def save(self, outFile):
        with Path(outFile).open("w", encoding="utf-8") as writer:
            for pattern, record in self.records.items():
                writer.write(
                    f"{'\t'.join(pattern)}:"
                    f"{record.coverage_count}:"
                    f"{record.coverage_support:.12g}:"
                    f"{record.overlap_ratio:.12g}\n"
                )

    def getPatternsAsDataFrame(self):
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "pandas is required for getPatternsAsDataFrame()."
            ) from exc

        return pd.DataFrame(
            [
                {
                    "Patterns": " ".join(pattern),
                    "Length": len(pattern),
                    "CoverageCount": record.coverage_count,
                    "CoverageSupport": record.coverage_support,
                    "OverlapCount": record.overlap_count,
                    "OverlapRatio": record.overlap_ratio,
                }
                for pattern, record in self.records.items()
            ]
        )

    def getRuntime(self):
        return self.endTime - self.startTime

    def getMemoryUSS(self):
        return self.memoryUSS

    def getMemoryRSS(self):
        return self.memoryRSS

    def isComplete(self):
        return self.status == "complete"

    def getAbortReason(self):
        return self.abortReason

    def getFList(self):
        return list(self.fList)

    def getStatistics(self):
        return {
            "algorithm": "PSC-CPM",
            "status": self.status,
            "complete": self.isComplete(),
            "abort_reason": self.abortReason,
            "raw_transactions": self.rawTransactionCount,
            "filtered_transactions": self.filteredTransactionCount,
            "compressed_states": self.stateCount,
            "effective_denominator": getattr(self, "denominator", 0),
            "raw_items": self.rawItemCount,
            "globally_retained_items": self.retainedItemCount,
            "frequent_items": len(self.fList),
            "max_pattern_length": self.maxPatternLength,
            "preserve_multiplicity": self.preserveMultiplicity,
            "prefix_non_overlap_pruning": self.requirePrefixNonOverlap,
            "candidate_extensions": self.candidateExtensions,
            "recursive_calls": self.recursiveCalls,
            "max_depth": self.maxDepth,
            "coverage_patterns": len(self.records),
            "runtime_seconds": self.getRuntime(),
            "memory_uss_bytes": self.memoryUSS,
            "memory_rss_bytes": self.memoryRSS,
        }

    def printResults(self):
        print("\n========== PSC-CPM RESULTS ==========")
        for key, value in self.getStatistics().items():
            print(f"{key}: {value}")

    def getMonitoringScores(self, excludeSingletons=True):
        scores = defaultdict(float)
        for pattern, record in self.records.items():
            if excludeSingletons and len(pattern) == 1:
                continue
            for item in pattern:
                scores[item] += record.coverage_support

        return dict(
            sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        )

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------
    def _preprocess(self):
        rawDatabase = self._read_database()
        self.rawTransactionCount = len(rawDatabase)

        if not rawDatabase:
            raise ValueError("The transaction database is empty.")

        rawSupport = Counter()
        for transaction in rawDatabase:
            rawSupport.update(set(transaction))

        self.rawItemCount = len(rawSupport)
        rawDenominator = len(rawDatabase)

        retainedItems = {
            item
            for item, count in rawSupport.items()
            if self.minGlobalSupport - self.EPSILON
            <= count / rawDenominator
            <= self.maxGlobalSupport + self.EPSILON
        }
        self.retainedItemCount = len(retainedItems)

        filteredDatabase = []
        for transaction in rawDatabase:
            filtered = tuple(
                sorted(item for item in transaction if item in retainedItems)
            )
            if filtered or not self.dropEmptyTransactions:
                filteredDatabase.append(filtered)

        self.filteredTransactionCount = len(filteredDatabase)
        if not filteredDatabase:
            raise ValueError("No transactions remain after item filtering.")

        if self.deduplicate:
            stateCounts = Counter(filteredDatabase)
            self.states = list(stateCounts.keys())
            self.stateWeights = list(stateCounts.values())
        else:
            self.states = list(filteredDatabase)
            self.stateWeights = [1] * len(self.states)

        self.stateCount = len(self.states)
        self.denominator = (
            sum(self.stateWeights)
            if self.preserveMultiplicity
            else len(self.states)
        )

        # Build multiplicity-expanded vertical bitsets.
        #
        # When preserveMultiplicity=True, each unique state with multiplicity w
        # receives a contiguous block of w bits. Therefore, int.bit_count()
        # directly returns the original transaction-level count. This preserves
        # RF, CS, and OR exactly while avoiding a Python loop over every set bit
        # during each candidate evaluation.
        verticalBits = {}
        bitOffset = 0

        for stateId, transaction in enumerate(self.states):
            if self.preserveMultiplicity:
                weight = self.stateWeights[stateId]
                stateBit = ((1 << weight) - 1) << bitOffset
                bitOffset += weight
            else:
                stateBit = 1 << stateId

            for item in transaction:
                verticalBits[item] = verticalBits.get(item, 0) | stateBit

        weightedSupport = {
            item: bits.bit_count()
            for item, bits in verticalBits.items()
        }

        self.support = {
            item: count
            for item, count in weightedSupport.items()
            if count / self.denominator + self.EPSILON >= self.minRF
        }

        self.fList = sorted(
            self.support,
            key=lambda item: (-self.support[item], item),
        )
        self.tidBits = {
            item: verticalBits[item]
            for item in self.fList
        }

        # Suffix unions provide a safe upper bound for coverage pruning.
        # suffixUnion[i] contains all transactions covered by items i..m-1.
        self.suffixUnion = [0] * (len(self.fList) + 1)
        runningUnion = 0
        for index in range(len(self.fList) - 1, -1, -1):
            runningUnion |= self.tidBits[self.fList[index]]
            self.suffixUnion[index] = runningUnion

    def _read_database(self):
        path = Path(self.iFile)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")

        transactions = []
        with path.open("r", encoding="utf-8") as handle:
            for rawLine in handle:
                line = rawLine.rstrip("\r\n")

                if line == "":
                    transactions.append(tuple())
                    continue

                seen = set()
                transaction = []
                for token in line.split(self.sep):
                    item = token.strip()
                    if item and item not in seen:
                        seen.add(item)
                        transaction.append(item)

                transactions.append(tuple(transaction))

        return transactions

    def _weighted_count(self, bits):
        """Return the exact transaction/state count represented by ``bits``.

        Multiplicities are encoded directly in contiguous bit blocks during
        preprocessing, so cardinality is computed in optimized C code by
        ``int.bit_count()``.
        """
        return bits.bit_count()

    # ------------------------------------------------------------------
    # Bounded vertical search
    # ------------------------------------------------------------------
    def _dfs(self, prefix, startIndex, coverageBits, coverageCount):
        self.recursiveCalls += 1
        prefixLength = len(prefix)

        if prefixLength >= self.maxPatternLength:
            return

        for candidateIndex in range(startIndex, len(self.fList)):
            self._before_candidate()

            candidate = self.fList[candidateIndex]
            candidateBits = self.tidBits[candidate]
            candidateSupport = self.support[candidate]

            if prefixLength == 0:
                overlapCount = 0
                overlapRatio = 0.0
                newCoverageBits = candidateBits
                newCoverageCount = candidateSupport
            else:
                overlapBits = coverageBits & candidateBits
                overlapCount = self._weighted_count(overlapBits)
                overlapRatio = overlapCount / candidateSupport

                # Reject an overlap-violating extension. Every descendant
                # retains this violating prefix, so the branch can be safely
                # pruned under the fixed CPPG prefix-overlap semantics.
                if (
                    self.requirePrefixNonOverlap
                    and overlapRatio > self.maxOR + self.EPSILON
                ):
                    continue

                newCoverageBits = coverageBits | candidateBits
                newCoverageCount = (
                    coverageCount + candidateSupport - overlapCount
                )

            newPattern = prefix + (candidate,)
            newLength = prefixLength + 1
            self.maxDepth = max(self.maxDepth, newLength)

            coverageSupport = newCoverageCount / self.denominator

            if (
                coverageSupport + self.EPSILON >= self.minCS
                and overlapRatio <= self.maxOR + self.EPSILON
            ):
                self.records[newPattern] = PatternRecord(
                    coverage_count=newCoverageCount,
                    coverage_support=coverageSupport,
                    overlap_count=overlapCount,
                    overlap_ratio=overlapRatio,
                )

            if newLength < self.maxPatternLength:
                nextIndex = candidateIndex + 1

                # If even the union of every remaining item cannot reach
                # minCS, no descendant can be valid. This is a safe pruning
                # condition because it uses an optimistic coverage upper bound.
                optimisticCoverageBits = (
                    newCoverageBits | self.suffixUnion[nextIndex]
                )
                optimisticCoverage = (
                    optimisticCoverageBits.bit_count() / self.denominator
                )

                if optimisticCoverage + self.EPSILON >= self.minCS:
                    self._dfs(
                        prefix=newPattern,
                        startIndex=nextIndex,
                        coverageBits=newCoverageBits,
                        coverageCount=newCoverageCount,
                    )

    # ------------------------------------------------------------------
    # Progress, checkpointing, and limits
    # ------------------------------------------------------------------
    def _before_candidate(self):
        if (
            self.maxCandidateExtensions is not None
            and self.candidateExtensions >= self.maxCandidateExtensions
        ):
            raise MiningAborted(
                f"maxCandidateExtensions={self.maxCandidateExtensions} reached"
            )

        self.candidateExtensions += 1
        now = time.perf_counter()

        if (
            self.maxRuntimeSeconds is not None
            and now - self.startTime >= self.maxRuntimeSeconds
        ):
            raise MiningAborted(
                f"maxRuntimeSeconds={self.maxRuntimeSeconds} reached"
            )

        if (
            self.progressEvery
            and self.candidateExtensions % self.progressEvery == 0
        ):
            print(
                "[PSC-CPM] "
                f"candidates={self.candidateExtensions:,}, "
                f"patterns={len(self.records):,}, "
                f"depth={self.maxDepth}, "
                f"elapsed={now - self.startTime:.1f}s",
                flush=True,
            )

        if (
            self.checkpointFile
            and now - self._lastCheckpointTime
            >= self.checkpointEverySeconds
        ):
            self._synchronise_public_patterns()
            self.save(self.checkpointFile)
            self._lastCheckpointTime = now

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _synchronise_public_patterns(self):
        self.finalPatterns = {
            "\t".join(pattern): [
                record.coverage_count,
                record.coverage_support,
                record.overlap_ratio,
            ]
            for pattern, record in self.records.items()
        }

    # Convenience end-of-run memory statistics.
    # These values are not the peak-RSS measurements reported in the paper.
    # Paper benchmarks use the OS high-water RSS from `/usr/bin/time -v`.
    def _capture_memory(self):
        if psutil is None:
            return

        process = psutil.Process(os.getpid())
        self.memoryRSS = float(process.memory_info().rss)

        try:
            self.memoryUSS = float(process.memory_full_info().uss)
        except Exception:
            self.memoryUSS = 0.0

    def _validate_parameters(self):
        for name, value in (
            ("minRF", self.minRF),
            ("minCS", self.minCS),
            ("maxOR", self.maxOR),
            ("minGlobalSupport", self.minGlobalSupport),
            ("maxGlobalSupport", self.maxGlobalSupport),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1].")

        if self.minGlobalSupport > self.maxGlobalSupport:
            raise ValueError(
                "minGlobalSupport cannot exceed maxGlobalSupport."
            )

        if self.maxPatternLength < 1:
            raise ValueError("maxPatternLength must be at least 1.")

        if not self.sep:
            raise ValueError("sep must be a non-empty string.")

    def _reset_run_state(self):
        self.states = []
        self.stateWeights = []
        self.tidBits = {}
        self.support = {}
        self.fList = []
        self.suffixUnion = []
        self.records = {}
        self.finalPatterns = {}

        self.startTime = 0.0
        self.endTime = 0.0
        self.memoryUSS = 0.0
        self.memoryRSS = 0.0

        self.rawTransactionCount = 0
        self.filteredTransactionCount = 0
        self.stateCount = 0
        self.rawItemCount = 0
        self.retainedItemCount = 0

        self.candidateExtensions = 0
        self.recursiveCalls = 0
        self.maxDepth = 0

        self.status = "not_started"
        self.abortReason = None
        self._lastCheckpointTime = 0.0


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Mine bounded coverage patterns with PSC-CPM."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="tab-separated transaction file (blank lines are transactions)",
    )
    parser.add_argument("--min-rf", required=True, type=float)
    parser.add_argument("--min-cs", required=True, type=float)
    parser.add_argument("--max-or", required=True, type=float)
    parser.add_argument("--k", required=True, type=int)
    return parser


def main(argv=None):
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    try:
        miner = PSCCPM(
            iFile=args.input,
            minRF=args.min_rf,
            minCS=args.min_cs,
            maxOR=args.max_or,
            sep="\t",
            maxPatternLength=args.k,
            minGlobalSupport=0.0,
            maxGlobalSupport=1.0,
            deduplicate=True,
            preserveMultiplicity=True,
            dropEmptyTransactions=False,
            requirePrefixNonOverlap=True,
            progressEvery=0,
            checkpointFile=None,
        )
        miner.mine()
        if not miner.isComplete():
            raise RuntimeError(miner.getAbortReason() or "mining did not complete")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    stats = miner.getStatistics()
    print("Algorithm: PSC-CPM")
    print(f"Transactions: {stats['raw_transactions']}")
    print(f"Patterns: {stats['coverage_patterns']}")
    print(f"Candidate extensions: {stats['candidate_extensions']}")
    print("Pattern output:")
    for pattern, record in miner.records.items():
        print(
            "PATTERN\t"
            f"items={','.join(pattern)}\t"
            f"coverage_count={record.coverage_count}\t"
            f"coverage_support={record.coverage_support:.12g}\t"
            f"overlap_ratio={record.overlap_ratio:.12g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
