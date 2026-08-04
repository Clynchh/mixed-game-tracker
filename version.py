"""Single place the version is defined, so the UI, the database and the
build all agree on it."""

VERSION = "1.0.0"

# Bump when a release changes how hands are PARSED (not when the schema
# changes - new columns are handled automatically). The app compares this
# against the value stored in the database and, if it's moved, offers to
# re-read the hand histories so existing hands pick up the fix. Without
# that, a parsing correction would only ever apply to hands imported after
# the update, and old ones would quietly keep the wrong numbers.
PARSER_VERSION = 1
