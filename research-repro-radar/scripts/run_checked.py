"""Gate an explicitly supplied radar command before it can collect/rank/write state."""
import argparse
import json
import subprocess
import sys
import llm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--timeout', type=llm.positive_timeout, default=llm.TIMEOUT_SEC)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('supply the radar command after --')
    result = llm.preflight(args.model, args.timeout)
    if result['verdict'] is None:
        print(json.dumps({'status': 'judge_unavailable', **result}), file=sys.stderr)
        return 1
    return subprocess.run(command).returncode


if __name__ == '__main__':
    raise SystemExit(main())
