#!/usr/bin/env python3
"""Upload the built artifacts to GitHub releases.

Usage:
    python scripts/upload_build.py

The script:
1. Determines a new version tag based on the latest tag (patch bump).
2. Creates the tag and pushes it.
3. Creates a GitHub release with the tag and uploads all files in the build directory.

It requires the GitHub CLI (`gh`) to be installed and authenticated.
"""
import subprocess
import os
import sys
import re

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()

def get_latest_tag():
    tags = run(['git', 'tag', '--list', 'v*']).split('\n')
    tags = [t for t in tags if re.match(r'^v\d+\.\d+\.\d+$', t)]
    if not tags:
        return None
    # sort by version
    def ver_key(v):
        return tuple(map(int, v.lstrip('v').split('.')))
    return sorted(tags, key=ver_key)[-1]

def bump_version(latest):
    if not latest:
        return 'v0.1.0'
    major, minor, patch = map(int, latest.lstrip('v').split('.'))
    patch += 1
    return f'v{major}.{minor}.{patch}'

def main():
    os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    latest = get_latest_tag()
    new_tag = bump_version(latest)
    print(f"Creating new tag: {new_tag}")
    run(['git', 'tag', new_tag])
    run(['git', 'push', 'origin', new_tag])
    # Create release
    build_dir = os.path.join('build', '发票助手')
    if not os.path.isdir(build_dir):
        print(f"Build directory {build_dir} not found", file=sys.stderr)
        sys.exit(1)
    # Gather files to upload
    files = [os.path.join(build_dir, f) for f in os.listdir(build_dir) if os.path.isfile(os.path.join(build_dir, f))]
    upload_cmd = ['gh', 'release', 'create', new_tag, '-t', new_tag, '-n', f'Automated release {new_tag}'] + files
    print('Running:', ' '.join(upload_cmd))
    run(upload_cmd)
    print('Release created and artifacts uploaded.')

if __name__ == '__main__':
    main()
