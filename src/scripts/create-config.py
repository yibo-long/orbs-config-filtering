#!/usr/bin/env python3

import fnmatch
import json
import os
import requests
import subprocess
import yaml

# Required.
CIRCLE_CONTINUATION_KEY = os.environ["CIRCLE_CONTINUATION_KEY"]
CIRCLECI_DOMAIN = os.environ["CIRCLECI_DOMAIN"]
PROJECT_CONFIG_PATH = os.environ["PROJECT_CONFIG_PATH"]


def checkout(revision):
    """
  Helper function for checking out a branch

  :param revision: The revision to checkout
  :type revision: str
  """
    subprocess.run(
        ['git', 'checkout', revision],
        check=True
    )


def merge_base(base, head):
    return subprocess.run(
        ['git', 'merge-base', base, head],
        check=True,
        capture_output=True
    ).stdout.decode('utf-8').strip()


def parent_commit():
    return subprocess.run(
        ['git', 'rev-parse', 'HEAD~1'],
        check=True,
        capture_output=True
    ).stdout.decode('utf-8').strip()


def changed_files(base, head):
    return subprocess.run(
        ['git', '-c', 'core.quotepath=false', 'diff', '--name-only', base, head],
        check=True,
        capture_output=True
    ).stdout.decode('utf-8').splitlines()


def scan_configs():
    configs = []
    with os.scandir(PROJECT_CONFIG_PATH) as it:
        for entry in it:
            if not entry.name.startswith('.') and entry.name.endswith('.yml') and entry.is_file():
                configs.append(entry.path)
    return configs


def check_config_match(config_path, changes):
    with open(config_path, 'r') as config_file:
        config_yaml = yaml.load(config_file, Loader=yaml.SafeLoader)
        for pattern in config_yaml['paths']:
            if fnmatch.filter(changes, pattern):
                return True
        return False


def merge_config(final_config, config_path):
    print(f'merge {config_path} into final config')
    with open(config_path, 'r') as config_file:
        config_yaml = yaml.load(config_file, Loader=yaml.SafeLoader)
        if 'orbs' in config_yaml:
            final_config['orbs'].update(config_yaml['orbs'])
        if 'commands' in config_yaml:
            final_config['commands'].update(config_yaml['commands'])
        if 'jobs' in config_yaml:
            final_config['jobs'].update(config_yaml['jobs'])
        if 'workflows' in config_yaml:
            final_config['workflows'].update(config_yaml['workflows'])


def send_continuation_file(config_path):
    print(f'start workflow in {config_path}')
    with open(config_path, 'r') as config_file:
        content = config_file.read()
        res = requests.post(
            f'https://{CIRCLECI_DOMAIN}/api/v2/pipeline/continue',
            json={
                'continuation-key': CIRCLE_CONTINUATION_KEY,
                'configuration': content
            },
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }
        )
        print(res)
        print(res.text)


def send_continuation(config):
    print(f'start workflow as {config}')
    res = requests.post(
        f'https://{CIRCLECI_DOMAIN}/api/v2/pipeline/continue',
        json={
            'continuation-key': CIRCLE_CONTINUATION_KEY,
            'configuration': json.dumps(config)
        },
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
    )
    print(res)
    print(res.text)


def create_config(head, base):
    checkout(base)  # Checkout base revision to make sure it is available for comparison
    checkout(head)  # return to head commit
    base = merge_base(base, head)

    if head == base:
        try:
            # If building on the same branch as BASE_REVISION, we will get the
            # current commit as merge base. In that case try to go back to the
            # first parent, i.e. the last state of this branch before the
            # merge, and use that as the base.
            base = parent_commit()
        except:
            # This can fail if this is the first commit of the repo, so that
            # HEAD~1 actually doesn't resolve. In this case we can compare
            # against this magic SHA below, which is the empty tree. The diff
            # to that is just the first commit as patch.
            base = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

    print('Comparing {}...{}'.format(base, head))
    changes = changed_files(base, head)
    additional_trigger_path = os.environ.get('ADDITIONAL_TRIGGER_PATH', '')
    if additional_trigger_path:
        print('Adding additional path {}'.format(additional_trigger_path))
        changes.append(additional_trigger_path)
    print(changes)
    config_paths = scan_configs()
    final_config = {
        'version': 2.1,
        'orbs': {},
        'commands': {},
        'jobs': {},
        'workflows': {
        },
        'parameters': {}
    }
    for config_path in config_paths:
        if check_config_match(config_path, changes):
            merge_config(final_config, config_path)
    if final_config['workflows']:
        send_continuation(final_config)
    else:
        print('no workflow to be scheduled, skip creating continuation workflow')


create_config(
    os.environ.get('CIRCLE_SHA1'),
    os.environ.get('BASE_REVISION')
)
