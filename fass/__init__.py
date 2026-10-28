"""fass — AI FFmpeg command assistant.

Small compiler with an LLM at the front:
  user request -> NLU adapter -> validated editing plan
                -> deterministic planner/builder -> argv list -> POSIX render
"""

from fass.version import __version__
