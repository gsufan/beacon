"""Edition of this build.

"desarrollo": the source build, which also works without a Hub (standalone mode, study mode, evaluations).
"comercial": the compiled build that customers receive; it only works while registered with Beacon Hub and holding a
valid lease. The commercial build script rewrites this value before compiling.
"""

EDITION = "desarrollo"


def is_commercial() -> bool:
    return EDITION == "comercial"
