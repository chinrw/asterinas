# SPDX-License-Identifier: MPL-2.0
root:
let
  entries = builtins.genericClosure {
    startSet = [
      {
        key = root.drvPath;
        value = root;
      }
    ];
    operator =
      entry:
      map (dep: {
        key = dep.drvPath;
        value = dep;
      }) (entry.value.dependencies ++ entry.value.buildDependencies);
  };
in
map (
  entry:
  let
    d = entry.value;
  in
  {
    name = d.crateName;
    inherit (d) version drvPath;
    role = d.c2nRole or "host";
    features = d.crateFeatures;
    source = toString d.src;
    outputs = map (name: toString d.${name}) d.outputs;
    dependencies = map (dep: dep.drvPath) d.dependencies;
    buildDependencies = map (dep: dep.drvPath) d.buildDependencies;
  }
) entries
