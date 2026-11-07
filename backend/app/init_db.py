from .db import Base, get_engine
from . import models  # noqa: F401  (registers tables)


def main() -> None:
    Base.metadata.create_all(get_engine())


if __name__ == "__main__":
    main()
