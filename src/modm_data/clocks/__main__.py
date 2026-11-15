# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

# The pool workers find the functions in the export module, not in __main__
from .export import main

if __name__ == "__main__":
    main()
