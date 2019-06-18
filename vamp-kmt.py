import argparse
import copy
import glob
import json
import ntpath
from os import listdir
from os.path import isdir, isfile, join

from semver import max_satisfying, satisfies

import yaml

OF_KUSTOMIZE = 'kustomize'
JSON_EXTENSIONS = ['.json']
YAML_EXTENSIONS = ['.yml', '.yaml']
DATA_FILE_EXTENSIONS = JSON_EXTENSIONS + YAML_EXTENSIONS

RELEASE_PLAN_NOT_STARTED = 'not started'
RELEASE_PLAN_STARTED = 'started'
RELEASE_PLAN_FINISHED = 'finished'
RELEASE_PLAN_FAILED = 'failed'
RELEASE_PLAN_ABORTED = 'aborted'
RELEASE_PLAN_SKIPPED = 'skipped'
RELEASE_PLAN_PENDING = 'pending'

CAN_RELEASE = [RELEASE_PLAN_NOT_STARTED, RELEASE_PLAN_STARTED, RELEASE_PLAN_FINISHED]
CANNOT_RELEASE = [RELEASE_PLAN_FAILED, RELEASE_PLAN_ABORTED, RELEASE_PLAN_SKIPPED, RELEASE_PLAN_PENDING]

release_plan_lut = {}


def check_extension(file_path, extensions):
    for extension in extensions:
        if file_path.endswith(extension):
            return True
    return False


def is_directory(directory_path):
    return isdir(directory_path)


def is_file(file_path):
    return isfile(file_path)


def is_data_file(file_path):
    return check_extension(file_path, DATA_FILE_EXTENSIONS)


def directory(astring):
    if not is_directory(astring):
        raise argparse.ArgumentTypeError(
            'directory does not exist: `{}`'.format(astring))
    return astring


def file(astring):
    if not is_file(astring):
        raise argparse.ArgumentTypeError(
            'file does not exist: `{}`'.format(astring))
    return astring


def data_file(astring):
    file(astring)
    if not is_data_file(astring):
        raise argparse.ArgumentTypeError(
            'data file must be either in json or yaml format: `{}`'.format(astring))
    return astring


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-s', '--service-defs',
        type=directory,
        help='location of the service definitions')

    parser.add_argument(
        '-A', '--application',
        type=data_file,
        help='application definition')

    parser.add_argument(
        '-E', '--environment',
        type=data_file,
        help='environment definition')

    parser.add_argument(
        '-r', '--release-plans',
        type=directory,
        help='location of the release plans')

    parser.add_argument(
        '-o', '--output',
        type=directory,
        default='.',
        help='output directory')

    parser.add_argument(
        '-f', '--output-format',
        default=OF_KUSTOMIZE,
        choices=[OF_KUSTOMIZE],
        help='output format'
    )

    args = parser.parse_args()

    return args


def read_yaml(yaml_file_path):
    with open(yaml_file_path, 'r') as f:
        return yaml.safe_load(f)


def write_yaml(yaml_file_path, data):
    with open(yaml_file_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)


def read_json(json_file_path):
    with open(json_file_path, 'r') as f:
        return json.load(f)


def write_json(json_file_path, data):
    with open(json_file_path, 'w') as f:
        json.dump(data, f)


def read_data_file(file_path):
    if check_extension(file_path, YAML_EXTENSIONS):
        return read_yaml(file_path)
    else:
        return read_json(file_path)


def get_file_paths(dir_path, extensions, recursive=False):
    paths = []
    for extension in extensions:
        file_wildcard = '*{}'.format(extension)
        if recursive:
            paths.extend(
                glob.glob(
                    '{}/**/{}'.format(dir_path, file_wildcard),
                    recursive=True
                )
            )
        else:
            paths.extend(
                glob.glob('{}/{}'.format(dir_path, file_wildcard))
            )
    return paths


