#!/usr/bin/env python3
"""Projected-database CPPG with an optional maximum pattern length.

This is the controlled CPPG-k baseline. With maxPatternLength=None, it follows
the unrestricted original CPPG search. With an integer k, it uses the same
projected-database operations but stops recursive growth at length k.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

try:
    import psutil
except ImportError:
    psutil = None


Item = str
Transaction = Tuple[Item, ...]
Pattern = Tuple[Item, ...]


class MiningAborted(RuntimeError):
    pass


@dataclass(frozen=True)
class PatternRecord:
    coverage_count: int
    coverage_support: float
    overlap_ratio: float


class CPPGK:
    EPSILON = 1e-12

    def __init__(
        self,
        iFile,
        minRF,
        minCS,
        maxOR,
        sep="\t",
        maxPatternLength: Optional[int] = 5,
        *,
        maxRuntimeSeconds: Optional[float] = None,
        maxCandidateExtensions: Optional[int] = None,
        progressEvery: int = 0,
    ):
        self.iFile = iFile
        self.minRF = float(minRF)
        self.minCS = float(minCS)
        self.maxOR = float(maxOR)
        self.sep = "\t" if sep == r"\t" else sep
        self.maxPatternLength = maxPatternLength
        self.maxRuntimeSeconds = maxRuntimeSeconds
        self.maxCandidateExtensions = maxCandidateExtensions
        self.progressEvery = int(progressEvery)
        self._validate()
        self._reset()

    def mine(self):
        self._reset()
        self.startTime = time.perf_counter()
        self.status = "running"

        try:
            self.database = self._read_database()
            if not self.database:
                raise ValueError("The transaction database is empty.")

            self._prepare_frequent_items()
            self.orderedDatabase = self._order_database()

            n = len(self.database)
            for item in self.fList:
                support = self.support[item]
                cs = support / n
                if cs + self.EPSILON >= self.minCS:
                    self._record((item,), support, 0.0)

                if self.maxPatternLength == 1:
                    continue

                projected = self._construct_projection(
                    self.orderedDatabase,
                    item,
                )
                self._mine_projection(
                    prefix=(item,),
                    coverage_count=support,
                    projected_database=projected,
                )

            self.status = "complete"
        except MiningAborted as exc:
            self.status = "aborted"
            self.abortReason = str(exc)
        finally:
            self.finalPatterns = {
                "\t".join(pattern): [
                    record.coverage_count,
                    record.coverage_support,
                    record.overlap_ratio,
                ]
                for pattern, record in self.records.items()
            }
            self.endTime = time.perf_counter()
            self._capture_memory()

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
            "algorithm": (
                "CPPG-unbounded"
                if self.maxPatternLength is None
                else "CPPG-k"
            ),
            "status": self.status,
            "complete": self.isComplete(),
            "abort_reason": self.abortReason,
            "transactions": len(self.database),
            "frequent_items": len(self.fList),
            "max_pattern_length": self.maxPatternLength,
            "coverage_patterns": len(self.records),
            "projected_databases": self.projectionCount,
            "recursive_calls": self.recursiveCalls,
            "candidate_extensions": self.candidateExtensions,
            "max_depth": self.maxDepth,
            "runtime_seconds": self.getRuntime(),
            "memory_uss_bytes": self.memoryUSS,
            "memory_rss_bytes": self.memoryRSS,
        }

    def _prepare_frequent_items(self):
        n = len(self.database)
        tids: dict[str, set[int]] = {}
        for tid, transaction in enumerate(self.database):
            for item in transaction:
                tids.setdefault(item, set()).add(tid)

        self.tidsets = {
            item: frozenset(item_tids)
            for item, item_tids in tids.items()
            if len(item_tids) / n + self.EPSILON >= self.minRF
        }
        self.support = {
            item: len(item_tids)
            for item, item_tids in self.tidsets.items()
        }
        self.fList = sorted(
            self.support,
            key=lambda item: (-self.support[item], item),
        )
        self.rank = {
            item: index for index, item in enumerate(self.fList)
        }

    def _order_database(self):
        frequent = set(self.fList)
        ordered = []
        for transaction in self.database:
            retained = [item for item in transaction if item in frequent]
            retained.sort(key=self.rank.__getitem__)
            ordered.append(tuple(retained))
        return ordered

    def _construct_projection(
        self,
        database: Sequence[Transaction],
        extension_item: Item,
    ) -> list[Transaction]:
        self.projectionCount += 1
        extension_rank = self.rank[extension_item]
        result = []

        for transaction in database:
            if extension_item in transaction:
                continue
            suffix = tuple(
                item
                for item in transaction
                if self.rank[item] > extension_rank
            )
            if suffix:
                result.append(suffix)
        return result

    def _mine_projection(
        self,
        prefix: Pattern,
        coverage_count: int,
        projected_database: Sequence[Transaction],
    ):
        self.recursiveCalls += 1
        self.maxDepth = max(self.maxDepth, len(prefix))

        if (
            self.maxPatternLength is not None
            and len(prefix) >= self.maxPatternLength
        ):
            return

        last_rank = self.rank[prefix[-1]]
        if last_rank >= len(self.fList) - 1:
            return

        projected_counts: Counter[str] = Counter()
        for transaction in projected_database:
            projected_counts.update(transaction)

        n = len(self.database)
        for candidate_rank in range(last_rank + 1, len(self.fList)):
            self._before_candidate()

            candidate = self.fList[candidate_rank]
            non_overlap_count = projected_counts.get(candidate, 0)
            candidate_support = self.support[candidate]
            overlap_count = candidate_support - non_overlap_count
            overlap_ratio = overlap_count / candidate_support

            if overlap_ratio > self.maxOR + self.EPSILON:
                continue

            new_prefix = prefix + (candidate,)
            new_coverage_count = coverage_count + non_overlap_count
            self.maxDepth = max(self.maxDepth, len(new_prefix))

            if new_coverage_count / n + self.EPSILON >= self.minCS:
                self._record(
                    new_prefix,
                    new_coverage_count,
                    overlap_ratio,
                )

            if (
                self.maxPatternLength is not None
                and len(new_prefix) >= self.maxPatternLength
            ):
                continue

            next_projection = self._construct_projection(
                projected_database,
                candidate,
            )
            self._mine_projection(
                new_prefix,
                new_coverage_count,
                next_projection,
            )

    def _record(
        self,
        pattern: Pattern,
        coverage_count: int,
        overlap_ratio: float,
    ):
        self.records[pattern] = PatternRecord(
            coverage_count=coverage_count,
            coverage_support=coverage_count / len(self.database),
            overlap_ratio=overlap_ratio,
        )

    def _before_candidate(self):
        if (
            self.maxCandidateExtensions is not None
            and self.candidateExtensions
            >= self.maxCandidateExtensions
        ):
            raise MiningAborted(
                f"maxCandidateExtensions="
                f"{self.maxCandidateExtensions} reached"
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
                "[CPPG-k] "
                f"candidates={self.candidateExtensions:,}, "
                f"patterns={len(self.records):,}, "
                f"depth={self.maxDepth}, "
                f"elapsed={now - self.startTime:.1f}s",
                flush=True,
            )

    def _read_database(self):
        path = Path(self.iFile)
        if not path.exists():
            raise FileNotFoundError(path)

        transactions = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\r\n")
                if not line:
                    transactions.append(tuple())
                    continue

                seen = set()
                items = []
                for token in line.split(self.sep):
                    item = token.strip()
                    if item and item not in seen:
                        seen.add(item)
                        items.append(item)
                transactions.append(tuple(items))
        return transactions

    def _capture_memory(self):
        if psutil is None:
            return
        process = psutil.Process(os.getpid())
        self.memoryRSS = float(process.memory_info().rss)
        try:
            self.memoryUSS = float(process.memory_full_info().uss)
        except Exception:
            self.memoryUSS = 0.0

    def _validate(self):
        for name, value in (
            ("minRF", self.minRF),
            ("minCS", self.minCS),
            ("maxOR", self.maxOR),
        ):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0,1]")

        if (
            self.maxPatternLength is not None
            and self.maxPatternLength < 1
        ):
            raise ValueError("maxPatternLength must be positive or None")

    def _reset(self):
        self.database = []
        self.orderedDatabase = []
        self.tidsets = {}
        self.support = {}
        self.fList = []
        self.rank = {}
        self.records = {}
        self.finalPatterns = {}

        self.startTime = 0.0
        self.endTime = 0.0
        self.memoryUSS = 0.0
        self.memoryRSS = 0.0
        self.projectionCount = 0
        self.recursiveCalls = 0
        self.candidateExtensions = 0
        self.maxDepth = 0
        self.status = "not_started"
        self.abortReason = None


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Mine bounded coverage patterns with CPPG-k."
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
        miner = CPPGK(
            iFile=args.input,
            minRF=args.min_rf,
            minCS=args.min_cs,
            maxOR=args.max_or,
            sep="\t",
            maxPatternLength=args.k,
            progressEvery=0,
        )
        miner.mine()
        if not miner.isComplete():
            raise RuntimeError(miner.getAbortReason() or "mining did not complete")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    stats = miner.getStatistics()
    print("Algorithm: CPPG-k")
    print(f"Transactions: {stats['transactions']}")
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
