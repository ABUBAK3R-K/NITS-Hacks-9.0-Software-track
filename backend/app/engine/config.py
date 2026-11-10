"""Every engine tunable in one place. Weights and abstain thresholds come from
the contract (app.schemas) so the fixtures, the UI and the engine agree."""
from app.schemas import ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE, ENGINE_WEIGHTS, VICTIM_PENALTY

CONFIG = {
    # ---- scoring (contract)
    "weights": dict(ENGINE_WEIGHTS),
    "victim_penalty": VICTIM_PENALTY,
    "abstain_min_margin": ABSTAIN_MIN_MARGIN,
    "abstain_min_score": ABSTAIN_MIN_SCORE,
    "change_lookback_s": 600.0,        # change event within 10 min before onset
    # ---- metric anomaly detection (EWMA mean + EWMA variance per (service, metric))
    "ewma_alpha": 0.1,                 # effective alpha is max(alpha, 1/(n+1)), so warmup is a plain mean
    "warmup_samples": 5,               # no z-scoring before this many baseline samples
    "z_threshold": 4.0,                # z at or above this is an anomaly candidate
    "z_strong": 20.0,                  # a single scrape this far out (critical level) needs no confirmation
    "hf_interval_s": 1.0,              # a metric sampled this often or faster is high-frequency ...
    "hf_confirm_s": 5.0,               # ... and its anomalies must last 5 s (otherwise 2 samples suffice)
    "z_update_gate": 3.0,              # samples at or above this never update the baseline
    "z_cap": 50.0,                     # magnitude cap for the severity component
    "std_rel_floor": 0.10,             # std >= 10% of |mean| (absorbs +-10% multiplicative noise)
    "std_abs_eps": {"_ms": 1.0, "_pct": 1.0, "rate": 0.01, "ratio": 0.01, "default": 0.01},
    "down_is_bad": ("success", "throughput", "rps", "qps", "availability", "hit_rate"),
    # ---- log / request / alert signals
    "log_burst_count": 2,              # 2+ error logs within log_burst_window_s form a burst
    "log_burst_window_s": 15.0,         # 15 s, not 10: uploaded logs are sparse (Lane A sample has 10.07 s gaps)
    "min_weak_points": 3,              # an episode of only error logs / failed requests needs 3+ points
    "attach_window_s": 5.0,            # a lone error log or failed request counts if a strong signal is this close
    "req_burst_count": 3,              # 3+ failed requests (status >= 500) ...
    "req_burst_window_s": 10.0,        # ... within +-10 s ...
    "req_burst_min_ratio": 0.2,        # ... and at least 20% of that service's requests in the window
    "severity_magnitude": {"info": 3.0, "warn": 8.0, "error": 15.0, "critical": 25.0},
    # ---- silence (a service that stops reporting metrics is down)
    "silence_min_scrapes": 6,
    "silence_factor": 3.0,             # gap > 3 x median scrape interval ...
    "silence_min_s": 6.0,              # ... and > 6 s
    "silence_magnitude": 40.0,
    # ---- grouping / lifecycle
    "episode_gap_s": 60.0,             # a quiet gap this long ends a service's anomaly episode
    "group_window_s": 300.0,           # onsets within 5 min can share an incident
    "resolve_after_s": 60.0,
    "settle_single_s": 3.0,            # hold back a single-service incident until it is this old
    # resolve_after_s: status RESOLVED when no anomalous signal in the last 60 s (vs latest event ts)
    "spreading_window_s": 10.0,        # lifecycle SPREADING: the anomalous set grew in the last 10 s
    "resolved_quiet_s": 15.0,          # lifecycle RESOLVED: every incident service within baseline for 15 s
    "fix_event_name": "fix_applied",   # change events with this name are remediations, never deploys
    # ---- given-time ranking (given_time.py, used only when the failure time is known)
    "given_time_pre_s": 600.0,         # "before" window: 10 min up to the failure time (chosen on RCAEval DEV)
    "given_time_post_s": 300.0,        # "after" window: 5 min from the failure time (chosen on RCAEval DEV)
    "given_time_min_pre": 10,          # a metric needs this many samples before ...
    "given_time_min_post": 5,          # ... and after, or it is not scored
    "given_time_min_ratio": 2.0,       # committed only if the top shift is 2x the second (set before any run)
    # ---- disambiguation
    "scrape_interval_default_s": 2.0,
    "close_onset_intervals": 2.0,      # onsets this many scrape intervals apart are "too close to order"
    # ---- signatures
    "pool_saturated_pct": 95.0,
    "down_error_rate": 0.9,            # error_rate this high means the service is effectively down
    "error_dominant_rate": 0.25,       # error_spike over latency_spike when error_rate peaks at or above this
    "pool_log_patterns": ("remaining connection slots", "too many connections", "connection pool exhausted",
                          "pool exhausted", "no more connections", "connection limit exceeded"),
    "down_log_patterns": ("connection refused", "econnrefused", "unreachable", "no route to host",
                          "is down", "connection reset", "name or service not known"),
}
