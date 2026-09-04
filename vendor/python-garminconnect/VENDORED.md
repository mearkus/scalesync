# Vendored: python-garminconnect (widget + curl_cffi login fork)

This directory is a vendored copy of a third-party fork of
[`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect).

| | |
|---|---|
| Upstream project | `cyberjunky/python-garminconnect` |
| Fork vendored here | `diegoscarabelli/python-garminconnect` |
| Branch | `feat/widget-cffi-login-strategy` |
| Commit | `c1f8a9a38291387178ea10c4416fe3c72137297c` |
| Commit subject | "feat: add 30-45s random delay before portal credential POST" |
| Package version | 0.3.1 |
| License | MIT (see `LICENSE`) |

## Why it is vendored

`requirements.txt` used to install this fork straight from its git URL:

    garminconnect @ git+https://github.com/diegoscarabelli/python-garminconnect@feat/widget-cffi-login-strategy

On 2026-09-03 that repository stopped being publicly reachable (deleted or made
private), and every scheduled sync began failing in `pip install` with:

    fatal: could not read Username for 'https://github.com': No such device or address

The fork is required rather than upstream because `sync.py` depends on its SSO
embed widget login strategy, which omits the `clientId` parameter and so avoids
Garmin's per-account login rate limiting. Vendoring the source removes the
external single point of failure entirely.

## What was copied

Only what is needed to build and install the package: the `garminconnect/`
package source, `pyproject.toml`, `README.md`, and `LICENSE`. The fork's tests,
VCR cassettes, docs, examples and CI config were left out — the upstream build
already excluded them from the distributed wheel.

The Python sources are byte-for-byte identical to the fork commit above and
carry no scalesync-specific modifications. Keeping it that way makes it obvious
what is third-party code; any local change should be recorded here.

## Recovering or updating this copy

The fork's repository is gone, but its objects survive in the upstream fork
network on GitHub and can still be fetched by commit SHA:

    git init recovered && cd recovered
    git remote add up https://github.com/cyberjunky/python-garminconnect
    git fetch --depth 1 up c1f8a9a38291387178ea10c4416fe3c72137297c
    git checkout FETCH_HEAD
