"""UnityGuard - read-only health audit for Unity projects.

v0.1 is deliberately read-only: it never writes, renames, moves or deletes
anything inside the audited project. The execution engine (plan/apply with a
write-ahead log and rollback) lands in the paid tier and reuses the same
scanner output.
"""

__version__ = "0.6.0"
