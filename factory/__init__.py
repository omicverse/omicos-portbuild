"""omicos-portbuild factory package.

`__version__` is stamped into every manifest.yaml, partition.json, and
iterate _state file at creation. Loading state from a different factory
version emits a warning (not an error) so old work-in-progress targets
can still finish, but the user knows there might be semantic drift in
prompt_contracts / patcher ops / rubric weights.
"""
__version__ = "0.2.0"
