# FAILURES

Bugs, dead ends, and what we learned. One short entry each, newest at the bottom.

- 14:13 `git init -b main` ran on a repo someone had just initialised (branch `master`, remote set). It printed "re-init: ignored --initial-branch". Fixed with `git branch -m master main` before the first commit.
- 14:15 The fixture generator wrote `disamb or [] if abstain else []`, which Python parses as `disamb or ([] if ...)`. Caught on read-through and parenthesised before the first run.
- 14:33 `core.autocrlf` is on for this machine, so a checkout would turn `sse_sample.txt` into CRLF and break the SSE wire format. `.gitattributes` pins fixtures to LF.
- 14:24 [Lane A] Bash heredocs containing both quote styles failed in this shell ("unexpected EOF while looking for matching quote"). Switched to the Write tool for Python files.
- 14:33 [Lane A] Two store tests were wrong, not the code: the SSE throttle fills its 20 slots with non-info events once alerts run out, and the 15 min window is measured from the newest event (08:49:59.7), not from a round minute.
- 14:36 [Lane A] Live smoke test showed "analyze failed: NotImplementedError" logged every second while Lane B is not merged. Now logged once per distinct error.
- 14:30 [Lane B] First run on the hero fixture labelled every victim latency_spike: latency z-scores cap at 50 while error_rate z is about 30, so "biggest z wins" always picked latency. Fixed with an explicit errors-dominate rule (error_rate >= 0.25).
- 14:33 [Lane B] Synthetic ambiguous scenario opened as postgres (0.6) on 2 of 8 seeds: when postgres lock waits crossed the threshold before the gateway, postgres gained 0.1 precedence and the margin reached 0.167. The scenario is defined as "postgres crosses last", so the builder now places it more than one scrape after the gateway. Lesson: the abstain case is fragile to scrape phase, which is exactly what the disambiguation list says.
- 14:36 [Lane B] 20k-event timing swung from 70 ms to 550 ms between runs because other lanes were using the CPU. Added a cached minute-prefix fast path to timestamp parsing and a single preprocessing pass. Typical run is now 70 to 250 ms; the test asserts < 1 s.
- 14:40 [Lane B] Incremental replay of Lane A's red_herring showed a one-second ghost "api-gateway: latency_spike" incident before the merge into the postgres incident (the gateway alert lands before intermediate services are confirmed). Fixed with the 3 s settle rule for single-service incidents.
- 14:44 [Lane B] Concurrent faults that share api-gateway (redis_down + bad_deploy, psp_slow + redis_down) merge into one incident, because the gateway is anomalous and adjacent to both sides. This follows the induced-subgraph grouping rule; the root is still one of the two real causes, but the second cause shows only as a high candidate. Not fixed (demo resets between faults).
- 14:44 [Lane B] If a fault stays active longer than the store window (15 min), its pre-fault baseline is evicted, onsets slide with the window, and the incident id changes every recompute. Engine cannot fix this without history; the store should keep the window above fault duration or the demo should reset.
