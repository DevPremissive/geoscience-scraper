"""screens — the theory compiler (C4.3).

`dsl` parses a screen, `compile` turns it into DuckDB SQL over the feature
store, `runner` executes it against a NAMED snapshot and persists the run.

Structured screens first; natural language is a front-end (C4.4), never the
engine. A screen is a hypothesis written down so it can be re-run, diffed and
argued with.
"""
