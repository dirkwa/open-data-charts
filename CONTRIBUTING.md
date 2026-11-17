# Contributing

Thanks for helping. Issues and pull requests are welcome, in particular new
open data sources and new regions.

## Before you start

- Read `AGENTS.md`: it holds the layout, the rules that keep units seamless
  and the index contract.
- A new source must allow redistribution of derived charts. Name its
  licence and attribution in `LICENSE-DATA.md`.
- Open an issue first for a new region or source, so the licence and the
  data can be checked before code is written.

## Development

The build runs in the builder image, which brings GDAL, tippecanoe, 7z and
osmium:

```sh
docker build -f builder/Dockerfile -t odc-builder .
docker run --rm -v "$PWD:/src" -w /src odc-builder python3 -m pytest -q
ruff check && ruff format --check
```

## Pull requests

- One logical change per PR, branched from the latest `main`.
- Title and commits in conventional form, `<type>(<scope>): <subject>`,
  for example `feat(sources): read Shom tide stations`.
- Describe why and how, and what you tested. Add tests for new behaviour.
- CodeRabbit reviews every PR; answer each finding by fixing it or saying
  on its thread why it does not apply.

## Not for navigation

The charts are no substitute for official charts. Keep that statement on
every output, and don't present the data as anything else.
