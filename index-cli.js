const program = require('commander');
const fs = require('fs-extra');
const path = require('path');
const klawSync = require('klaw-sync');
const semver = require('semver');
const yaml = require('js-yaml');
const readline = require('readline-promise').default;


function isJsonFile(filename) {
  const pieces = filename.split('.');
  return pieces[pieces.length - 1] == 'json';
}

function isYamlFile(filename) {
  const pieces = filename.split('.');
  const ext = pieces[pieces.length - 1];
  return ext == 'yml' || ext == 'yaml';
}

function isDataFile(filename) {
  const pieces = filename.split('.');
  const ext = pieces[pieces.length - 1];
  return ext == 'json' || ext == 'yml' || ext == 'yaml';
}

function dirExists(path) {
  if (path == undefined) {
    return false;
  }
  if (!fs.pathExistsSync(path)) {
    console.error(path + ' does not exist');
    return false;
  }
  if (!fs.lstatSync(path).isDirectory()) {
    console.error(path + ' is does not a directory');
    return false;
  }

  return true;
}

function fileExists(path) {
  if (path == undefined) {
    return false;
  }
  if (!fs.pathExistsSync(path)) {
    console.error(path + ' does not exist');
    return false;
  }
  if (!fs.lstatSync(path).isFile()) {
    console.error(path + ' is does not a file');
    return false;
  }

  return true;
}

function readFile(file) {
  if (isYamlFile(file)) {
    return new Promise((resolve, reject) => {
      try {
        resolve(yaml.safeLoad(fs.readFileSync(file, 'utf8')))
      } catch (err) {
        reject(err)
      }
    });
  } else {
    return fs.readJson(file);
  }  
}

function addVersion(serviceDef, version) {
  serviceDef.tag = version.tag;
  serviceDef.dependencies = version.dependencies;
  if (version.hasOwnProperty('environment_variables')) {
    serviceDef.environment_variables = [
      ...new Set(serviceDef.environment_variables.concat(version.environment_variables))
    ];
  }
  if (version.hasOwnProperty('labels')) {
    serviceDef.labels = [
      ...new Set(serviceDef.labels.concat(version.labels))
    ];
  }
}

function flattenServiceVersion(serviceDef, versionTag) {
  let sd = JSON.parse(JSON.stringify(serviceDef));
  delete sd.versions;

  addVersion(sd, serviceDef.versions.find(v => {
    return v.tag == versionTag;
  }));

  return sd;
}

function resolveDependencies(requestedServices, seviceDefinitions, resolvedServices) {
  requestedServices.forEach(dependency => {
    let name = dependency.name;
    let reqVersion = dependency.version;

    // check if dependency is already resolved
    let existingVersion = resolvedServices.get(name);
    if (existingVersion && semver.satisfies(existingVersion.tag, reqVersion)) {
      return;
    }

    let serviceDef = seviceDefinitions.get(name);
    if (serviceDef == null) {
      throw new Error('No matching service definition found for ' + name);
    }

    let availableVersions = [];
    serviceDef.versions.forEach(version => {
      availableVersions.push(version.tag);
    });

    let highestVersion = semver.maxSatisfying(availableVersions, reqVersion);
    if (highestVersion === null) {
      throw new Error('No matching version found for ' + name + ' ' + reqVersion + '\nAvailable versions: ' + availableVersions);
    }

    let resolvedVersion = flattenServiceVersion(serviceDef, highestVersion);
    resolvedServices.set(name, resolvedVersion);

    resolveDependencies(resolvedVersion.dependencies, seviceDefinitions, resolvedServices);
  });
}

function substParam(value) {
  if (value.charAt(0) == '<') {
    return value.replace(/[<>]/g, '');
  }

  return value;
}

function setEnvironmentVariables(source, target) {
  source.services.forEach(s => {
    if (s.hasOwnProperty('environment_variables')) {
      for (var prop in s.environment_variables) {
        if (s.environment_variables.hasOwnProperty(prop)) {
          target.get(s.name).environment_variables.get(prop).value = s.environment_variables[prop];
        }
      }
    }
  });
}

function setLabels(source, target) {
  source.services.forEach(s => {
    if (s.hasOwnProperty('labels')) {
      for (var prop in s.labels) {
        if (s.labels.hasOwnProperty(prop)) {
          target.get(s.name).labels.set(prop = substParam(labels[prop]));
        }
      }
    }
  });
}

function setReplicas(source, target) {
  source.services.forEach(s => {
    if (s.hasOwnProperty('replicas')) {
      target.get(s.name)['replicas'] = s.replicas;
    }
  });
}

