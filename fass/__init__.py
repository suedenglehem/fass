"""fass — AI FFmpeg command assistant.

Small compiler with an LLM at the front (plan0.md):
  user request -> NLU adapter -> validated editing plan
                -> deterministic planner/builder -> argv list -> POSIX render
"""

__version__ = "0.1.0"
