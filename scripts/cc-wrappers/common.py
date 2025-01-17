import hashlib
import os
import subprocess
import sys

import orjson


def run(build_type: str) -> int:
    build_commands_dir = os.environ.get(
        "BUILD_COMMANDS_DIRECTORY", "/tmp/build_commands"
    )
    if not os.path.exists(build_commands_dir):
        os.mkdir(build_commands_dir)

    command = os.path.basename(sys.argv[0])
    build_info = {
        "type": build_type,
        "directory": os.getcwd(),
        "arguments": [command] + sys.argv[1:],
    }
    build_json = orjson.dumps(build_info, option=orjson.OPT_INDENT_2)

    # Hash the contents of the JSON file and use that as the file name
    # This is safe for concurrency, and guarantees that each unique
    # compilation gets an output file
    hm = hashlib.sha256()
    hm.update(build_json)
    build_file_name = f"{hm.hexdigest()}.json"
    build_file = os.path.join(build_commands_dir, build_file_name)
    with open(build_file, "wb") as f:
        f.write(build_json)

    script_dir = os.path.dirname(os.path.realpath(__file__))
    b_arg = ["-B" + script_dir] if build_type == "cc" else []
    return subprocess.call([command] + b_arg + sys.argv[1:])
