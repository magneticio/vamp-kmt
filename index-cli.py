import argparse
import copy
import glob
import json
import ntpath
from os import listdir
from os.path import isdir, isfile, join

from semver import max_satisfying, satisfies

import yaml

OF_KSONNET = 'ksonnet'
JSON_EXTENSIONS = ['.json']
YAML_EXTENSIONS = ['.yml', '.yaml']
DATA_FILE_EXTENSIONS = JSON_EXTENSIONS + YAML_EXTENSIONS


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
        'service_defs',
        type=directory,
        help='location of the service definitions')

    application_group = parser.add_mutually_exclusive_group(required=True)
    application_group.add_argument(
        '-A', '--application',
        type=data_file,
        help='application definition')
    application_group.add_argument(
        '-a', '--application-defs',
        type=directory,
        help='location of the application definitions')

    environment_group = parser.add_mutually_exclusive_group(required=True)
    environment_group.add_argument(
        '-E', '--environment',
        type=data_file,
        help='application definition')
    environment_group.add_argument(
        '-e', '--environment-defs',
        type=directory,
        help='location of the application definitions')

    parser.add_argument(
        '-o', '--output',
        type=directory,
        default='.',
        help='output directory')

    parser.add_argument(
        '-f', '--output-format',
        default=OF_KSONNET,
        choices=[OF_KSONNET],
        help='output format'
    )

    parser.add_argument(
        '-D', '--deployment-template',
        type=file,
        default='./deployment-template.jsonnet',
        help='the Jsonnet template used to create deployments')

    args = parser.parse_args()

    print('service-defs dir: {}'.format(args.service_defs))
    print('application-defs dir: {}'.format(args.application_defs))
    print('application file: {}'.format(args.application))
    print('environment-defs dir: {}'.format(args.environment_defs))
    print('environment file: {}'.format(args.environment))
    print('output dir: {}'.format(args.output))
    print('output format: {}'.format(args.output_format))
    print('deployment template: {}'.format(args.deployment_template))

    return args


def parse_args2():
    args = Args()
    return args


class Args:
    def __init__(self):
        self.service_defs = '/Users/tymoteuszgach/git/vamp/vamp-kmt-examples/tutorial5/services'
        self.application = '/Users/tymoteuszgach/git/vamp/vamp-kmt-examples/tutorial5/applications/vampio-tutorial5-eu/application.yml'
        self.environment = '/Users/tymoteuszgach/git/vamp/vamp-kmt-examples/tutorial5/environments/vampio-tutorial5-eu/vampio-tutorial5-eu.json'
        self.output = '.'
        self.output_format = OF_KSONNET
        self.deployment_template = 'test.jsonnet'


def read_yaml(yaml_file_path):
    with open(yaml_file_path, 'r') as f:
        return yaml.safe_load(f)


def read_json(json_file_path):
    with open(json_file_path, 'r') as f:
        return json.load(f)


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


def get_service_defs_file_paths(service_defs_dir_path):
    return get_file_paths(service_defs_dir_path, DATA_FILE_EXTENSIONS, recursive=True)


def get_environment_def_file_path(environment_defs_dir_path, environment):
    environment_defs_file_paths = get_file_paths(
        environment_defs_dir_path, DATA_FILE_EXTENSIONS)
    environment_defs_file_paths.extend(get_file_paths(
        '{}/{}'.format(environment_defs_dir_path, environment), DATA_FILE_EXTENSIONS))
    environment_file_path = next(
        filter(
            lambda p: ntpath.basename(
                environment_defs_dir_path).startswith(environment),
            environment_defs_file_paths
        ), None
    )
    if environment_file_path == None:
        raise Exception('No environment definition found for `{}` in `{}`'.format(
            environment, environment_defs_dir_path))
    return environment_file_path


def add_version(service_def, version):
    service_def['tag'] = version['tag']
    service_def['dependencies'] = version['dependencies']
    version_env_variables = version.get('environment_variables', None)
    if version_env_variables != None:
        service_def['environment_variables'] += version_env_variables
    version_labels = version.get('labels', None)
    if version_labels != None:
        service_def['labels'] += version_labels


def get_service_defs(service_def_file_paths):
    service_defs = {}
    for service_def_file_path in service_def_file_paths:
        file_content = read_data_file(service_def_file_path)
        service_defs[file_content['name']] = file_content
    return service_defs


def flatten_service_version(service_def, version_tag):
    sd = copy.deepcopy(service_def)
    sd['versions'] = None
    version = next(
        (v for v in service_def['versions'] if v['tag'] == version_tag), None)
    add_version(sd, version)
    return sd


def subst_param(value):
    if value[0] == '<':
        return value.replace('<', '').replace('>', '')
    return value


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
        available_versions = []
        for version in service_def['versions']:
            available_versions.append(version['tag'])
        highest_version = max_satisfying(available_versions, req_version)
        if highest_version == None:
            raise Exception('No matching version found for {} {}\nAvailable versions: {}'.format(
                name, req_version, available_versions))
        resolved_version = flatten_service_version(
            service_def, highest_version)
        resolved_services[name] = resolved_version
        resolve_dependencies(
            resolved_version['dependencies'], service_defs, resolved_services)