function exportParams(outputPath, servicesToDeploy) {
  let params = {
    global: {},
    components: {}
  };

  servicesToDeploy.forEach((serviceDef, serviceName, map) => {
    let component = {};
    component['replicas'] = 1;
    component['name'] = serviceName;
    component['image'] = serviceDef.image;
    component['tag'] = serviceDef.tag;
    component['containerPort'] = serviceDef.ports[0];

    serviceDef.environment_variables.forEach((ev, name, map) => {
      if (ev.value == null) {
        throw new Error('Param components.' + serviceName + '.' + name + ' has no value');
      }
      component[name] = ev.value;
    });

    params.components[serviceName] = component;
  });

  fs.writeJson(path.join(outputPath, 'params.libsonnet'), params);
}

function exportGateways(outputPath, servicesToDeploy, env) {
  env.services.forEach(envService => {
    let selector = '';
    if (envService.vamp.gateway.selector.type == 'label') {
      let dplService = servicesToDeploy.get(envService.name);
      dplService['labels'].forEach((value, name, map) => {
        if (envService.vamp.gateway.selector.discriminator == name) {
          selector += "label(" + name + ")((.*)) && ";
        } else {
          let v = value;
          if (value == 'name') {
            v = envService.name;
          } else if (value == 'tag') {
            v = envService.tag;
          } else {
            v = dplService.environment_variables.get(value).value;
          }
          selector += "label(" + name + ")(" + v + ") && ";
        }
      });

      // remove trailing ' && '
      selector = selector.slice(0, -3);
    }

    let data = '';
    data += "name: " + envService.name + "\n";
    data += "port: " + envService.port + "\n";
    data += "selector: " + selector + "\n";

    fs.outputFile(path.join(outputPath, envService.name + "-gw.yml"), data);
  });
}

function writeDeploymentJsonnet(outputPath, template, service) {
  const rlp = readline.createInterface({
    terminal: false,
    input: fs.createReadStream(template)
  });

  let data = '';

  return rlp
    .forEach((line, index) => {
      if (line.includes('@@componentName@@')) {
        data += line.replace('@@componentName@@', service.name);
        data += '\r\n';
      } else if (line.includes('@@labels@@')) {
        let labels = '';
        service.labels.forEach((value, name, map) => {
          labels += '  ' + name + ': params.' + value + '@';
        });
        labels = labels.slice(0, -1);
        labels = labels.replace(/@/g, ',\r\n');
        data += labels;
        data += '\r\n';
      } else if (line.includes('@@withEnv@@')) {
        service.environment_variables.forEach((ev, name, map) => {
          data += '  .withEnv(container.envType.new("' + ev.name + '", params.' + name + '))';
          data += '\r\n';
        });
      } else {
        data += line;
        data += '\r\n';
      }
    })
    .then(() => {
      return fs.outputFile(path.join(outputPath, service.name + '.jsonnet'), data);
    });
}


program
  .version('0.1.0')
  .option('-s, --service-defs <dir>', 'location of the service definitions')
  .option('-a, --application-defs <dir>', 'location of the application definitions')
  .option('-A, --application <file>', 'application definition')
  .option('-e, --environment-defs <dir>', 'location of the application definitions')
  .option('-E, --environment <file>', 'application definition')
  .option('-o, --output <dir>', 'output dir, defaults to the current dir')
  .option('-D, --deployment-template <file>', 'the Jsonnet template used to create deployments')
  .parse(process.argv);

/*
console.log('service-defs dir: ' + program.serviceDefs);
console.log('application-defs dir: ' + program.applicationDefs);
console.log('application file: ' + program.application);
console.log('environment-defs dir: ' + program.environmentDefs);
console.log('environment file: ' + program.environment);
console.log('output dir: ' + program.output);
*/

let serviceDefsDir;
if (dirExists(program.serviceDefs)) {
  serviceDefsDir = program.serviceDefs;
}
if (serviceDefsDir == undefined) {
  console.error("--service-defs <dir> must be specified");
  process.exit(1);
}

let applicationDefFile;
let applicationDefsDir;
if (program.application == undefined) {
  if (dirExists(program.applicationDefs)) {
    applicationDefsDir = program.applicationDefs;
  }
} else if (isDataFile(program.application) && fileExists(program.application)) {
  applicationDefFile = program.application
}
if (applicationDefFile == undefined && applicationDefsDir == undefined) {
  console.error("Either --application-defs <dir> or --application <file> must be specified");
  process.exit(2);
};

let environmentDefFile;
let environmentDefsDir;
if (program.environmentDef == undefined) {
  if (dirExists(program.environmentDefs)) {
    environmentDefsDir = program.environmentDefs;
  }
} else if (isDataFile(program.environment) && fileExists(program.environment)) {
  applicationDefFile = program.environment
}
if (environmentDefFile == undefined && environmentDefsDir == undefined) {
  console.error("Either --environment-defs <dir> or --environment <file> must be specified");
  process.exit(3);
};

let outputDir;
if (dirExists(program.output)) {
  outputDir = program.output;
} else {
  outputDir = '.';
}

const OF_KSONNET = 'ksonnet';
let outputFormat = OF_KSONNET;

