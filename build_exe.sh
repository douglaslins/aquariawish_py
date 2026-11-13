#!/bin/sh
# The same build for a test on Linux or macOS (the program itself is made for Windows): ./build_exe.sh
# The build tools go in .venv-build (inside this folder): the Python of the system is not changed.
set -e
cd "$(dirname "$0")"
DIST="dist/AquariaWish - Pulls Tracker"
# The build replaces the whole "$DIST" folder (PyInstaller --noconfirm), and the pull database lives in its data folder.
if [ -e "$DIST/data/pulls.sqlite" ]; then
    echo "$DIST/data/pulls.sqlite exists: the build would delete it. Move the folder $DIST/data somewhere else, then run this again." >&2
    exit 3
fi
[ -x .venv-build/bin/python ] || python3 -m venv .venv-build
PY=.venv-build/bin/python
"$PY" -m pip install -r requirements.txt -r requirements-build.txt
"$PY" -m PyInstaller --noconfirm --clean pulls_tracker.spec
cp dist_README.txt "$DIST/README.txt"
cp LICENSE "$DIST/LICENSE.txt"
cp THIRD_PARTY_NOTICES.md "$DIST/THIRD_PARTY_NOTICES.md"
"$DIST/AquariaWish" --selfcheck
# The folder is made to be shared: no selfcheck.txt of this computer (it lists local paths) and no other data. The program creates "data" at its first start.
rm -rf "$DIST/data"
