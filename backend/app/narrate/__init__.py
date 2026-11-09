"""Narrator: LLM phrasing of engine output, grounded on evidence ids."""
from .grounding import ground  # noqa: F401
from .llm import narrate  # noqa: F401
from .router import make_router  # noqa: F401
