#!/usr/bin/env python3

import fnmatch
import json
import os
from pprint import pprint
import requests
import subprocess
import yaml

# Required.
CIRCLE_CONTINUATION_KEY = os.environ['CIRCLE_CONTINUATION_KEY']
CIRCLECI_DOMAIN = os.environ['CIRCLECI_DOMAIN']
PROJECT_CONFIG_PATH = os.environ['PROJECT_CONFIG_PATH']
HEADER_APPLICATION_JSON = 'application/json'


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


def load_configs(config_paths):
    configs = {}
    for config_path in config_paths:
        with open(config_path, 'r') as config_file:
            configs[config_path] = yaml.load(config_file, Loader=yaml.SafeLoader)
    return configs


# Allows for a single transitive dependency to be included in the config
def extend_configs_with_sub_projects(configs):
    new_configs = {}
    for config_path, config_yaml in configs.items():
        # Make a deep copy of the path config to allow for modification
        new_config_yaml = config_yaml.copy()
        new_config_yaml['paths'] = config_yaml['paths'].copy()
        for sub_project_path in config_yaml.get('projects', []):
            if sub_project_path not in configs:
                print('WARNING: dependency project {} not found for {}, skipping'.format(sub_project_path, config_path))
                continue
            print('Extending config {} with sub project {}'.format(config_path, sub_project_path))
            sub_config = configs.get(sub_project_path, {})
            sub_paths = sub_config.get('paths', [])
            new_config_yaml['paths'].extend(sub_paths)
            if 'projects' in sub_config:
                print(
                    'WARNING: dependency project {} has its own subprojects: {}, these are not being processed into {}'
                    .format(sub_project_path, sub_config['projects'], config_path)
                )
        new_configs[config_path] = new_config_yaml

    return new_configs

def check_config_match(config_yaml, changes):
    for pattern in config_yaml['paths']:
        if fnmatch.filter(changes, pattern):
            return True
    return False


def merge_config(final_config, config_path, config_yaml):
    print(f'merge {config_path} into final config')
    if 'orbs' in config_yaml:
        final_config['orbs'].update(config_yaml['orbs'])
    if 'commands' in config_yaml:
        final_config['commands'].update(config_yaml['commands'])
    if 'jobs' in config_yaml:
        final_config['jobs'].update(config_yaml['jobs'])
    if 'workflows' in config_yaml:
        final_config['workflows'].update(config_yaml['workflows'])


def send_continuation(config, changes):
    print(f'start workflow as {config}')
    res = requests.post(
        f'https://{CIRCLECI_DOMAIN}/api/v2/pipeline/continue',
        json={
            'continuation-key': CIRCLE_CONTINUATION_KEY,
            'configuration': json.dumps(config),
            'parameters': {
                'change-paths': ','.join(changes)
            }
        },
        headers={
            'Content-Type': HEADER_APPLICATION_JSON,
            'Accept': HEADER_APPLICATION_JSON,
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

    additional_trigger_path = os.environ.get('ADDITIONAL_TRIGGER_PATH', '')
    if additional_trigger_path:
        print('Using additional path {} only'.format(additional_trigger_path))
        changes = [additional_trigger_path]
    else:
        print('Comparing {}...{}'.format(base, head))
        changes = changed_files(base, head)
    pprint(changes)
    config_paths = scan_configs()
    configs = load_configs(config_paths)
    extended_configs = extend_configs_with_sub_projects(configs)
    final_config = {
        'version': 2.1,
        'orbs': {},
        'commands': {},
        'jobs': {},
        'workflows': {
        },
        'parameters': {
            'trigger-path': {
                'type': 'string',
                'default': ''
            },
            'change-paths': {
                'type': 'string',
                'default': ''
            }
        }
    }
    for config_path, config_yaml in extended_configs.items():
        if check_config_match(config_yaml, changes):
            merge_config(final_config, config_path, config_yaml)
    if final_config['workflows']:
        send_continuation(final_config, changes)
    else:
        print('no workflow to be scheduled, skip creating continuation workflow')


create_config(
    os.environ.get('CIRCLE_SHA1'),
    os.environ.get('BASE_REVISION')
)