let deploymentTemplateFile;
if (program.deploymentTemplate == undefined) {
  deploymentTemplateFile = path.join('.', 'deployment-template.jsonnet');
} else if (fileExists(program.deploymentTemplate)) {
  deploymentTemplateFile = program.deploymentTemplate;
} else {
  console.error("--deployment-template must specify a valid Jsonnet template");
  process.exit(4);
}

console.log('service-defs dir: ' + serviceDefsDir);
console.log('application-defs dir: ' + applicationDefsDir);
console.log('application file: ' + applicationDefFile);
console.log('environment-defs dir: ' + environmentDefsDir);
console.log('environment file: ' + environmentDefFile);
console.log('output dir: ' + outputDir);
console.log('output format: ' + outputFormat);
console.log('deployment template: ' + deploymentTemplateFile);

const klawDataFileFilter = (item) => {
  const basename = path.basename(item.path);
  // ignore hidden files and dirs (starting with .) and non data files (*.json|yaml|yml)
  return basename === '.' || basename[0] !== '.' || (item.stats.isFile() && isDataFile(item.path));
}

// read sevice defs
const serviceDefFiles = klawSync(serviceDefsDir, { nodir: true, depthLimit: 1, filter: klawDataFileFilter });
if (serviceDefFiles.length == 0) {
  console.error("No service definitions found reading: " + serviceDefsDir);
  process.exit(10);
}

let serviceDefs = new Map();
let applicationDef;
let environmentDef;

Promise.all(serviceDefFiles.map(f => {
  return readFile(f.path);
}))
  .then(files => {
    files.forEach(file => {
      serviceDefs.set(file.name, file);
    });

    // read app config
    return readFile(applicationDefFile);
  })
  .then(file => {
    applicationDef = file;

    // read env config
    if (environmentDefFile) {
      return readFile(environmentDefFile);
    }

    const envName = applicationDef.environment.name;

    const klawNamedFileFilter = (item) => {
      // ignore everything except data files (.json|yaml|yml) with the required name
      return (item.stats.isFile() && isDataFile(item.path) && path.basename(item.path).startsWith(envName));
    }
    
    let environmentDefFiles = klawSync(environmentDefsDir,
      { nodir: true, depthLimit: 0, filter: klawDataFileFilter });
    if (environmentDefFiles.length == 0) {
      environmentDefFiles = klawSync(path.join(environmentDefsDir, envName),
        { nodir: true, depthLimit: 0, filter: klawNamedFileFilter });
      if (environmentDefFiles.length == 0) {
        console.error('No environment definition found for ' + envName + ' in ' + environmentDefsDir);
        process.exit(11);
      }
    }
    
    return readFile(environmentDefFiles[0].path);
})
  .then(file => {
    environmentDef = file;

    // check environment name match
    if (environmentDef.environment.name !== applicationDef.environment.name) {
      console.error('No environment definition found for ' + applicationDef.environment.name 
        + ' in ' + environmentDefsDir);
      process.exit(12);
  }

    // process dependencies
    let resolvedServices = new Map();
    resolveDependencies(applicationDef.services, serviceDefs, resolvedServices);

    // convert env vars and labels to maps
    resolvedServices.forEach((serviceDef, name, map) => {
      // process env vars
      let evMap = new Map();
      if (serviceDef.hasOwnProperty('environment_variables')) {
        serviceDef.environment_variables.forEach(ev => {
          evMap.set(
            ev.toLowerCase(),
            {
              name: ev,
              value: null
            }
          );
        });
      }
      serviceDef.environment_variables = evMap;

      // process labels
      let labelMap = new Map();
      if (serviceDef.hasOwnProperty('labels')) {
        serviceDef.labels.forEach(labels => {
          for (var prop in labels) {
            if (labels.hasOwnProperty(prop)) {
              labelMap.set(prop, substParam(labels[prop]));
            }
          }
        });
      }
      serviceDef.labels = labelMap;
    });

    return resolvedServices;
  })
  .then(resolvedServices => {
    // populate values from application def
    setEnvironmentVariables(applicationDef, resolvedServices);

    // populate values from environment dev (overrides)
    setEnvironmentVariables(environmentDef, resolvedServices);
    setLabels(environmentDef, resolvedServices);
    setReplicas(environmentDef, resolvedServices);

    console.log(resolvedServices);

    return resolvedServices;
  })
  .then(resolvedServices => {
    // create gateways
    exportGateways(outputDir, resolvedServices, environmentDef);

    if (outputFormat === OF_KSONNET) {
      // export params
      exportParams(outputDir, resolvedServices);

      // write ksonnet files
      let files = [];
      resolvedServices.forEach((service, name, map) => {
        files.push(writeDeploymentJsonnet(outputDir, deploymentTemplateFile, service));
      });

      return Promise.all(files);
    } else {
      console.error("Unsupported output format: " + outputFormat);
      process.exit(20);
    }
  })

  .catch(err => {
    console.error(err);
  });

