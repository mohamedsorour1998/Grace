# Contributing to Grace

Thank you for helping. Grace decides whether a family's benefit renewal is filed without a human,
so a few rules here are stricter than in most projects. Read the "Hard rules" in `CLAUDE.md`
before your first change; each one exists because breaking it costs a family their coverage or
exposes a household.

## Set up

Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

The dashboard in `web/` has its own toolchain, Node 22:

```bash
cd web && npm ci
```

## Run the tests

```bash
.venv/bin/python -m pytest -m "not live"
```

Tests marked `live` call real AWS services. CI runs without credentials and deselects them;
`--strict-markers` makes a misspelt marker fail collection. The trajectory evals in `evals/` run
against real Bedrock (`.venv/bin/python -m pytest evals/`) and are not part of the fast suite.

The dashboard has four gates, and all four must pass. `build` is the one that matters most,
because it is what deploys:

```bash
cd web && npm run typecheck && npm run lint && npm run test && npm run build
```

## How changes are made

- **Write the test first, and watch it fail** before you write the code. A test you never saw
  fail may be unable to fail. Many of the lessons in `docs/lessons/` are about exactly that.
- **Work on a branch and open a pull request.** `main` is protected.
- **Use conventional commits**: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.
- **Keep dependencies minimal.** A new dependency needs a reason, its measured transitive cost
  recorded in `CLAUDE.md`, and an exact pin to a release at least two weeks old.
- **Never add a household identity field**: no name, phone number, or address in a tool's
  returned text, a log, a span, a fixture, or the intake form. A case id is enough. `CLAUDE.md`
  hard rule 9 records how a surname once reached CloudWatch.
- **Never route around the gate.** `grace/authority.py` stays pure (no `strands`, no `boto3`, no
  I/O), and an action tool is gated on the question it actually answers.
- **Run the content check before you push.** Public text describes features generically.
  `scripts/content_check.sh` reads its word list from a file outside the repository; CI runs it
  with the list stored as a repository secret.

## Where things are

`README.md` describes what Grace does and how it is deployed. `docs/README.md` indexes the
runbooks, the evidence from the deployed system, the lessons, and every design and plan.
