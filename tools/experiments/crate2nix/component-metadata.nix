# SPDX-License-Identifier: MPL-2.0
{ pkgs, root }:
let
  components = builtins.fromJSON (builtins.readFile ./generated/components.json);
  template = pkgs.writeText "component-metadata-template.json" (
    builtins.toJSON {
      workspace_root = "@COMPONENT_ROOT@";
      packages = map (component: {
        inherit (component) name dependencies;
        id = "path+file://@COMPONENT_ROOT@/${component.relative_path}#${component.name}";
      }) components;
    }
  );
  componentsToml = root + "/Components.toml";
in
pkgs.runCommand "c2n-component-metadata" { } ''
  mkdir -p "$out"
  cp ${componentsToml} "$out/Components.toml"
  sed "s|@COMPONENT_ROOT@|$out|g" ${template} > "$out/metadata.json"
''
