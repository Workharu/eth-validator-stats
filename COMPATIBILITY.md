# Beacon node compatibility

`eth-validator-stats` talks to your consensus client over the **standard [Ethereum Beacon API](https://ethereum.github.io/beacon-APIs/)**. Every endpoint we use is part of the spec — there is no client-specific code path. In practice this means the same binary works with Prysm, Lighthouse, Teku, Nimbus, and Lodestar.

## Default ports

| Client    | Default Beacon API URL          | Flag to expose / re-bind                                                   |
|-----------|---------------------------------|----------------------------------------------------------------------------|
| Prysm     | `http://localhost:3500`         | `--http-host 0.0.0.0 --http-port 3500`                                     |
| Lighthouse| `http://localhost:5052`         | `--http --http-address 0.0.0.0 --http-port 5052`                           |
| Teku      | `http://localhost:5051`         | `--rest-api-enabled --rest-api-interface=0.0.0.0 --rest-api-port=5051`     |
| Nimbus    | `http://localhost:5052`         | `--rest --rest-address=0.0.0.0 --rest-port=5052`                           |
| Lodestar  | `http://localhost:9596`         | `--rest --rest.address=0.0.0.0 --rest.port=9596`                           |

Set `beacon_node_url` in `config.toml` (or `BEACON_NODE_URL` env var) accordingly.

## Endpoints used

| Endpoint | Why we use it | Status in spec | Notes |
|---|---|---|---|
| `GET /eth/v1/node/version` | client name + version (for `info` command) | stable | All major clients |
| `GET /eth/v1/beacon/genesis` | genesis time | stable | All clients |
| `GET /eth/v1/config/spec` | `SECONDS_PER_SLOT`, `SLOTS_PER_EPOCH` | stable | All clients; we tolerate UPPER/lower keys |
| `GET /eth/v1/beacon/headers/head` | current slot | stable | All clients |
| `POST /eth/v1/beacon/states/head/validators` | bulk status + balance | stable | All clients. POST form (vs GET `?id=`) avoids the 64-id cap |
| `POST /eth/v1/validator/liveness/{epoch}` | last-N attestation hit/miss | added later in spec history | If a client returns 404/405, we degrade gracefully — `last 5 atts` stays blank but everything else works |

## Auth

If your node sits behind a reverse proxy or hosted RPC service that requires a Bearer token, set either:

- `beacon_auth_token = "..."` in `config.toml`, or
- `BEACON_NODE_AUTH_TOKEN=...` env var (wins over config).

Hosted providers known to need this: Infura, Alchemy, QuickNode, BloXroute. Self-hosted Prysm/Lighthouse/etc. usually don't need it on the loopback interface.

## Diagnosing a new node

```bash
eth-validator-stats info
```

Prints client name/version and probes every endpoint we depend on, marking each `OK` / `UNSUPPORTED` / `ERROR`. This is the first thing to run against a node you haven't used with this tool before. Exit code 0 means every probe returned `OK`.

## Update process

When a Beacon API spec revision lands or a major client release ships:

1. Run `eth-validator-stats info` against the latest stable build of each client (Prysm, Lighthouse, Teku, Nimbus, Lodestar).
2. If any probe returns `UNSUPPORTED` or `ERROR` on a previously-working endpoint, file an issue and add a code-path fallback in `src/eth_validator_stats/beacon.py` (this is the only file that touches the API).
3. Update the "Tested against" table below.
4. Tag a release.

## Tested against

| Client     | Versions verified | Network    | Last checked |
|------------|-------------------|------------|--------------|
| Prysm      | v6.x              | mainnet    | 2026-05-18   |
| Lighthouse | _untested_        | _–_        | _–_          |
| Teku       | _untested_        | _–_        | _–_          |
| Nimbus     | _untested_        | _–_        | _–_          |
| Lodestar   | _untested_        | _–_        | _–_          |

Help wanted: PRs adding rows to this table after running `eth-validator-stats info` against the corresponding client.

## Known limitations

- **Liveness vs. on-chain inclusion.** The liveness endpoint reports whether the node *saw* the validator over gossip in a given epoch, which is close to "did it attest" but not identical to "the attestation was included on-chain with correct head/target". The latter requires `POST /eth/v1/beacon/rewards/attestations/{epoch}` against a finalized epoch — planned as a v2 enhancement.
- **Liveness epoch retention.** Per spec, nodes only guarantee liveness data for the current and previous epoch. We always query `current_epoch - 1` and accumulate results in a local ring buffer; the `last 5 atts` glyphs therefore reflect "the last 5 epochs *this CLI has observed*", not the last 5 epochs of chain history. Run regularly via cron and the buffer stays current.
- **Deprecated `proposer/{epoch}` endpoint** — not currently used in v1 (proposer tracking is on the v2 backlog).
