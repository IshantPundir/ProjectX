"""Provider-agnostic AI layer.

Business logic imports from this package — never from openai/instructor/langfuse
directly. This is the load-bearing abstraction that makes a future model or
provider swap a config change, not a code rewrite."""
