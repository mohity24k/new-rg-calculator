"""Dataset extraction pipeline: many reactions per PDF, page filter, text-first requests,
concurrent server calls with structured output, checkpoints and resume.

    pages.py     page filter and work units (text chunks, page images)
    schema.py    JSON schemas, prompts, normalization, general procedures
    engines.py   concurrent OpenAI-compatible client
    pipeline.py  per-paper assembly and the checkpointed corpus runner
    enrich.py    structures, masses, validation flags, adapter to the app's review table
    config.py    loads config/extraction.yaml
"""
