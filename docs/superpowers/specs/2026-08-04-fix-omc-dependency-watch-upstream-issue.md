# Draft upstream issue for GitNexus — file at github.com/chris-husse/GitNexus

Status: DRAFT, not yet filed. Companion to
`2026-08-04-fix-omc-dependency-watch-design.md` (omc's side: heal + park).

---

## Title

Opening a corrupt lbug store segfaults Node instead of raising a clean
error

## Environment

- GitNexus 1.6.8, Node v26.0.0, macOS 15 (Darwin 25.5.0), arm64
- Store: ~45MB `.gitnexus/lbug` produced by
  `gitnexus analyze --index-only` (exit 0) over a medium Rust monorepo

## Symptom

Every command that opens the affected store — `gitnexus wiki` and a plain
`gitnexus query "<q>" --repo <key>` — dies with SIGSEGV (exit 139,
`EXC_BAD_ACCESS at 0x0000000000000018`) before printing anything beyond
the wiki banner. Nothing on stderr. Faulting stack (from the macOS crash
report, all frames in `lbugjs.node`):

```
lbug::storage::DiskArrayInternal::DiskArrayInternal(...) + 152
lbug::storage::HashIndex<lbug::common::string_t>::HashIndex(...) + 456
lbug::storage::PrimaryKeyIndex::initOverflowAndSubIndices(...) + 400
lbug::storage::PrimaryKeyIndex::PrimaryKeyIndex(...) + 756
lbug::storage::PrimaryKeyIndex::load(...) + 152
lbug::storage::IndexHolder::load(...) + 128
lbug::storage::NodeTable::deserialize(...) + 1560
lbug::storage::StorageManager::deserialize(...) + 720
lbug::storage::Checkpointer::readCheckpoint(...) + 200
lbug::storage::WALReplayer::replay(bool, bool) const + 212
lbug::storage::StorageManager::recover(...) + 72
lbug::main::Database::initMembers(...) + 1224
```

Null-ish dereference (offset 0x18) during recovery/checkpoint read — the
store's on-disk state is bad, and recovery crashes rather than failing.

## Why it matters

A background reconciler (oh-my-clanker's dependency watch) drives
GitNexus headlessly. A signal death produces no parseable error, so
callers can't distinguish "corrupt store" from "GitNexus bug" without
reading macOS crash reports. omc now heals (wipe + re-index) and parks on
repeat, but a clean `Error: store is corrupt — re-run analyze` (nonzero
exit, message on stderr) would make every caller's life saner.

## Two asks

1. **Recovery must not crash**: guard the checkpoint/WAL-replay
   deserialization path so a bad store raises a catchable error naming the
   store path and suggesting a re-index.
2. **Root cause of the corruption**: the store was written by an `analyze
   --index-only` run that exited 0 (meta.json written, caches intact,
   `indexedAt` stamped). What sequence lets analyze exit 0 while leaving
   an unopenable checkpoint? (Possibly an interrupted earlier run's WAL
   surviving into a later "successful" one.)

## Repro state available on request

The corrupt 45MB store and the matching `.ips` crash reports are preserved
locally (not attached — internal repo content).
