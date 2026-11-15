# graph-node fails to restart on `CLOUD_PROVIDER=local`: `put_opts PutMode::Update not yet implemented`

**HydraDB commit:** `9166374` (2026-11-14) · debug build · Linux x86_64 · `CLOUD_PROVIDER=local`

## What happens

A graph-node that uses the local filesystem object store starts and serves normally the first time.
After a restart against the **same** `LOCAL_PATH`, the first write fails:

```
put_opts PutMode::Update not yet implemented
```

Reads still work. Any `MERGE` or `SET` (and writer-lease renewal) fails until the store directory is wiped.

## Reproduce

```bash
export CLOUD_PROVIDER=local LOCAL_PATH=/tmp/hdb/store GRAPH_DATA_CACHE_DIR=/tmp/hdb/cache \
       GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 GRAPH_NODE_ID=node-0 \
       GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 GRAPH_ALLOW_PLAINTEXT=true
graph-node &            # 1. first start
# 2. write anything:  CREATE (:T {id: 1})
kill %1; graph-node &   # 3. restart on the same LOCAL_PATH
# 4. write again:     MERGE (:T {id: 2})   →  put_opts PutMode::Update not yet implemented
```

## Cause

The writer lease and index store do conditional overwrites with `PutMode::Update(version)`:

- `src/engine/writer_lease.rs:268` and `:619`
- `src/engine/index_store.rs:511`

`object_store::local::LocalFileSystem` does not implement `PutMode::Update` and returns
`NotImplemented`. On first start these paths use `PutMode::Create`, which local supports, so the
bug only shows up when an existing lease or index object is found.

## Suggested fix (any of)

1. When the backend is `LocalFileSystem`, fall back to a lock file plus an atomic rename (write `path.tmp`, then
   `rename`). That is safe for the single-node local mode it is meant for.
2. Or wrap the local store in a small adapter that implements `Update` by comparing the stored `e_tag` / mtime
   under a process mutex.
3. At minimum, return a clear error at start-up ("local store does not support restart; wipe LOCAL_PATH"), not
   on the first write.

## Workaround we used

Wipe `LOCAL_PATH` and the data cache before each start, then reload. Our app (Physarum, a hackathon project)
detects an empty graph and reloads automatically.

Happy to open a PR for option 1 if that direction works for you.