def resolve_services(requested_services, service_defs):
    resolved_services = {}
    resolve_dependencies(requested_services, service_defs, resolved_services)

    for name, service_def in resolved_services.items():
        ev_map = {}
        service_def_env_variables = service_def.get(
            'environment_variables', None)
        if service_def_env_variables != None:
            for env_variable in service_def_env_variables:
                ev_map[env_variable.lower()] = {
                    'name': env_variable, 'value': None}
        service_def['environment_variables'] = ev_map

        label_map = {}
        service_def_labels = service_def.get('labels', None)
        if service_def_labels != None:
            for labels in service_def_labels:
                for prop in labels:
                    label_map[prop] = subst_param(prop)
        service_def['labels'] = label_map
    return resolved_services


def set_environment_variables(source, target):
    for s in source['services']:
        env_variables = s.get('environment_variables', None)
        if env_variables != None:
            for env_name, env_value in env_variables.items():
                target[s['name']]['environment_variables'][env_name] = env_value


def set_labels(source, target):
    for s in source['services']:
        labels = s.get('labels', None)
        if labels != None:
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
                        v = dpl_service['environment_variables'][label_value]
                    selector += 'label({})({}) && '.format(label_name, v)
            selector = selector[:-3]

        data = ''
        data += 'name: {}\n'.format(env_service['name'])
        data += 'port: {}\n'.format(env_service['port'])
        data += 'selector: {}\n'.format(selector)

        with open(join(output_path, env_service['name'] + '-gw.yml'), 'w') as f:
            f.write(data)


def export_params(output_path, services_to_deploy):
    params = {'global': {}, 'components': {}}

    for service_name, service_def in services_to_deploy.items():
        component = {}
        component['replicas'] = 1
        component['name'] = service_name
        component['image'] = service_def.get('image', None)
        component['tag'] = service_def['tag']
        component['containerPort'] = service_def['ports'][0]

        env_variables = service_def.get('environment_variables', {})
        for ev_name, ev_value in env_variables.items():
            if ev_value == None:
                raise Exception('Param components. {}. {} has no value'.format(
                    service_name, ev_name))
            component[ev_name] = ev_value
        params['components'][service_name] = component
    with open(join(output_path, 'params.libsonnet'), 'w') as f:
        f.write(params)


def write_deployment_jsonnet(output_path, template, service):
    data = ''
    for line in [line.rstrip('\n') for line in open(template)]:
        if '@@componentName@@' in line:
            data += line.replace('@@componentName@@', service['name'])
            data += '\r\n'
        elif '@@labels@@' in line:
            labels = ''
            service_labels = service.get('labels', {})
            for service_label_name, service_label_value in service_labels.items():
                labels += '  {}: params.{}@'.format(
                    service_label_name, service_label_value)
            labels = labels.slice[:-1]
            labels = labels.replace('@', '\r\n')
            data += labels
            data += '\r\n'
        elif '@@withEnv@@' in line:
            service_env_variables = service.get('environment_variables', {})
            for env_variable_name, env_variable_value in service_env_variables.items():
                data += '  .withEnv(container.envType.new("{}", params.{}))'.format(
                    env_variable_value['name'], env_variable_name)
                data += '\r\n'
        else:
            data += line
            data += '\r\n'
    with open(join(output_path, service['name'] + '.jsonnet'), 'w') as f:
        f.write(data)


def main():
    args = parse_args2()

    service_def_file_paths = get_service_defs_file_paths(args.service_defs)
    if len(service_def_file_paths) == 0:
        raise Exception(
            'No service definitions found reading: {}'.format(args.service_defs))
    service_defs = get_service_defs(service_def_file_paths)
    application_def = read_data_file(args.application)
    environment_def = read_data_file(args.environment) if args.environment != None else read_data_file(
        get_environment_def_file_path(args.environment_defs)[0])
    if environment_def['environment']['name'] != application_def['environment']['name']:
        raise Exception(
            'There was a mismatch in application definition: `{}` and environment definition: `{}`'.format(
                application_def.environment.name, environment_def.environment.name)
        )
    resolved_services = resolve_services(
        application_def['services'], service_defs)
    set_environment_variables(application_def, resolved_services)
    set_environment_variables(environment_def, resolved_services)
    set_labels(environment_def, resolved_services)
    set_replicas(environment_def, resolved_services)

    export_gateways(args.output, resolved_services, environment_def)

    if args.output_format == OF_KSONNET:
        export_params(args.output, resolved_services)
        for service in resolved_services:
            pass
            # write_deployment_jsonnet(
            # args.output, args.deployment_template, service)
    else:
        raise Exception(
            'Unsupported output format: {}'.format(args.output_format))


if __name__ == '__main__':
    main()