def build_release_plan_lut(environment_name, release_plan_file_path):
    global release_plan_lut
    for release_plan_file_path in get_file_paths(release_plan_file_path, JSON_EXTENSIONS, recursive=True):
        print ("Reading: " + release_plan_file_path)
        release_plan = read_data_file(release_plan_file_path)       
        service = release_plan_lut.get(release_plan['service']['name'])
        if service == None:
            service = {}
            release_plan_lut[release_plan['service']['name']] = service
        if release_plan['status'] in [RELEASE_PLAN_NOT_STARTED, RELEASE_PLAN_STARTED]:
            # there is an active release plan for this version, so find the status for this environment
            for group in release_plan['releaseGroups']:
                for environment in group['environments']:
                    if environment['name'] == environment_name:
                        if (not group['canStart'] and environment['status'] == RELEASE_PLAN_NOT_STARTED):
                            service[release_plan['service']['version']] = RELEASE_PLAN_PENDING
                        else:
                            service[release_plan['service']['version']] = environment['status']
        else:
            # release plan is in an aborted/failed state
            service[release_plan['service']['version']] = release_plan['status']
    print(release_plan_lut)


def get_service_defs_file_paths(service_defs_dir_path):
    return get_file_paths(service_defs_dir_path, JSON_EXTENSIONS, recursive=True)


def get_service_defs(service_def_file_paths):
    service_defs = {}
    for service_def_file_path in service_def_file_paths:
        print ("Reading: " + service_def_file_path)
        file_content = read_data_file(service_def_file_path)
        service_defs[file_content['name']] = file_content
    return service_defs


def add_version(service_def, version):
    service_def['tag'] = version['tag']
    service_def['dependencies'] = version['dependencies']
    version_env_variables = version.get('environment_variables', None)
    if version_env_variables != None:
        service_def['environment_variables'] += version_env_variables
    version_labels = version.get('labels', None)
    if version_labels != None:
        service_def['labels'] += version_labels


def flatten_service_version(service_def, version_tag):
    sd = copy.deepcopy(service_def)
    sd['versions'] = None
    version = next(
        (v for v in service_def['versions'] if v['tag'] == version_tag), None)
    add_version(sd, version)
    return sd


def filter_version(name, version, filtered_versions):
    try:
        status = release_plan_lut[name][version]
        if status in CAN_RELEASE:
            filtered_versions.append(version)
        else:
            print('*** {}: version {} has a release plan in {} state'.format(name, version, status))
    except KeyError:
        print('{}: no release plan for version {}'.format(name, version))
        filtered_versions.append(version)


def resolve_dependencies(requested_services, service_defs, resolved_services):
    for dependency in requested_services:
        name = dependency['name']
        req_version = dependency['version']
        existing_version = resolved_services.get(name, None)
        if existing_version != None and satisfies(existing_version['tag'], req_version):
            continue
        service_def = service_defs.get(name, None)
        if service_def == None:
            raise Exception('No matching service definition found for ' + name)
        
        # get the hightest 'listed' version
        available_versions = []
        for version in service_def['versions']:
            available_versions.append(version['tag'])
        highest_version = max_satisfying(available_versions, req_version)

        # check to see if it can be used
        status = ''
        usable_versions = []
        if highest_version:
            status = release_plan_lut[name][highest_version]
            if status in CANNOT_RELEASE:
                print('{}: version {} has a release plan in {} state'.format(name, version['tag'], status))
                for version in service_def['versions']:
                    filter_version(name, version['tag'], usable_versions)
                highest_version = max_satisfying(usable_versions, req_version)

        if highest_version == None:
            resolved_services.clear()
            if status in CANNOT_RELEASE:
                print('{}: no usable version found for {}'.format(name, req_version))
                print('A matching version was found but release plan is in {} state'.format(status))
                print('Available versions: {}'.format(available_versions))
                print('Usable versions: {}'.format(usable_versions))
            else:
                print('{}: no matching version found for {}'.format(name, req_version))
                print('Available versions: {}'.format(available_versions))
        else:
            print('{}: resolved {} to version {} from {}'.format(
                name, req_version, highest_version, available_versions))
            resolved_version = flatten_service_version(service_def, highest_version)
            resolved_services[name] = resolved_version
            resolve_dependencies(resolved_version['dependencies'], service_defs, resolved_services)


