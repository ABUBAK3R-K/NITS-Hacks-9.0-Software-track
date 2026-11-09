# FAILURES

Bugs, dead ends, and what we learned. One short entry each, newest at the bottom.

- 14:13 `git init -b main` ran on a repo someone had just initialised (branch `master`, remote set). It printed "re-init: ignored --initial-branch". Fixed with `git branch -m master main` before the first commit.
- 14:15 The fixture generator wrote `disamb or [] if abstain else []`, which Python parses as `disamb or ([] if ...)`. Caught on read-through and parenthesised before the first run.
- 14:33 `core.autocrlf` is on for this machine, so a checkout would turn `sse_sample.txt` into CRLF and break the SSE wire format. `.gitattributes` pins fixtures to LF.
