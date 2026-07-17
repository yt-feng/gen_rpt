import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import literal

# Override pgvector's cosine_distance method to compile to literal 0.0 for SQLite tests
Vector.Comparator.cosine_distance = lambda self, other: literal(0.0)