def subst_param(value):
    if value[0] == '<':
        return value.replace('<', '').replace('>', '')
    return value


def resolve_services(requested_services, service_defs):
    resolved_services = {}
    resolve_dependencies(requested_services, service_defs, resolved_services)
    for name, service_def in resolved_services.items():
        ev_map = {}
        service_def_env_variables = service_def.get('environment_variables', [])
        for env_variable in service_def_env_variables:
            ev_map[env_variable.lower()] = {
                'name': env_variable, 'value': None}
        service_def['environment_variables'] = ev_map

        label_map = {}
        service_def_labels = service_def.get('labels', [])
        for labels in service_def_labels:
            for label_name, label_value in labels.items():
                label_map[label_name] = subst_param(label_value)
        service_def['labels'] = label_map
    return resolved_services


def set_environment_variables(source, target):
    for s in source['services']:
        env_variables = s.get('environment_variables', {})
        for env_name, env_value in env_variables.items():
            try:
                target[s['name']]['environment_variables'][env_name]['value'] = env_value
            except KeyError:
                # this can happen when downgrading
                print('{}: service does not have environment variable for "{}"'.format(s['name'], env_name))


def set_labels(source, target):
    for s in source['services']:
        labels = s.get('labels', {})
        for label_name, label_value in labels.items():
            target[s['name']]['labels'][label_name] = label_value


def set_replicas(source, target):
    for s in source['services']:
        replicas = s.get('replicas', None)
        if replicas != None:
            target[s['name']]['replicas'] = replicas


def export_gateways(output_path, services_to_deploy, env):
    for env_service in env['services']:
        selector = ''
        if env_service['vamp']['gateway']['selector']['type'] == 'label':
            dpl_service = services_to_deploy[env_service['name']]
            for label_name, label_value in dpl_service['labels'].items():
                if env_service['vamp']['gateway']['selector']['discriminator'] == label_name:
                    selector += 'label({})((.*)) && '.format(label_name)
                else:
                    v = label_value
                    if label_value == 'name':
                        v = env_service['name']
                    elif label_value == 'tag':
                        v = env_service['tag']
                    else:
                        v = dpl_service['environment_variables'][label_value]['value']
                    selector += 'label({})({}) && '.format(label_name, v)
            # remove trailing ' && '
            selector = selector[:-3]

        data = ''
        data += 'name: {}\n'.format(env_service['name'])
        data += 'kind: gateways\n'
        data += 'deployed: false\n'
        if 'port' in env_service:
            data += 'port: {}\n'.format(env_service['port'])
        data += 'selector: {}\n'.format(selector)
        if 'policy' in env_service['vamp']['gateway']:
            data += 'metadata:\n  release.vamp.io/policy: {}\n'.format(env_service['vamp']['gateway']['policy'])

        with open(join(output_path, env_service['name'] + '.yaml'), 'w') as f:
            f.write(data)


def write_deployment_kustomize(output_path, service_def):
    data = ''
    env_variables = service_def.get('environment_variables', {})
    for ev_name, ev in env_variables.items():
        ev_value = ev.get('value', None)
        if ev_value == None:
            raise Exception('{}. {} has no value'.format(service_def['name'], ev_name))
        data += '{}={}\r\n'.format(ev['name'], ev_value)

    if data != '':
        with open(join(output_path, service_def['name'], 'configMap.env'), 'w') as f:
            f.write(data)

