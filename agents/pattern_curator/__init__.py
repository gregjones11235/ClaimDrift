"""pattern_curator — offline memory-governance batch job (C3 / D3).

NOT an ADK LlmAgent and NOT in the supervisor main chain. A standalone,
independently-triggered batch job (cron / Cloud Run Job / Elastic Workflow)
that purifies the drift_patterns base rates the §3.2 severity_calibration
depends on. See contracts.md §3.6 and memory_loop_v2_design.md B.1.

Public entry point: pattern_curator.curator.run_curator (and the __main__ CLI).
"""