def update_release_group(release_plan, group):
    # check if group is 'finished'
    group_finished = True
    for environment in group['environments']:
        if not environment['status'] in [RELEASE_PLAN_FINISHED, RELEASE_PLAN_SKIPPED]:
            group_finished = False
            break
    if group_finished:
        group['status'] = RELEASE_PLAN_FINISHED
        group['canStart'] = False

        # update next group and overall plan
        all_groups_finished = True
        for g in release_plan['releaseGroups']:
            if g['group'] == group['group'] + 1 and not g['canStart'] and g['status'] == RELEASE_PLAN_NOT_STARTED:
                g['canStart'] = True
                break
            if g['status'] != RELEASE_PLAN_FINISHED:
                all_groups_finished = False
                break
        if all_groups_finished:
            release_plan['status'] = RELEASE_PLAN_FINISHED


def update_release_plan(environment_name, service_name, version, status, release_plan_file_path):
    file_path = join(release_plan_file_path, '{}-{}.json'.format(service_name, version))
    release_plan = read_json(file_path)

    if status == RELEASE_PLAN_STARTED:
        if release_plan['status'] == RELEASE_PLAN_NOT_STARTED:
            release_plan['status'] = RELEASE_PLAN_STARTED

    for group in release_plan['releaseGroups']:
        for environment in group['environments']:
            if environment['name'] == environment_name:
                environment['status'] = status
                if status == RELEASE_PLAN_STARTED and group['status'] == RELEASE_PLAN_NOT_STARTED:
                    # mark group as started
                    group['status'] = RELEASE_PLAN_STARTED
                elif status in [RELEASE_PLAN_FAILED, RELEASE_PLAN_ABORTED]:
                    # mark group as failed/aborted
                    group['status'] = status
                    group['canStart'] = False
                    if status == RELEASE_PLAN_FAILED:
                        release_plan['status'] = RELEASE_PLAN_FAILED
                elif status == RELEASE_PLAN_FINISHED:
                    update_release_group(release_plan, group)
                break
        else:
            continue
        break

    print('Updating release plan: {}'.format(file_path))
    write_json(file_path, release_plan)

def main():
    args = parse_args()

    print ("Reading: " + args.application)
    application_def = read_data_file(args.application)

    print ("Reading: " + args.environment)
    environment_def = read_data_file(args.environment)
    
    if environment_def['environment']['name'] != application_def['environment']['name']:
        raise Exception(
            'There was a mismatch in application definition: `{}` and environment definition: `{}`'.format(
                application_def['environment']['name'], environment_def['environment']['name'])
        )

    environment_name = environment_def['environment']['name']

    build_release_plan_lut(environment_name, args.release_plans)

    service_def_file_paths = get_service_defs_file_paths(args.service_defs)
    if len(service_def_file_paths) == 0:
        raise Exception(
            'No service definitions found reading: {}'.format(args.service_defs))
    service_defs = get_service_defs(service_def_file_paths)

    resolved_services = resolve_services(application_def['services'], service_defs)
    if not resolved_services:
        # dependency resolution failed, so do nothing
        print ('Quiting, service resolution did not produce a result')
        exit()
    
    set_environment_variables(application_def, resolved_services)
    set_environment_variables(environment_def, resolved_services)
    set_labels(environment_def, resolved_services)
    set_replicas(environment_def, resolved_services)

    export_gateways(join(args.output, 'infrastructure', 'vamp', 'config', 'gateways'), 
        resolved_services, environment_def)

    if args.output_format == OF_KUSTOMIZE:
        for _, service in resolved_services.items():
            write_deployment_kustomize(join(args.output, 'services'), service)
    else:
        raise Exception(
            'Unsupported output format: {}'.format(args.output_format))

    computed_services = []
    for _, service in resolved_services.items():
        entry = {}
        entry['name'] = service['name']
        entry['version'] = service['tag']
        computed_services.append(entry)

        # update the release plans
        if release_plan_lut[entry['name']][entry['version']] == RELEASE_PLAN_NOT_STARTED:
            update_release_plan(environment_name, entry['name'], entry['version'], 
                RELEASE_PLAN_STARTED, args.release_plans)

    environment_def['computed-services'] = computed_services
    environment_def['updated'] = True
    
    write_yaml(args.environment, environment_def)


if __name__ == '__main__':
    main()
